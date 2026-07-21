#!/usr/bin/env python3
"""Read and validate up to 10 Bitrix orders without writing any data."""

import argparse
import json
import os
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.bitrix_orders import (
    BitrixOrdersReadOnlyClient,
    match_items,
    normalize_order,
)


MOYSKLAD_PRODUCTS_URL = "https://api.moysklad.ru/api/remap/1.2/entity/product"


def load_json(path, expected_type, default):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value if isinstance(value, expected_type) else default


def get_catalog():
    token = os.getenv("MOYSKLAD_TOKEN")
    if not token:
        return [], "MOYSKLAD_TOKEN is not configured; product matching was skipped"

    try:
        response = requests.get(
            MOYSKLAD_PRODUCTS_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json;charset=utf-8",
            },
            params={"limit": 1000},
            timeout=(3.05, 15),
        )
    except (requests.Timeout, requests.ConnectionError) as error:
        return [], f"MoySklad read failed ({type(error).__name__})"

    if response.status_code >= 400:
        return [], f"MoySklad read failed (HTTP {response.status_code})"
    try:
        payload = response.json()
    except ValueError:
        return [], "MoySklad product catalog returned non-JSON data"
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return [], "MoySklad product catalog could not be read"
    return payload["rows"], None


def presence(value):
    return value not in (None, "", [], {})


def build_report(limit, include_catalog=True):
    client = BitrixOrdersReadOnlyClient(
        orders_url=os.getenv("BITRIX_ORDERS_URL"),
        order_url=os.getenv("BITRIX_ORDER_URL"),
        token=os.getenv("BITRIX_ORDERS_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
    )
    fetched = client.get_latest_orders(limit=limit)
    orders = [normalize_order(order) for order in fetched["orders"]]

    catalog, catalog_warning = (
        get_catalog() if include_catalog else ([], "Catalog matching disabled")
    )
    mappings = load_json(
        PROJECT_ROOT / "instance" / "product_mappings.json", dict, {}
    )
    imported_orders = load_json(
        PROJECT_ROOT / "instance" / "bitrix_imported_orders.json", dict, {}
    )
    stock_operations = load_json(
        PROJECT_ROOT / "instance" / "stock_operations.json", list, []
    )
    related_order_ids = {
        str(row.get("order_id") or "")
        for row in stock_operations
        if isinstance(row, dict) and str(row.get("source") or "") == "Заказ Битрикс"
    }

    preview = []
    matched = 0
    requires_mapping = 0
    field_counts = {}
    item_field_counts = {}
    item_count = 0
    classifications = {"new": 0, "update": 0, "duplicate": 0, "error": 0}

    for order in orders:
        matched_items = match_items(order["items"], catalog, mappings)
        item_count += len(matched_items)
        matched += sum(row["match_status"] == "matched" for row in matched_items)
        requires_mapping += sum(
            row["match_status"] == "requires_mapping" for row in matched_items
        )
        for key, value in order.items():
            if key != "items" and presence(value):
                field_counts[key] = field_counts.get(key, 0) + 1
        for item in matched_items:
            for key, value in item.items():
                if presence(value):
                    item_field_counts[key] = item_field_counts.get(key, 0) + 1

        import_key = f"bitrix:{order['external_id']}"
        stored = imported_orders.get(import_key)
        if isinstance(stored, dict):
            remote_updated = str(order.get("updated_at") or "")
            stored_updated = str(stored.get("external_updated_at") or "")
            classification = (
                "duplicate"
                if remote_updated and remote_updated == stored_updated
                else "update"
            )
        else:
            classification = "new"
        classifications[classification] += 1

        preview.append({
            "external_id": order["external_id"],
            "external_source": "bitrix",
            "number": order["number"],
            "created_at": order["created_at"],
            "updated_at": order["updated_at"],
            "status": order["status"],
            "customer_fields_present": {
                "name": presence(order["customer"]),
                "phone": presence(order["phone"]),
                "email": presence(order["email"]),
            },
            "source": order["source"],
            "source_kind": order["source_kind"],
            "payment": order["payment"],
            "paid": order["paid"],
            "delivery": order["delivery"],
            "comment": order["comment"],
            "total": order["total"],
            "currency": order["currency"],
            "classification": classification,
            "has_related_stock_operation": order["external_id"] in related_order_ids,
            "items": matched_items,
        })

    return {
        "mode": "dry-run",
        "writes_performed": 0,
        "inventory_changes_performed": 0,
        "source_system": "1C-Bitrix Site Management sale module via custom PHP API",
        "orders_storage": "Internet-store sale orders (not CRM deals/smart processes)",
        "fetch": {
            key: fetched[key]
            for key in (
                "requested_limit", "server_list_count", "server_honored_limit",
                "pagination", "request_count"
            )
        },
        "summary": {
            "orders": len(orders),
            "items": item_count,
            "matched_items": matched,
            "requires_mapping": requires_mapping,
            **classifications,
        },
        "field_presence": {
            "orders": field_counts,
            "items": item_field_counts,
        },
        "price_semantics": {
            "sale_unit_price": "Bitrix product price returned by the current API",
            "original_unit_price": "separate field; unavailable when null",
            "purchase_unit_price": (
                "separate field; unavailable when null and never inferred from sale price"
            ),
            "line_total": (
                "API value when present, otherwise sale unit price multiplied by quantity"
            ),
        },
        "catalog_warning": catalog_warning,
        "preview": preview,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="1..10 (default: 10)")
    parser.add_argument(
        "--no-catalog", action="store_true", help="skip read-only MoySklad matching"
    )
    parser.add_argument(
        "--allow-read-only-network",
        action="store_true",
        help="explicitly allow GET requests to configured Bitrix/MoySklad endpoints",
    )
    parser.add_argument(
        "--include-preview",
        action="store_true",
        help="include order and product details that may contain live business data",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        parser.error("--limit must be between 1 and 10")
    if not args.allow_read_only_network:
        parser.error("--allow-read-only-network is required before any HTTP request")

    report = build_report(args.limit, include_catalog=not args.no_catalog)
    if not args.include_preview:
        report.pop("preview", None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
