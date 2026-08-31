#!/usr/bin/env python3
"""Production smoke for the GET-only WB FBS assembly workflow."""

from __future__ import print_function

import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.wildberries_orders import WildberriesOrdersReadOnlyClient  # noqa: E402
from app.services.orders_snapshot import OrdersSnapshotStore  # noqa: E402
from app.services.wildberries_orders import synchronize_wildberries_orders  # noqa: E402


def _http_body(url):
    with urlopen(url, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("HTTP {} returned {}".format(url, response.status))
        return response.read()


def _application_body(path):
    """Exercise an authenticated ERP route without production credentials."""
    from app.web import app

    previous_testing = app.config.get("TESTING")
    previous_auth_testing = app.config.get("AUTH_TESTING")
    app.config["TESTING"] = True
    app.config["AUTH_TESTING"] = False
    try:
        response = app.test_client().get(path)
    finally:
        app.config["TESTING"] = previous_testing
        app.config["AUTH_TESTING"] = previous_auth_testing
    if response.status_code != 200:
        raise RuntimeError("HTTP {} returned {}".format(
            path, response.status_code
        ))
    return response.data


def main():
    if os.getenv("ERP_PRODUCTION_WB_FBS_SMOKE") != "confirmed":
        raise RuntimeError("production WB FBS smoke requires explicit confirmation")
    token = os.getenv("WB_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WB_API_TOKEN is unavailable")

    store = OrdersSnapshotStore(os.getenv(
        "ORDERS_DATABASE_PATH", str(PROJECT_ROOT / "instance" / "orders.db")
    ))
    client = WildberriesOrdersReadOnlyClient(token)
    first = synchronize_wildberries_orders(client, store)
    second = synchronize_wildberries_orders(client, store)
    if second["added"] != 0:
        raise RuntimeError("repeated WB sync created duplicates")
    if any(item.get("method") != "GET" for item in client.request_audit):
        raise RuntimeError("non-GET Wildberries request detected")

    with store.connection() as connection:
        duplicate_count = int(connection.execute(
            "SELECT COUNT(*) FROM (SELECT external_order_id "
            "FROM orders_snapshot WHERE source='wildberries' "
            "GROUP BY external_order_id HAVING COUNT(*) > 1)"
        ).fetchone()[0])
        stored_count = int(connection.execute(
            "SELECT COUNT(*) FROM orders_snapshot WHERE source='wildberries'"
        ).fetchone()[0])
    if duplicate_count:
        raise RuntimeError("duplicate Wildberries order IDs detected")

    login = _http_body("http://127.0.0.1:5000/login")
    register = _http_body("http://127.0.0.1:5000/register")
    assembly = _application_body("/sales?view=assembly")
    token_bytes = token.encode("utf-8")
    if token_bytes in login or token_bytes in register or token_bytes in assembly:
        raise RuntimeError("WB token exposed over HTTP")
    if b"data-wb-assembly-list" not in assembly:
        raise RuntimeError("Sales assembly workspace is unavailable")

    result = {
        "received": first["received"],
        "added": first["added"],
        "updated": first["updated"],
        "repeat_added": second["added"],
        "stored": stored_count,
        "duplicates": duplicate_count,
        "wb_writes": 0,
        "http": {"login": 200, "register": 200, "sales_assembly": 200},
    }
    print("WB_FBS_SALES_SMOKE_OK={}".format(
        json.dumps(result, ensure_ascii=False, sort_keys=True)
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
