#!/usr/bin/env python3
"""Dry-run or import product photos and brand logos into local ERP storage."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase, DEFAULT_CATALOG_DATABASE_PATH  # noqa: E402
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyClient  # noqa: E402
from app.services.brand_images import BitrixBrandImageImporter, BrandImageStore  # noqa: E402
from app.services.product_images import (  # noqa: E402
    ProductImageImporter,
    ProductImageStore,
    TicTacToyImageSource,
)
from app.services.shared_catalog import normalized_name  # noqa: E402


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def backup_runtime(database_path, roots, backup_root):
    target = Path(backup_root) / "catalog-images-{}".format(_timestamp())
    target.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(str(database_path))
    try:
        backup = getattr(source, "backup", None)
        if backup is not None:
            destination = sqlite3.connect(str(target / "catalog.db"))
            try:
                backup(destination)
            finally:
                destination.close()
        else:
            # Python 3.6 builds can lack Connection.backup(). Flush WAL pages,
            # then hold writers while copying the stable database file.
            source.execute("PRAGMA wal_checkpoint(FULL)")
            source.execute("BEGIN IMMEDIATE")
            try:
                shutil.copy2(str(database_path), str(target / "catalog.db"))
            finally:
                source.rollback()
    finally:
        source.close()
    for root in roots:
        root = Path(root)
        if root.is_dir():
            shutil.copytree(str(root), str(target / root.name))
    return target


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database", default=str(DEFAULT_CATALOG_DATABASE_PATH))
    parser.add_argument("--product-image-root", default=str(PROJECT_ROOT / "instance" / "product_images"))
    parser.add_argument("--brand-image-root", default=str(PROJECT_ROOT / "instance" / "brand_images"))
    parser.add_argument("--backup-dir")
    parser.add_argument("--output")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--max-products", type=int)
    parser.add_argument("--skip-site", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.env_file)

    if args.apply and not args.backup_dir:
        parser.error("--backup-dir is required with --apply")

    database = CatalogDatabase(args.database)
    database.initialize()
    product_store = ProductImageStore(database, args.product_image_root)
    brand_store = BrandImageStore(database, args.brand_image_root)
    client = BitrixCatalogReadOnlyClient(
        os.getenv("BITRIX_CATALOG_URL"),
        os.getenv("BITRIX_CATALOG_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )

    backup_path = None
    if args.apply:
        backup_path = backup_runtime(
            Path(args.database), [product_store.root, brand_store.root],
            Path(args.backup_dir),
        )

    bitrix_products = list(client.iter_products(
        limit=200, max_items=args.max_products, include_inactive=False
    ))
    product_importer = ProductImageImporter(database, product_store)
    bitrix_report = product_importer.run(
        bitrix_products, "bitrix",
        downloader=client.download_brand_image if args.apply else None,
        apply=args.apply,
    )

    site_records = []
    site_errors = []
    if not args.skip_site:
        source = TicTacToyImageSource()
        for product in product_importer._products():
            local_path = Path(product.get("local_image_path") or "").name
            if local_path and (product_store.root / local_path).is_file():
                continue
            try:
                record = source.record_for_product(product)
                if record:
                    site_records.append(record)
            except Exception as error:
                site_errors.append({
                    "product_id": int(product["id"]),
                    "name": product["excel_name_raw"],
                    "error": type(error).__name__,
                })
    site_report = product_importer.run(
        site_records, "tictactoy",
        downloader=source.download if site_records and args.apply else None,
        apply=args.apply,
    )
    site_report["source_errors"] = site_errors

    brand_payload = client.get_brands(include_inactive=True)
    brand_report = BitrixBrandImageImporter(database, brand_store).run(
        brand_payload["brands"],
        downloader=client.download_brand_image if args.apply else None,
        apply=args.apply,
    )
    bitrix_brand_names = {
        normalized_name(record.get("name"))
        for record in brand_payload["brands"] if record.get("images")
    }
    site_brand_source = TicTacToyImageSource()
    site_brand_records = [
        record for record in site_brand_source.brand_records()
        if normalized_name(record.get("name")) not in bitrix_brand_names
    ]
    site_brand_report = BitrixBrandImageImporter(database, brand_store).run(
        site_brand_records,
        downloader=site_brand_source.download if args.apply else None,
        apply=args.apply,
    )
    with database.connect() as connection:
        missing_brands = [row["name"] for row in connection.execute(
            "SELECT name FROM erp_brands WHERE active = 1 "
            "AND (image_path IS NULL OR trim(image_path) = '') "
            "ORDER BY name COLLATE NOCASE"
        ).fetchall()]

    report = {
        "mode": "apply" if args.apply else "dry_run",
        "backup_path": str(backup_path) if backup_path else None,
        "products": {"bitrix": bitrix_report, "tictactoy": site_report},
        "brands": {"bitrix": brand_report, "tictactoy": site_brand_report},
        "brands_without_logos": missing_brands,
        "stock_writes": 0,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if (
        bitrix_report["errors"] or site_report["errors"]
        or site_errors or brand_report["errors"] or site_brand_report["errors"]
    ) else 0


if __name__ == "__main__":
    sys.exit(main())
