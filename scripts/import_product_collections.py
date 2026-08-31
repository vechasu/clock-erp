#!/usr/bin/env python3
"""Dry-run or import Bitrix collection membership without changing products."""

from __future__ import print_function

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyClient  # noqa: E402
from app.services.product_collections import ProductCollections  # noqa: E402


PROTECTED_PRODUCT_COLUMNS = (
    "id", "active", "excel_name_raw", "model", "excel_article", "excel_brand",
    "excel_category", "brand_id", "category_id", "stock", "cell",
    "bitrix_price_amount", "bitrix_price_currency", "bitrix_gallery_json",
    "bitrix_primary_image_url", "bitrix_thumbnail_url", "moysklad_product_id",
)


def protected_snapshot(database):
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT {} FROM catalog_excel_products ORDER BY id".format(
                ",".join(PROTECTED_PRODUCT_COLUMNS)
            )
        ).fetchall()
    payload = json.dumps(
        [list(row) for row in rows], ensure_ascii=False,
        separators=(",", ":"), sort_keys=False,
    ).encode("utf-8")
    return {"rows": len(rows), "sha256": hashlib.sha256(payload).hexdigest()}


def read_bitrix(client):
    products = []
    page = 1
    while True:
        payload = client.get_products_page(
            page=page, limit=200, include_inactive=True
        )
        products.extend(payload["products"])
        if not payload.get("has_more"):
            return products
        page += 1


def printable(report):
    result = dict(report)
    result.pop("links", None)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="replace imported membership after the default dry-run",
    )
    parser.add_argument("--database", default="")
    arguments = parser.parse_args()
    load_dotenv(ROOT / ".env")
    database = CatalogDatabase(arguments.database or None)
    service = ProductCollections(database)
    client = BitrixCatalogReadOnlyClient(
        export_url=os.getenv("BITRIX_CATALOG_URL"),
        token=os.getenv("BITRIX_CATALOG_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )
    products = read_bitrix(client)
    before = protected_snapshot(database)
    report = (
        service.import_bitrix_memberships(products)
        if arguments.apply else service.dry_run(products)
    )
    after = protected_snapshot(database)
    if before != after:
        raise RuntimeError("protected product data changed during collection import")
    output = printable(report)
    output.update({
        "mode": "apply" if arguments.apply else "dry-run",
        "protected_product_data_unchanged": True,
        "protected_snapshot": after,
    })
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
