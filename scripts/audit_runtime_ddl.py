#!/usr/bin/env python3
"""Trace production-like startup SQL on isolated SQLite database copies."""

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import socket
import sqlite3
import sys
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DDL_PATTERN = re.compile(
    r"^\s*(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER)|"
    r"ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX|TRIGGER))\b",
    re.IGNORECASE,
)
OBJECT_PATTERN = re.compile(
    r"^\s*(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER)|"
    r"ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX|TRIGGER))\s+"
    r"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?[\"'`\[]?([^\s(\]`\"]+)",
    re.IGNORECASE,
)


def schema_snapshot(path):
    connection = sqlite3.connect(
        "file:{}?mode=ro".format(Path(path).resolve()), uri=True
    )
    try:
        rows = connection.execute(
            "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        serialized = json.dumps(
            rows, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return {
            "sha256": hashlib.sha256(serialized).hexdigest(),
            "objects": len(rows),
        }
    finally:
        connection.close()


def scalar(connection, statement, default=0):
    try:
        row = connection.execute(statement).fetchone()
    except sqlite3.Error:
        return default
    return row[0] if row is not None else default


def data_snapshot(root):
    result = {}
    definitions = {
        "catalog": (
            root / "catalog.db",
            {
                "products": "SELECT COUNT(*) FROM catalog_excel_products",
                "stock_sum": "SELECT COALESCE(SUM(stock),0) FROM catalog_excel_products WHERE active=1",
                "sales": "SELECT COUNT(*) FROM erp_sales",
                "sale_items": "SELECT COUNT(*) FROM erp_sale_items",
                "movements": "SELECT COUNT(*) FROM catalog_stock_movements",
                "receipts": "SELECT COUNT(*) FROM erp_receipts",
                "receipt_items": "SELECT COUNT(*) FROM erp_receipt_items",
                "comments": "SELECT COUNT(*) FROM erp_order_comments",
                "active_inventories": "SELECT COUNT(*) FROM erp_inventory_sessions WHERE status='active'",
                "active_inventory_items": "SELECT COUNT(*) FROM erp_inventory_items WHERE session_id IN (SELECT id FROM erp_inventory_sessions WHERE status='active')",
            },
        ),
        "orders": (
            root / "orders.db",
            {
                "orders": "SELECT COUNT(*) FROM orders_snapshot",
                "order_item_units": "SELECT COALESCE(SUM(item_units),0) FROM orders_snapshot",
                "customers": "SELECT COUNT(*) FROM customers",
            },
        ),
        "auth": (
            root / "auth.db",
            {
                "users": "SELECT COUNT(*) FROM users",
                "sessions": "SELECT COUNT(*) FROM auth_sessions",
            },
        ),
    }
    for name, (path, queries) in definitions.items():
        connection = sqlite3.connect(
            "file:{}?mode=ro".format(path.resolve()), uri=True
        )
        try:
            result[name] = {
                key: scalar(connection, statement)
                for key, statement in sorted(queries.items())
            }
            result[name]["quick_check"] = scalar(
                connection, "PRAGMA quick_check", "failed"
            )
            result[name]["foreign_key_violations"] = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        finally:
            connection.close()
    orders_connection = sqlite3.connect(
        "file:{}?mode=ro".format((root / "orders.db").resolve()), uri=True
    )
    try:
        positions = 0
        for row in orders_connection.execute(
            "SELECT payload_json FROM orders_snapshot"
        ).fetchall():
            try:
                payload = json.loads(row[0] or "{}")
            except (TypeError, ValueError):
                payload = {}
            items = payload.get("products") or payload.get("items") or []
            positions += len(items) if isinstance(items, list) else 0
        result["orders"]["order_positions"] = positions
    finally:
        orders_connection.close()
    for label, filename in (
        ("receipts_json", "receipts.json"),
        ("repairs_json", "repair_cases.json"),
    ):
        path = root / filename
        if not path.exists():
            result[label] = None
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        result[label] = len(payload) if isinstance(payload, (list, dict)) else None
    return result


def ledger_snapshot(path):
    connection = sqlite3.connect(
        "file:{}?mode=ro".format(Path(path).resolve()), uri=True
    )
    try:
        return connection.execute(
            "SELECT migration_id,checksum,state,COALESCE(app_commit,'') "
            "FROM erp_migration_ledger ORDER BY migration_id"
        ).fetchall()
    finally:
        connection.close()


def sanitize_statement(statement):
    normalized = " ".join(str(statement or "").split())
    match = OBJECT_PATTERN.match(normalized)
    if match:
        return "{} {}".format(match.group(1).upper(), match.group(2))
    operation = normalized.split(" ", 1)[0].upper() if normalized else "UNKNOWN"
    return operation


def worker(worker_id, root, queue):
    statements = []
    original_connect = sqlite3.connect
    original_socket_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)

        def trace(statement):
            normalized = str(statement or "").lstrip()
            if DDL_PATTERN.match(normalized):
                statements.append(sanitize_statement(normalized))
            elif normalized.upper().startswith(("BEGIN", "COMMIT", "ROLLBACK")):
                statements.append(normalized.split(" ", 1)[0].upper())

        connection.set_trace_callback(trace)
        return connection

    def blocked_connect(unused_socket, unused_address):
        raise OSError("runtime DDL audit blocks network egress")

    def blocked_getaddrinfo(*unused_args, **unused_kwargs):
        raise OSError("runtime DDL audit blocks DNS egress")

    try:
        os.environ["CATALOG_DATABASE_PATH"] = str(root / "catalog.db")
        os.environ["ORDERS_DATABASE_PATH"] = str(root / "orders.db")
        os.environ["ERP_AUTH_DATABASE"] = str(root / "auth.db")
        os.environ["ERP_SESSION_COOKIE_SECURE"] = "0"
        sqlite3.connect = traced_connect
        socket.socket.connect = blocked_connect
        socket.getaddrinfo = blocked_getaddrinfo

        from app import web
        from app.auth import AuthStore
        from app.catalog_db import CatalogDatabase
        from app.services.customer_identity import CustomerStore
        from app.services.order_comments import OrderCommentsService
        from app.services.orders_snapshot import OrdersSnapshotStore

        phase_counts = {}
        phase_counts["application_import"] = len(statements)
        AuthStore(root / "auth.db").get_user(None)
        phase_counts["http_auth_setup"] = len(statements)
        OrdersSnapshotStore(root / "orders.db").initialize()
        OrdersSnapshotStore(root / "orders.db").initialize()
        CustomerStore(OrdersSnapshotStore(root / "orders.db")).list()
        phase_counts["orders_request_and_background"] = len(statements)
        catalog = CatalogDatabase(root / "catalog.db", cache_initialization=False)
        catalog.initialize()
        catalog.initialize()
        OrderCommentsService(catalog, client_factory=lambda: None).list(
            "runtime-ddl-audit"
        )
        phase_counts["catalog_services"] = len(statements)
        queue.put(
            {
                "worker": worker_id,
                "ok": True,
                "sqlite_version": sqlite3.sqlite_version,
                "python_version": sys.version.split()[0],
                "statements": statements,
                "phase_statement_totals": phase_counts,
                "web_app": bool(web.app),
            }
        )
    except Exception as error:
        queue.put(
            {
                "worker": worker_id,
                "ok": False,
                "error": "{}: {}".format(type(error).__name__, error),
                "traceback": traceback.format_exc(),
                "statements": statements,
            }
        )
    finally:
        sqlite3.connect = original_connect
        socket.socket.connect = original_socket_connect
        socket.getaddrinfo = original_getaddrinfo


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--copy-root", required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--expected-python", default="")
    parser.add_argument("--expected-sqlite", default="")
    parser.add_argument("--report")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    root = Path(arguments.copy_root).resolve()
    required = [root / name for name in ("catalog.db", "orders.db", "auth.db")]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Missing rehearsal copies: {}".format(", ".join(missing)))
    if not (root / ".catalog-schema-preflight-required").is_file():
        raise SystemExit("Catalog runtime guard sentinel is missing from copy")
    if not (root / ".catalog-schema-state.json").is_file():
        raise SystemExit("Catalog runtime guard marker is missing from copy")

    schemas_before = {
        path.name: schema_snapshot(path) for path in required
    }
    data_before = data_snapshot(root)
    ledger_before = ledger_snapshot(root / "catalog.db")

    queue = multiprocessing.Queue()
    workers = [
        multiprocessing.Process(target=worker, args=(index + 1, root, queue))
        for index in range(max(1, arguments.workers))
    ]
    for process in workers:
        process.start()
    results = [queue.get(timeout=90) for unused in workers]
    for process in workers:
        process.join(90)
        if process.is_alive():
            process.terminate()
            process.join()

    schemas_after = {
        path.name: schema_snapshot(path) for path in required
    }
    data_after = data_snapshot(root)
    ledger_after = ledger_snapshot(root / "catalog.db")
    report = {
        "copy_root": str(root),
        "workers": sorted(results, key=lambda item: item["worker"]),
        "schemas_before": schemas_before,
        "schemas_after": schemas_after,
        "schema_unchanged": schemas_before == schemas_after,
        "ledger_unchanged": ledger_before == ledger_after,
        "data_before": data_before,
        "data_after": data_after,
        "business_data_unchanged": data_before == data_after,
        "all_workers_ok": all(item.get("ok") for item in results),
        "network_egress_blocked": True,
    }
    versions = {
        (item.get("python_version"), item.get("sqlite_version"))
        for item in results if item.get("ok")
    }
    report["runtime_versions"] = sorted(list(versions))
    report["expected_runtime"] = {
        "python": arguments.expected_python,
        "sqlite": arguments.expected_sqlite,
    }
    report["runtime_matches"] = all(
        (not arguments.expected_python or python == arguments.expected_python)
        and (not arguments.expected_sqlite or sqlite == arguments.expected_sqlite)
        for python, sqlite in versions
    ) and bool(versions)

    output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(output)
    if arguments.report:
        Path(arguments.report).write_text(output + "\n", encoding="utf-8")
    if not (
        report["all_workers_ok"]
        and report["runtime_matches"]
        and report["schema_unchanged"]
        and report["ledger_unchanged"]
        and report["business_data_unchanged"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
