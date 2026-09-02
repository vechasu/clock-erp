#!/usr/bin/env python3
"""Import exactly one Bitrix product card and all its images into ERP."""

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase, DEFAULT_CATALOG_DATABASE_PATH  # noqa: E402
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyClient  # noqa: E402
from app.services.bitrix_catalog_importer import BitrixCatalogImporter  # noqa: E402
from app.services.bitrix_erp_product_sync import (  # noqa: E402
    BitrixERPProductSync,
    create_database_backup,
)
from app.services.product_images import (  # noqa: E402
    MAX_PRODUCT_IMAGE_BYTES,
    ProductImageStore,
    utc_now,
)
from app.services.product_reconciliation import normalize_text  # noqa: E402


class AmbiguousBitrixProductError(RuntimeError):
    def __init__(self, candidates):
        super().__init__("Multiple exact Bitrix products found")
        self.candidates = candidates


def _text(value):
    return str(value or "").strip()


def _candidate(product):
    sale_price = product.get("sale_price") or {}
    return {
        "id": _text(product.get("external_product_id")),
        "code": _text(product.get("code")),
        "name": _text(product.get("name")),
        "section_id": _text((product.get("category") or {}).get("id")),
        "article": _text(product.get("external_sku")),
        "brand": _text(product.get("brand")),
        "category": _text((product.get("category") or {}).get("name")),
        "price": sale_price.get("value"),
        "currency": _text(sale_price.get("currency")),
        "stock": product.get("stock"),
        "image_ids": [_text(image.get("id")) for image in product.get("images") or []],
    }


def find_exact_product(client, code, name, page_size=200):
    """Find by exact code first, then exact name, and fail closed."""
    code_matches = []
    name_matches = []
    page = 1
    scanned = 0
    while True:
        payload = client.get_products_page(
            page=page, limit=page_size, include_inactive=True
        )
        products = payload.get("products") or []
        scanned += len(products)
        for product in products:
            if _text(product.get("code")) == code:
                code_matches.append(product)
            if _text(product.get("name")) == name:
                name_matches.append(product)
        if not payload.get("has_more") or not products:
            break
        page += 1
    matches = code_matches or name_matches
    if len(matches) > 1:
        raise AmbiguousBitrixProductError([_candidate(item) for item in matches])
    if not matches:
        raise LookupError("Exact Bitrix product was not found")
    return matches[0], scanned


def _erp_candidates(database, product):
    if not database.exists():
        return [], "not_found"
    external_id = _text(product.get("external_product_id"))
    xml_id = _text(product.get("external_xml_id")).casefold()
    article = _text(product.get("external_sku")).casefold()
    code = _text(product.get("code"))
    normalized_name = normalize_text(product.get("name"))
    with database.connect() as connection:
        identity_lookups = (
            ("bitrix_id", "bitrix_external_product_id = ?", (external_id,)),
            (
                "external_id", "lower(trim(COALESCE(bitrix_xml_id, ''))) = ?",
                (xml_id,),
            ),
        )
        for method, where, values in identity_lookups:
            if not values[0]:
                continue
            rows = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE active = 1 AND "
                + where + " ORDER BY id",
                values,
            ).fetchall()
            if rows:
                return [dict(row) for row in rows], method
        if code:
            rows = connection.execute(
                "SELECT card.* FROM catalog_excel_products card "
                "JOIN catalog_products source "
                "ON source.id = card.bitrix_catalog_product_id "
                "WHERE card.active = 1 AND source.slug = ? ORDER BY card.id",
                (code,),
            ).fetchall()
            if rows:
                return [dict(row) for row in rows], "symbolic_code"
        fallback_lookups = (
            ("normalized_name", "normalized_name = ?", (normalized_name,)),
            (
                "article", "lower(trim(COALESCE(excel_article, ''))) = ?",
                (article,),
            ),
        )
        for method, where, values in fallback_lookups:
            if not values[0]:
                continue
            rows = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE active = 1 AND "
                + where + " ORDER BY id",
                values,
            ).fetchall()
            if rows:
                return [dict(row) for row in rows], method
    return [], "not_found"


