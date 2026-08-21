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
from app.services.inventory_lock import assert_no_active_inventory  # noqa: E402
from app.services.protected_catalog_brands import (  # noqa: E402
    PROTECTED_BRANDS,
    protected_brand_rows,
    protected_product_brand,
    protected_state_digest,
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


def _source_brand_ids(product, protected_brand):
    discovered = {
        str(prop.get("enum_id") or "").strip()
        for prop in product.get("properties") or []
        if str(prop.get("enum_id") or "").strip()
    }
    expected = set(PROTECTED_BRANDS[protected_brand]["source_brand_ids"])
    return sorted(discovered & expected)


def _database_verification(database):
    with database.connect() as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        duplicate_external_ids = connection.execute(
            "SELECT COUNT(*) FROM (SELECT bitrix_external_product_id "
            "FROM catalog_excel_products WHERE active = 1 "
            "AND trim(COALESCE(bitrix_external_product_id, '')) <> '' "
            "GROUP BY bitrix_external_product_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    return {
        "quick_check": quick_check,
        "foreign_key_errors": foreign_key_errors,
        "duplicate_bitrix_links": duplicate_external_ids,
    }


def import_missing_active_products(client, database, apply=False, page_size=200,
                                   backup_root=None, progress_callback=None):
    """Create only missing active ERP cards behind a protected-brand firewall."""
    page_size = max(1, min(int(page_size), 200))
    report = _initial_report("apply_missing_active" if apply else "dry_run_missing_active")
    report.update({
        "active_products_found": 0,
        "already_in_erp": 0,
        "excluded_total": 0,
        "excluded_by_brand": {
            brand: {"count": 0, "source_brand_ids": [], "product_ids": []}
            for brand in PROTECTED_BRANDS
        },
        "protected_erp_brands": [],
        "protected_unchanged": False,
        "without_exact_stock": {"count": 0, "products": []},
        "not_imported": [],
        "imported_stock_quantity": 0,
        "database_verification": {},
    })
    eligible = []
    seen_source_ids = set()
    page = 1
    while True:
        payload = client.get_products_page(
            page=page, limit=page_size, include_inactive=False
        )
        report["pages_processed"] += 1
        report["source_total"] = int(payload.get("total") or 0)
        for product in payload["products"]:
            report["source_rows_scanned"] += 1
            report["active_products_found"] += 1
            source_id = _text(product.get("external_product_id"))
            if source_id and source_id in seen_source_ids:
                report["source_duplicates"] += 1
                report["skipped"] += 1
                continue
            if source_id:
                seen_source_ids.add(source_id)
            protected_brand = protected_product_brand(product)
            if protected_brand:
                item = report["excluded_by_brand"][protected_brand]
                item["count"] += 1
                item["product_ids"].append(source_id)
                item["source_brand_ids"] = sorted(set(
                    item["source_brand_ids"]
                    + _source_brand_ids(product, protected_brand)
                ))
                report["excluded_total"] += 1
                continue
            eligible.append(product)
            _record_quality(report, product)
        if progress_callback:
            progress_callback({
                "page": page,
                "received": report["source_rows_scanned"],
                "source_total": report["source_total"],
            })
        if not payload.get("has_more") or not payload["products"]:
            break
        page += 1

    if database.exists():
        with database.connect() as connection:
            report["protected_erp_brands"] = protected_brand_rows(connection)
            before_digest = protected_state_digest(connection)
            assert_no_active_inventory(connection)
    else:
        before_digest = None

    card_sync = BitrixERPProductSync(database)
    preview = card_sync.preview_products(
        eligible, create_only=True, require_exact_stock=True
    )
    new_products = []
    for product, result in zip(eligible, preview):
        if result["status"] == "skipped" and result.get("error") == "exact_stock_unavailable":
            report["skipped"] += 1
            report["without_exact_stock"]["count"] += 1
            report["without_exact_stock"]["products"].append(_quality_item(product))
            report["not_imported"].append(result)
        elif result["status"] == "skipped":
            report["skipped"] += 1
            report["not_imported"].append(result)
        elif result["status"] == "ambiguous":
            report["ambiguous"] += 1
            report["conflicts"].append(result)
            report["not_imported"].append(result)
        else:
            _add_card_result(report, result)
        if result["status"] == "created":
            new_products.append(product)
    report["already_in_erp"] = report["matched"]

    if apply:
        backup_path = create_database_backup(database, backup_root)
        report["backup_path"] = str(backup_path) if backup_path else None
        report["created"] = 0
        if new_products:
            source_result = BitrixCatalogImporter(database).import_products(
                new_products, "full_sync"
            )
            _add_source_totals(report, source_result)
            applied = card_sync.apply_products(
                new_products, create_only=True, require_exact_stock=True
            )
            report["inventory_operations"] = 0
            report["stock_changes"] = 0
            for result in applied:
                status = result["status"]
                if status == "created":
                    report["created"] += 1
                    quantity = float(result.get("stock_imported") or 0)
                    report["imported_stock_quantity"] += quantity
                    if quantity:
                        report["inventory_operations"] += 1
                        report["stock_changes"] += 1
                elif status in {"ambiguous", "skipped", "error"}:
                    _add_card_result(report, result)
        report["writes_performed"] = (
            report["created"]
            + report["source_catalog"]["created"]
            + report["source_catalog"]["updated"]
        )

    if database.exists():
        with database.connect() as connection:
            after_digest = protected_state_digest(connection)
        report["protected_unchanged"] = before_digest == after_digest
    else:
        report["protected_unchanged"] = before_digest is None
    report["database_verification"] = _database_verification(database)
    report["duplicate_bitrix_links"] = card_sync.duplicate_bitrix_links()
    report["duplicate_bitrix_link_count"] = len(report["duplicate_bitrix_links"])
    verification = report["database_verification"]
    if not report["protected_unchanged"]:
        report["status"] = "protected_brand_changed"
    elif verification["quick_check"] != "ok" or verification["foreign_key_errors"]:
        report["status"] = "database_verification_failed"
    elif report["errors"]:
        report["status"] = "completed_with_errors"
    else:
        report["status"] = "success"
    return report


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
    parser.add_argument("--missing-active-only", action="store_true")
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")

    try:
        operation = (
            import_missing_active_products
            if args.missing_active_only else sync_bitrix_products
        )
        report = operation(
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
