#!/usr/bin/env python3
"""Audit and restore one exact Bitrix brand without touching other brands."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyClient  # noqa: E402
from app.services.brand_values import normalize_brand  # noqa: E402
from app.services.bitrix_catalog_importer import BitrixCatalogImporter  # noqa: E402
from app.services.bitrix_erp_product_sync import BitrixERPProductSync  # noqa: E402
from app.services.bitrix_stock_sync import BitrixStockSync  # noqa: E402
from app.services.product_images import ProductImageImporter, ProductImageStore  # noqa: E402
from scripts.import_catalog_images import backup_runtime  # noqa: E402


def _text(value):
    return str(value or "").strip()


def exact_brand(value, requested):
    normalized = normalize_brand(value)
    return bool(normalized and normalized == normalize_brand(requested))


def load_brand_products(client, brand, page_size=200):
    products = []
    source_total = 0
    page = 1
    while True:
        payload = client.get_products_page(
            page=page, limit=page_size, include_inactive=True
        )
        source_total = int(payload.get("total") or 0)
        products.extend(
            product for product in payload["products"]
            if exact_brand(product.get("brand"), brand)
        )
        if not payload.get("has_more") or not payload["products"]:
            return products, source_total, page
        page += 1


def _erp_rows(database, brand):
    if not database.exists():
        return []
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM catalog_excel_products WHERE active = 1 ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows if exact_brand(row["excel_brand"], brand)]


def _other_products_digest(database, brand):
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM catalog_excel_products ORDER BY id"
        ).fetchall()
    payload = [
        dict(row) for row in rows
        if not exact_brand(row["excel_brand"], brand)
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _image_record(product):
    record = dict(product)
    record.update({
        "id": _text(product.get("external_product_id")),
        "xml_id": _text(product.get("external_xml_id")),
        "article": _text(product.get("external_sku")),
        "source_url": _text(product.get("url")),
    })
    return record


def restore_brand(client, database, brand="Луч", apply=False,
                  backup_root=None, image_root=None):
    products, source_total, pages = load_brand_products(client, brand)
    active = [product for product in products if product.get("active")]
    sync = BitrixERPProductSync(database)
    preview = sync.preview_products(
        active, create_only=True, require_exact_stock=True
    )
    missing = [
        product for product, result in zip(active, preview)
        if result["status"] == "created"
    ]
    conflicts = [
        result for result in preview
        if result["status"] in {"ambiguous", "skipped", "error"}
    ]
    before_rows = _erp_rows(database, brand)
    before_other = _other_products_digest(database, brand)
    store = ProductImageStore(database, image_root)
    backup_path = None
    source_result = {"created": 0, "updated": 0, "unchanged": 0, "conflicts": 0}
    card_results = preview
    stock_report = {"updated": 0, "status": "dry_run"}
    image_report = ProductImageImporter(database, store).run(
        [_image_record(product) for product in active], "bitrix", apply=False
    )

    if apply:
        if conflicts:
            raise RuntimeError("Brand restore has unresolved product matches")
        if not backup_root:
            raise ValueError("backup_root is required in apply mode")
        backup_path = backup_runtime(database.path, [store.root], Path(backup_root))
        source_result = BitrixCatalogImporter(database).import_products(
            active, "full_sync"
        )
        card_results = sync.apply_products(active, require_exact_stock=True)
        if any(
            result["status"] in {"ambiguous", "skipped", "error"}
            for result in card_results
        ):
            raise RuntimeError("Brand restore produced unresolved product matches")
        stock_report = BitrixStockSync(database).synchronize(active, apply=True)
        image_report = ProductImageImporter(database, store).run(
            [_image_record(product) for product in active],
            "bitrix",
            downloader=getattr(client, "download_brand_image", None),
            apply=True,
        )

    after_rows = _erp_rows(database, brand)
    by_external = {
        _text(row.get("bitrix_external_product_id")): row for row in after_rows
        if _text(row.get("bitrix_external_product_id"))
    }
    stock_mismatches = []
    for product in active:
        row = by_external.get(_text(product.get("external_product_id")))
        if row is None or float(row["stock"]) != float(product["stock"]):
            stock_mismatches.append(_text(product.get("external_product_id")))
    photo_ready = 0
    for product in active:
        row = by_external.get(_text(product.get("external_product_id")))
        filename = Path((row or {}).get("local_image_path") or "").name
        if product.get("images") and filename and (store.root / filename).is_file():
            photo_ready += 1
    after_preview = sync.preview_products(
        active, create_only=True, require_exact_stock=True
    )
    after_missing = sum(result["status"] == "created" for result in after_preview)
    report = {
        "mode": "apply" if apply else "dry_run",
        "brand": normalize_brand(brand),
        "source_catalog_total": source_total,
        "pages": pages,
        "bitrix_total": len(products),
        "bitrix_active": len(active),
        "erp_before": len(before_rows),
        "missing_before": len(missing),
        "imported": sum(result["status"] == "created" for result in card_results),
        "duplicates_skipped": sum(
            result["status"] in {"updated", "unchanged"}
            for result in card_results
        ),
        "missing_after": after_missing,
        "source_result": source_result,
        "stock_bitrix": sum(float(product["stock"]) for product in active),
        "stock_erp": sum(
            float(by_external[_text(product["external_product_id"])]["stock"])
            for product in active
            if _text(product["external_product_id"]) in by_external
        ),
        "stock_mismatch": len(stock_mismatches),
        "stock_mismatch_ids": stock_mismatches,
        "stock_report": stock_report,
        "photos": {
            "products_with_source_images": sum(
                bool(product.get("images")) for product in active
            ),
            "ready": photo_ready,
            "added": image_report["added"],
            "existing": image_report["existing"],
            "errors": image_report["errors"],
        },
        "backup_path": str(backup_path) if backup_path else None,
        "other_brands_changed": int(
            before_other != _other_products_digest(database, brand)
        ),
        "conflicts": conflicts,
    }
    report["status"] = "success" if (
        report["missing_after"] == 0
        and report["stock_mismatch"] == 0
        and report["other_brands_changed"] == 0
        and not report["photos"]["errors"]
    ) else "failed"
    return report


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", default="Луч")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument(
        "--database", type=Path,
        default=PROJECT_ROOT / "instance" / "catalog.db",
    )
    parser.add_argument(
        "--image-root", type=Path,
        default=PROJECT_ROOT / "instance" / "product_images",
    )
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    client = BitrixCatalogReadOnlyClient(
        os.getenv("BITRIX_CATALOG_URL"), os.getenv("BITRIX_CATALOG_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )
    report = restore_brand(
        client, CatalogDatabase(args.database), args.brand, args.apply,
        args.backup_root, args.image_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "success" else 2


if __name__ == "__main__":
    sys.exit(main())