def _before_state(database, product):
    candidates, method = _erp_candidates(database, product)
    if not database.exists():
        return {
            "products": 0, "matching_products": 0, "candidate_ids": [],
            "match_method": method, "stock": None, "photos": 0,
        }
    with database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_products WHERE active = 1"
        ).fetchone()[0]
    try:
        photos = len(json.loads(candidates[0].get("bitrix_gallery_json") or "[]")) \
            if len(candidates) == 1 else 0
    except (TypeError, ValueError):
        photos = 0
    return {
        "products": total,
        "matching_products": len(candidates),
        "candidate_ids": [row["id"] for row in candidates],
        "match_method": method,
        "stock": candidates[0].get("stock") if len(candidates) == 1 else None,
        "photos": photos,
    }


def _existing_local_gallery(database, erp_product_id, store):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT bitrix_gallery_json FROM catalog_excel_products WHERE id = ?",
            (int(erp_product_id),),
        ).fetchone()
    try:
        gallery = json.loads(row[0] or "[]") if row else []
    except (TypeError, ValueError):
        gallery = []
    result = {}
    for image in gallery if isinstance(gallery, list) else []:
        if not isinstance(image, dict):
            continue
        external_id = _text(image.get("external_file_id") or image.get("id"))
        local_path = Path(_text(image.get("local_image_path"))).name
        if external_id and local_path and (store.root / local_path).is_file():
            result[external_id] = image
    return result


def import_local_images(database, store, erp_product_id, product, downloader,
                        apply=False, existing=None):
    images = list(product.get("images") or [])
    report = {
        "source_count": len(images), "stored": 0, "existing": 0,
        "duplicates": 0, "errors": [], "gallery": [],
        "database_updated": False,
    }
    if not apply:
        return report
    existing = (
        _existing_local_gallery(database, erp_product_id, store)
        if existing is None else existing
    )
    seen_digests = set()
    gallery = []
    primary_prepared = None
    for index, image in enumerate(images):
        external_id = _text(image.get("id"))
        source_url = _text(image.get("original_url") or image.get("url"))
        saved = existing.get(external_id)
        if saved and _text(saved.get("source_url")) == source_url:
            digest = _text(saved.get("sha256"))
            if digest and digest in seen_digests:
                report["duplicates"] += 1
                continue
            if digest:
                seen_digests.add(digest)
            saved = dict(saved)
            saved.update({"order": len(gallery), "is_primary": not gallery})
            gallery.append(saved)
            report["existing"] += 1
            continue
        try:
            content, mime_type, filename = downloader(image)
            prepared = store.prepare_image(content, filename, mime_type)
            digest = prepared["sha256"]
            if digest in seen_digests:
                report["duplicates"] += 1
                continue
            seen_digests.add(digest)
            local_url = "/product-images/{}?v={}".format(
                prepared["path"], digest[:16]
            )
            gallery.append({
                "external_file_id": external_id,
                "id": external_id,
                "kind": _text(image.get("kind")) or "gallery",
                "is_primary": not gallery,
                "order": len(gallery),
                "original_url": local_url,
                "local_image_path": prepared["path"],
                "sha256": digest,
                "source_url": source_url,
            })
            primary_prepared = primary_prepared or prepared
            report["stored"] += 1
        except Exception as error:
            report["errors"].append({
                "external_file_id": external_id,
                "error": type(error).__name__,
            })
    if gallery:
        first = gallery[0]
        changed_at = (primary_prepared or {}).get("updated_at") or utc_now()
        gallery_json = json.dumps(gallery, ensure_ascii=False, sort_keys=True)
        with database.connect() as connection:
            current = connection.execute(
                "SELECT local_image_path, local_image_source, local_image_sha256, "
                "local_image_external_id, bitrix_primary_image_url, "
                "bitrix_thumbnail_url, bitrix_gallery_json "
                "FROM catalog_excel_products WHERE id = ?",
                (int(erp_product_id),),
            ).fetchone()
        desired = (
            first["local_image_path"], "bitrix", first["sha256"],
            "{}:{}".format(product["external_product_id"], first["external_file_id"]),
            first["original_url"], first["original_url"], gallery_json,
        )
        current_values = tuple(current) if current is not None else ()
        if current_values != desired:
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE catalog_excel_products SET local_image_path = ?, "
                    "local_image_source = 'bitrix', local_image_sha256 = ?, "
                    "local_image_external_id = ?, local_image_updated_at = ?, "
                    "bitrix_primary_image_url = ?, bitrix_thumbnail_url = ?, "
                    "bitrix_gallery_json = ?, updated_at = ? WHERE id = ?",
                    (
                        first["local_image_path"], first["sha256"],
                        "{}:{}".format(
                            product["external_product_id"],
                            first["external_file_id"],
                        ),
                        changed_at,
                        first["original_url"], first["original_url"],
                        gallery_json,
                        changed_at,
                        int(erp_product_id),
                    ),
                )
            report["database_updated"] = True
    report["gallery"] = gallery
    return report


