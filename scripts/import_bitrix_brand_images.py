#!/usr/bin/env python3
"""Dry-run or safely import Bitrix brand images into persistent ERP storage."""

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
from app.services.brand_images import (  # noqa: E402
    BitrixBrandImageImporter,
    BrandImageStore,
)


def timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def backup_runtime(database_path, image_root, backup_root):
    target = Path(backup_root) / "brand-images-{}".format(timestamp())
    target.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(str(database_path))
    destination = sqlite3.connect(str(target / "catalog.db"))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    if Path(image_root).is_dir():
        shutil.copytree(str(image_root), str(target / "brand_images"))
    return target


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database", default=str(DEFAULT_CATALOG_DATABASE_PATH))
    parser.add_argument(
        "--image-root", default=str(PROJECT_ROOT / "instance" / "brand_images")
    )
    parser.add_argument("--backup-dir")
    parser.add_argument("--output")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    args = parser.parse_args()
    load_dotenv(args.env_file)

    database = CatalogDatabase(args.database)
    store = BrandImageStore(database, args.image_root)
    client = BitrixCatalogReadOnlyClient(
        os.getenv("BITRIX_CATALOG_URL"),
        os.getenv("BITRIX_CATALOG_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )
    payload = client.get_brands(include_inactive=True)
    importer = BitrixBrandImageImporter(database, store)
    plan = importer.run(payload["brands"], apply=False)
    backup_path = None
    if args.apply:
        if not args.backup_dir:
            parser.error("--backup-dir is required with --apply")
        backup_path = backup_runtime(
            Path(args.database), Path(args.image_root), Path(args.backup_dir)
        )
        report = importer.run(
            payload["brands"], downloader=client.download_brand_image, apply=True
        )
    else:
        report = plan
    report["bitrix_storage"] = payload["storage"]
    report["bitrix_image_fields"] = payload["image_fields"]
    report["bitrix_source_ambiguities"] = payload["source_ambiguities"]
    report["backup_path"] = str(backup_path) if backup_path else None
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
