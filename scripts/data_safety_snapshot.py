#!/usr/bin/env python3
"""Print aggregate, non-personal production data counts for deploy comparison."""

from __future__ import print_function

import argparse
import json
import sqlite3
from pathlib import Path


def table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def database_counts(path, definitions):
    path = Path(path).resolve()
    if not path.exists():
        return {label: None for label, unused_tables, unused_condition in definitions}
    connection = sqlite3.connect(
        "file:{}?mode=ro".format(path), uri=True
    )
    try:
        tables = table_names(connection)
        result = {}
        for label, candidates, condition in definitions:
            table = next((name for name in candidates if name in tables), None)
            if table is None:
                result[label] = None
                continue
            statement = "SELECT COUNT(*) FROM {}".format(table)
            if condition:
                statement += " WHERE " + condition
            result[label] = int(connection.execute(statement).fetchone()[0])
        return result
    finally:
        connection.close()


def json_count(path):
    path = Path(path)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("items", "orders", "receipts", "repairs"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
        return len(payload)
    raise RuntimeError("unsupported JSON data shape: {}".format(path.name))


def snapshot(instance_dir):
    instance_dir = Path(instance_dir).resolve()
    catalog = database_counts(
        instance_dir / "catalog.db",
        (
            ("products", ("catalog_excel_products",), None),
            ("sales", ("erp_sales",), None),
            ("sale_items", ("erp_sale_items",), None),
            ("stock_movements", ("catalog_stock_movements",), None),
            ("receipts_db", ("erp_receipts",), None),
            ("receipt_items", ("erp_receipt_items",), None),
            ("comments", ("erp_order_comments",), None),
            ("active_inventories", ("erp_inventory_sessions",), "status='active'"),
            (
                "active_inventory_items",
                ("erp_inventory_items",),
                "session_id IN (SELECT id FROM erp_inventory_sessions "
                "WHERE status='active')",
            ),
        ),
    )
    connection = sqlite3.connect(
        "file:{}?mode=ro".format((instance_dir / "catalog.db").resolve()),
        uri=True,
    )
    try:
        tables = table_names(connection)
        catalog["active_stock_sum"] = (
            float(connection.execute(
                "SELECT COALESCE(SUM(stock), 0) FROM catalog_excel_products "
                "WHERE active=1"
            ).fetchone()[0] or 0)
            if "catalog_excel_products" in tables
            else None
        )
    finally:
        connection.close()
    orders = database_counts(
        instance_dir / "orders.db",
        (
            ("orders", ("orders_snapshot", "orders", "erp_orders", "order_snapshots"), None),
            (
                "order_items",
                ("order_items", "erp_order_items", "order_snapshot_items"),
                None,
            ),
        ),
    )
    orders_path = instance_dir / "orders.db"
    if orders_path.exists():
        connection = sqlite3.connect(
            "file:{}?mode=ro".format(orders_path.resolve()), uri=True
        )
        try:
            if "orders_snapshot" in table_names(connection):
                positions = 0
                for row in connection.execute(
                    "SELECT payload_json FROM orders_snapshot"
                ).fetchall():
                    payload = json.loads(row[0] or "{}")
                    items = payload.get("products") or payload.get("items") or []
                    positions += len(items) if isinstance(items, list) else 0
                orders["order_items"] = positions
                orders["order_item_units"] = float(
                    connection.execute(
                        "SELECT COALESCE(SUM(item_units), 0) FROM orders_snapshot"
                    ).fetchone()[0] or 0
                )
        finally:
            connection.close()
    return {
        "catalog": catalog,
        "orders": orders,
        "receipts_json": json_count(instance_dir / "receipts.json"),
        "repairs_json": json_count(instance_dir / "repair_cases.json"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-dir", required=True)
    arguments = parser.parse_args()
    print(json.dumps(
        snapshot(arguments.instance_dir),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ))


if __name__ == "__main__":
    main()