def import_single_product(client, database, code="nato-97", name="Nato 97",
                          apply=False, backup_root=None, image_store=None):
    product, scanned = find_exact_product(client, code, name)
    before = _before_state(database, product)
    if before["matching_products"] > 1:
        return {
            "status": "ambiguous_erp", "bitrix": _candidate(product),
            "before": before, "writes_performed": 0,
        }
    confirmed = (
        {_text(product.get("external_product_id")): before["candidate_ids"][0]}
        if before["matching_products"] == 1 else {}
    )
    card_sync = BitrixERPProductSync(database, confirmed_mappings=confirmed)
    card_preview = card_sync.preview_products([product])[0]
    report = {
        "status": "dry_run", "bitrix": _candidate(product),
        "source_rows_scanned": scanned, "properties": product.get("properties") or [],
        "before": before, "preview": card_preview, "backup_path": None,
        "writes_performed": 0, "stock_source": product.get("stock_source_field"),
        "bitrix_stock": product.get("stock"),
    }
    if not apply:
        return report
    existing_gallery = {}
    if before["matching_products"] == 1:
        existing_gallery = _existing_local_gallery(
            database, before["candidate_ids"][0],
            image_store or ProductImageStore(database),
        )
    report["backup_path"] = str(create_database_backup(database, backup_root) or "")
    source_result = BitrixCatalogImporter(database).import_products(
        [product], "full_sync"
    )
    card_result = card_sync.apply_products([product])[0]
    report["source_result"] = source_result
    report["card_result"] = card_result
    if card_result["status"] in {"ambiguous", "skipped", "error"}:
        report["status"] = card_result["status"]
        return report
    store = image_store or ProductImageStore(database)
    image_report = import_local_images(
        database, store, card_result["erp_product_id"], product,
        lambda image: client.download_brand_image(
            image, max_bytes=MAX_PRODUCT_IMAGE_BYTES
        ),
        apply=True, existing=existing_gallery,
    )
    report["images"] = image_report
    after = _before_state(database, product)
    report["after"] = after
    report["writes_performed"] = (
        int(source_result.get("created") or 0)
        + int(source_result.get("updated") or 0)
        + (1 if card_result["status"] in {"created", "updated"} else 0)
        + image_report["stored"]
    )
    report["status"] = "completed_with_errors" if image_report["errors"] else "success"
    if after["matching_products"] != 1:
        report["status"] = "verification_failed"
    return report


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default="nato-97")
    parser.add_argument("--name", default="Nato 97")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database", default=str(DEFAULT_CATALOG_DATABASE_PATH))
    parser.add_argument("--product-image-root")
    parser.add_argument("--backup-dir")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    args = parser.parse_args()
    if args.apply and not args.backup_dir:
        parser.error("--backup-dir is required with --apply")
    load_dotenv(args.env_file)
    database = CatalogDatabase(args.database)
    store = ProductImageStore(database, args.product_image_root)
    client = BitrixCatalogReadOnlyClient(
        os.getenv("BITRIX_CATALOG_URL"), os.getenv("BITRIX_CATALOG_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )
    try:
        report = import_single_product(
            client, database, code=args.code, name=args.name, apply=args.apply,
            backup_root=args.backup_dir, image_store=store,
        )
    except AmbiguousBitrixProductError as error:
        report = {"status": "ambiguous_bitrix", "candidates": error.candidates}
    except Exception as error:
        report = {"status": "error", "error": type(error).__name__}
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] in {"dry_run", "success"} else 1


if __name__ == "__main__":
    sys.exit(main())
