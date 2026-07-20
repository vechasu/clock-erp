#!/usr/bin/env python3
"""Import Bitrix catalog content into Vechasu without inventory side effects."""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyClient  # noqa: E402
from app.services.bitrix_catalog_importer import BitrixCatalogImporter  # noqa: E402


WRITE_MODES = ("create_only", "fill_empty", "update_content", "full_sync")


def belongs_to_category(product, category_id):
    category_id = str(category_id or "")
    for category in product.get("categories") or []:
        if str(category.get("id") or "") == category_id:
            return True
        if any(str(item.get("id") or "") == category_id for item in category.get("path_items") or []):
            return True
    return False


def database_counts(database):
    if not database.exists():
        return {
            "products": 0, "active": 0, "inactive": 0, "duplicate_external_ids": 0,
            "categories": 0, "properties": 0, "property_values": 0,
            "images": 0, "prices": 0, "offers": 0, "failed_runs": 0,
        }
    queries = {
        "products": "SELECT COUNT(*) FROM catalog_products",
        "active": "SELECT COUNT(*) FROM catalog_products WHERE active = 1",
        "inactive": "SELECT COUNT(*) FROM catalog_products WHERE active = 0",
        "duplicate_external_ids": (
            "SELECT COUNT(*) FROM (SELECT external_source, external_product_id "
            "FROM catalog_products GROUP BY external_source, external_product_id HAVING COUNT(*) > 1)"
        ),
        "categories": "SELECT COUNT(*) FROM catalog_categories",
        "properties": "SELECT COUNT(*) FROM catalog_properties",
        "property_values": "SELECT COUNT(*) FROM catalog_product_property_values",
        "images": "SELECT COUNT(*) FROM catalog_images",
        "prices": "SELECT COUNT(*) FROM catalog_prices",
        "offers": "SELECT COUNT(*) FROM catalog_offers",
        "failed_runs": "SELECT COUNT(*) FROM catalog_sync_runs WHERE status = 'failed'",
    }
    with database.connect() as connection:
        return {key: connection.execute(query).fetchone()[0] for key, query in queries.items()}


def import_catalog(client, database, mode="preview", target_mode="full_sync", max_items=None,
                   category_id="", include_inactive=False, inactive_only=False,
                   page_size=200, progress_callback=None):
    if mode not in ("preview",) + WRITE_MODES:
        raise ValueError("Unsupported import mode")
    if target_mode not in WRITE_MODES:
        raise ValueError("Unsupported target mode")
    if inactive_only:
        include_inactive = True
    max_items = int(max_items) if max_items not in (None, 0) else None
    if max_items is not None and max_items < 1:
        raise ValueError("max_items must be positive")
    page_size = max(1, min(int(page_size), 200))
    importer = BitrixCatalogImporter(database)
    totals = {"created": 0, "updated": 0, "unchanged": 0, "conflicts": 0}
    received = selected = pages = runs = 0
    source_total = 0
    page = 1
    while True:
        payload = client.get_products_page(
            page=page, limit=page_size, include_inactive=include_inactive
        )
        pages += 1
        source_total = payload["total"]
        products = payload["products"]
        received += len(products)
        filtered = []
        for product in products:
            if inactive_only and product.get("active", True):
                continue
            if category_id and not belongs_to_category(product, category_id):
                continue
            if max_items is not None and selected + len(filtered) >= max_items:
                break
            filtered.append(product)
        if filtered:
            result = (
                importer.preview(filtered, target_mode)
                if mode == "preview"
                else importer.import_products(filtered, mode)
            )
            runs += 1
            selected += len(filtered)
            for key in totals:
                totals[key] += result[key]
        if progress_callback:
            progress_callback({
                "page": page, "received": received, "selected": selected,
                "source_total": source_total,
            })
        if max_items is not None and selected >= max_items:
            break
        if not payload["has_more"] or not products:
            break
        page += 1
    report = {
        "mode": mode,
        "target_mode": target_mode if mode == "preview" else mode,
        "status": "success",
        "source_total": source_total,
        "source_rows_scanned": received,
        "selected_products": selected,
        "pages_processed": pages,
        "import_transactions": runs if mode != "preview" else 0,
        "preview_batches": runs if mode == "preview" else 0,
        "created": totals["created"],
        "updated": totals["updated"],
        "unchanged": totals["unchanged"],
        "conflicts": totals["conflicts"],
        "writes_performed": 0 if mode == "preview" else totals["created"] + totals["updated"],
        "moysklad_writes": 0,
        "inventory_operations": 0,
        "database": database_counts(database),
    }
    return report


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preview",) + WRITE_MODES, default="preview")
    parser.add_argument("--target-mode", choices=WRITE_MODES, default="full_sync")
    parser.add_argument("--limit", type=int, default=0, help="0 imports every selected product")
    parser.add_argument("--category-id", default="")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--inactive-only", action="store_true")
    parser.add_argument("--page-size", type=int, default=200)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be 0 or greater")

    load_dotenv(PROJECT_ROOT / ".env")
    client = BitrixCatalogReadOnlyClient(
        export_url=os.getenv("BITRIX_CATALOG_URL"),
        token=os.getenv("BITRIX_CATALOG_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )
    database = CatalogDatabase()

    def show_progress(state):
        print(json.dumps({"progress": state}, ensure_ascii=False), file=sys.stderr)

    report = import_catalog(
        client=client,
        database=database,
        mode=args.mode,
        target_mode=args.target_mode,
        max_items=args.limit or None,
        category_id=args.category_id,
        include_inactive=args.include_inactive,
        inactive_only=args.inactive_only,
        page_size=args.page_size,
        progress_callback=show_progress,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
