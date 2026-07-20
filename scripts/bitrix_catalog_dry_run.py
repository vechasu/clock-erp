#!/usr/bin/env python3
"""Inspect at most 20 Bitrix catalog products without writing any data."""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.bitrix_catalog import (  # noqa: E402
    BitrixCatalogReadOnlyClient,
    match_product,
)


IMPORTANT_FIELDS = (
    ("external_product_id", "ID"),
    ("external_xml_id", "XML_ID"),
    ("external_sku", "артикул"),
    ("name", "название"),
    ("category", "категория"),
    ("images", "изображения"),
    ("sale_price", "цена продажи"),
)


def missing_important_fields(product):
    missing = []
    for field, label in IMPORTANT_FIELDS:
        value = product.get(field)
        if field == "category":
            value = value.get("id") or value.get("name") if isinstance(value, dict) else value
        if value in (None, "", [], {}):
            missing.append(label)
    return missing


def build_report(products, vechasu_products, request_count=0, total=None, page_limit=100,
                 confirmed_mappings=None):
    rows = []
    categories = set()
    offers_count = images_count = errors = 0
    matched = ambiguous = new = 0
    for product in products:
        try:
            match = match_product(product, vechasu_products, confirmed_mappings)
            if match["status"] == "matched":
                matched += 1
            elif match["status"] == "ambiguous":
                ambiguous += 1
            else:
                new += 1
            product_categories = product.get("categories") or []
            category = product.get("category") or {}
            category_label = "/".join(category.get("path") or []) or category.get("name") or ""
            for product_category in product_categories or [category]:
                product_category_label = (
                    "/".join(product_category.get("path") or [])
                    or product_category.get("name")
                    or ""
                )
                if product_category.get("id") or product_category_label:
                    categories.add((product_category.get("id") or "", product_category_label))
            offers_count += len(product.get("offers") or [])
            images_count += len(product.get("images") or [])
            sale_price = product.get("sale_price") or {}
            rows.append({
                "id": product.get("external_product_id"),
                "name": product.get("name"),
                "xml_id": product.get("external_xml_id"),
                "sku": product.get("external_sku"),
                "brand": product.get("brand"),
                "category": category_label,
                "properties_count": len(product.get("properties") or []),
                "images_count": len(product.get("images") or []),
                "price": sale_price.get("value"),
                "currency": sale_price.get("currency"),
                "has_sku_offers": bool(product.get("offers")),
                "match_status": match["status"],
                "match_method": match["method"],
                "matched_product_id": (match.get("product") or {}).get("id"),
                "missing_important_fields": missing_important_fields(product),
            })
        except (TypeError, ValueError):
            errors += 1
    total_value = total if total is not None else len(products)
    estimated_requests = (int(total_value) + page_limit - 1) // page_limit if page_limit else None
    return {
        "mode": "read_only_dry_run",
        "writes_performed": 0,
        "products": rows,
        "summary": {
            "received_products": len(products),
            "offers": offers_count,
            "categories_in_sample": len(categories),
            "images": images_count,
            "with_sku": sum(bool(row.get("sku")) for row in rows),
            "with_xml_id": sum(bool(row.get("xml_id")) for row in rows),
            "with_description": sum(bool(product.get("preview_text") or product.get("detail_text")) for product in products),
            "with_sale_price": sum(bool(product.get("sale_price")) for product in products),
            "unambiguous_matches": matched,
            "ambiguous_matches": ambiguous,
            "new_products": new,
            "errors": errors,
            "requests_in_sample": request_count,
            "reported_catalog_total": total,
            "estimated_export_requests_at_limit_100": estimated_requests,
        },
    }


def load_vechasu_products():
    """Read current products from MoySklad; this method performs GET requests only."""
    from app.clients.moysklad import MoySkladClient

    client = MoySkladClient()
    products = []
    offset = 0
    while True:
        response = client.get(
            "/entity/product",
            params={"limit": 1000, "offset": offset, "expand": "attributes"},
        )
        rows = response.get("rows", []) if isinstance(response, dict) else []
        products.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < 1000:
            return products
        offset += len(rows)


def load_confirmed_mappings():
    path = PROJECT_ROOT / "instance" / "product_mappings.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20, help="sample size, from 1 to 20")
    parser.add_argument("--without-vechasu", action="store_true",
                        help="skip the read-only MoySklad match query")
    args = parser.parse_args()
    sample_limit = max(1, min(args.limit, 20))

    load_dotenv(PROJECT_ROOT / ".env")
    client = BitrixCatalogReadOnlyClient(
        export_url=os.getenv("BITRIX_CATALOG_URL"),
        token=os.getenv("BITRIX_CATALOG_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )
    first_page = client.get_products_page(page=1, limit=sample_limit)
    products = first_page["products"][:sample_limit]
    vechasu_products = [] if args.without_vechasu else load_vechasu_products()
    report = build_report(
        products,
        vechasu_products,
        request_count=client.request_count,
        total=first_page["total"],
        page_limit=100,
        confirmed_mappings=load_confirmed_mappings(),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
