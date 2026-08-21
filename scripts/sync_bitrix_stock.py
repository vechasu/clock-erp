#!/usr/bin/env python3
"""Synchronize active Bitrix quantities into existing ERP product cards."""

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
from app.services.bitrix_erp_product_sync import create_database_backup  # noqa: E402
from app.services.bitrix_stock_sync import BitrixStockSync  # noqa: E402


def load_active_products(client, page_size=200, progress_callback=None):
    products = []
    generated_at = ""
    page = 1
    while True:
        payload = client.get_products_page(
            page=page, limit=page_size, include_inactive=False
        )
        generated_at = payload.get("generated_at") or generated_at
        products.extend(payload["products"])
        if progress_callback:
            progress_callback({
                "page": page,
                "received": len(products),
                "source_total": payload["total"],
            })
        if not payload.get("has_more") or not payload["products"]:
            return products, generated_at
        page += 1


def sync_bitrix_stock(client, database, apply=False, backup=False,
                      backup_root=None, page_size=200, progress_callback=None):
    backup_path = None
    if apply or backup:
        backup_path = create_database_backup(database, backup_root)
    products, generated_at = load_active_products(
        client, max(1, min(int(page_size), 200)), progress_callback
    )
    report = BitrixStockSync(database).synchronize(
        products, apply=apply, source_generated_at=generated_at
    )
    report["active_products_found"] = len(products)
    report["backup_path"] = str(backup_path) if backup_path else None
    report["writes_performed"] = report["updated"] if apply else 0
    with database.connect() as connection:
        report["database"] = {
            "quick_check": connection.execute("PRAGMA quick_check").fetchone()[0],
            "foreign_key_errors": len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            ),
        }
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
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--page-size", type=int, default=200)
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        report = sync_bitrix_stock(
            build_client(),
            CatalogDatabase(),
            apply=bool(args.apply),
            backup=bool(args.backup),
            backup_root=args.backup_root,
            page_size=args.page_size,
            progress_callback=lambda state: print(
                json.dumps({"progress": state}, ensure_ascii=False),
                file=sys.stderr,
            ),
        )
    except Exception as error:
        print(
            "Bitrix stock synchronization failed: {}".format(type(error).__name__),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "success" else 2


if __name__ == "__main__":
    sys.exit(main())
