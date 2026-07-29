#!/usr/bin/env python3
"""Synchronize the full Bitrix catalog into ERP product cards."""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyClient  # noqa: E402
from app.services.brand_values import (  # noqa: E402
    is_numeric_brand,
    normalize_brand,
)
from app.services.bitrix_catalog_importer import BitrixCatalogImporter  # noqa: E402
from app.services.bitrix_erp_product_sync import (  # noqa: E402
    BitrixERPProductSync,
    create_database_backup,
)


RESULT_KEYS = ("created", "updated", "unchanged", "ambiguous", "skipped", "error")
SOURCE_RESULT_KEYS = ("created", "updated", "unchanged", "conflicts")


def _text(value):
    return str(value or "").strip()


def _quality_item(product):
    return {
        "id": _text(product.get("external_product_id")),
        "name": _text(product.get("name")),
    }


def _record_quality(report, product):
    price = product.get("sale_price") or {}
    checks = (
        ("without_price", price.get("value") is None),
        ("without_brand", not normalize_brand(product.get("brand"))),
        (
            "without_category",
            not (
                (product.get("category") or {}).get("id")
                or (product.get("category") or {}).get("name")
            ),
        ),
        ("without_image", not (product.get("images") or [])),
    )
    for key, missing in checks:
        if missing:
            report[key]["count"] += 1
            report[key]["products"].append(_quality_item(product))
    if (
        product.get("brand_validation_error")
        == "numeric_brand_rejected"
        or is_numeric_brand(product.get("brand"))
    ):
        report["invalid_brand"]["count"] += 1
        report["invalid_brand"]["products"].append(_quality_item(product))


def _add_source_totals(report, source_result):
    for key in SOURCE_RESULT_KEYS:
        report["source_catalog"][key] += int(source_result.get(key) or 0)


def _add_card_result(report, result):
    status = result["status"]
    if status not in RESULT_KEYS:
        status = "error"
    report[status] += 1
    if status in {"updated", "unchanged"}:
        report["matched"] += 1
    if status == "ambiguous":
        report["conflicts"].append(result)
    elif status in {"skipped", "error"}:
        report["errors"].append(result)


def _initial_report(mode):
    return {
        "mode": mode,
        "status": "running",
        "source_total": 0,
        "source_rows_scanned": 0,
        "pages_processed": 0,
        "created": 0,
        "matched": 0,
        "updated": 0,
        "unchanged": 0,
        "ambiguous": 0,
        "skipped": 0,
        "error": 0,
        "source_duplicates": 0,
        "source_catalog": {key: 0 for key in SOURCE_RESULT_KEYS},
        "without_price": {"count": 0, "products": []},
        "without_brand": {"count": 0, "products": []},
        "invalid_brand": {"count": 0, "products": []},
        "without_category": {"count": 0, "products": []},
        "without_image": {"count": 0, "products": []},
        "conflicts": [],
        "errors": [],
        "backup_path": None,
        "writes_performed": 0,
        "inventory_operations": 0,
        "stock_changes": 0,
        "moysklad_writes": 0,
    }


def _import_source_page(importer, products, report):
    """Import one page, falling back to isolated products after a page failure."""
    try:
        result = importer.import_products(products, "full_sync")
        _add_source_totals(report, result)
        return products
    except Exception:
        imported = []
        for product in products:
            try:
                result = importer.import_products([product], "full_sync")
                _add_source_totals(report, result)
                if result.get("conflicts"):
                    report["errors"].append({
                        "status": "error",
                        "external_product_id": _text(product.get("external_product_id")),
                        "name": _text(product.get("name")),
                        "error": "source_catalog_conflict",
                    })
                    report["error"] += 1
                    continue
                imported.append(product)
            except Exception as error:
                report["errors"].append({
                    "status": "error",
                    "external_product_id": _text(product.get("external_product_id")),
                    "name": _text(product.get("name")),
                    "error": type(error).__name__,
                })
                report["error"] += 1
        return imported


def sync_bitrix_products(client, database, apply=False, page_size=200,
                         backup_root=None, progress_callback=None):
    page_size = max(1, min(int(page_size), 200))
    report = _initial_report("apply" if apply else "dry_run")
    if apply:
        backup_path = create_database_backup(database, backup_root)
        report["backup_path"] = str(backup_path) if backup_path else None

    catalog_importer = BitrixCatalogImporter(database)
    card_sync = BitrixERPProductSync(database)
    page = 1
    seen_source_ids = set()
    while True:
        payload = client.get_products_page(
            page=page,
            limit=page_size,
            include_inactive=True,
        )
        report["pages_processed"] += 1
        report["source_total"] = int(payload.get("total") or 0)
        products = []
        for product in payload["products"]:
            source_id = _text(product.get("external_product_id"))
            if source_id and source_id in seen_source_ids:
                report["source_duplicates"] += 1
                report["skipped"] += 1
                continue
            if source_id:
                seen_source_ids.add(source_id)
            products.append(product)
            report["source_rows_scanned"] += 1
            _record_quality(report, product)

        if products:
            if apply:
                products = _import_source_page(
                    catalog_importer, products, report
                )
                card_results = card_sync.apply_products(products)
            else:
                source_preview = catalog_importer.preview(products, "full_sync")
                _add_source_totals(report, source_preview)
                card_results = card_sync.preview_products(products)
            for result in card_results:
                _add_card_result(report, result)

        if progress_callback:
            progress_callback({
                "page": page,
                "received": report["source_rows_scanned"],
                "source_total": report["source_total"],
            })
        if not payload.get("has_more") or not payload["products"]:
            break
        page += 1

    duplicates = card_sync.duplicate_bitrix_links()
    report["duplicate_bitrix_links"] = duplicates
    report["duplicate_bitrix_link_count"] = len(duplicates)
    report["writes_performed"] = (
        report["created"] + report["updated"]
        + report["source_catalog"]["created"]
        + report["source_catalog"]["updated"]
        if apply else 0
    )
    if report["errors"]:
        report["status"] = "completed_with_errors"
    elif report["conflicts"]:
        report["status"] = "completed_with_conflicts"
    else:
        report["status"] = "success"
    return report


def build_client():
    return BitrixCatalogReadOnlyClient(
        export_url=os.getenv("BITRIX_CATALOG_URL"),
        token=os.getenv("BITRIX_CATALOG_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")

    try:
        report = sync_bitrix_products(
            client=build_client(),
            database=CatalogDatabase(),
            apply=bool(args.apply),
            page_size=args.page_size,
            backup_root=args.backup_root,
            progress_callback=lambda state: print(
                json.dumps({"progress": state}, ensure_ascii=False),
                file=sys.stderr,
            ),
        )
    except Exception as error:
        print(
            "Bitrix product synchronization failed: {}".format(type(error).__name__),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "success" else 2


if __name__ == "__main__":
    sys.exit(main())
