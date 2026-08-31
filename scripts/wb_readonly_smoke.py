#!/usr/bin/env python3
"""Production-safe WB auth, GET-only endpoints and matching smoke."""

from __future__ import print_function

import argparse
import json
import os
import stat
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from app.clients.wildberries_orders import (
    WildberriesReadOnlyClient,
    WildberriesReadOnlyError,
)
from app.services.wildberries_matching import (
    build_matching_report,
    load_erp_product_index,
)


def _safe_call(name, callback):
    try:
        value = callback()
        return value, {"ok": True, "count": len(value) if isinstance(value, list) else 1}
    except WildberriesReadOnlyError as error:
        return [], {"ok": False, "code": error.code}


def _write_report(path, payload):
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(str(target), flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    if stat.S_IMODE(target.stat().st_mode) != 0o600:
        os.chmod(str(target), 0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="instance/catalog.db")
    parser.add_argument("--matching-report")
    parser.add_argument("--require-all-authorized", action="store_true")
    parser.add_argument("--production", action="store_true")
    arguments = parser.parse_args()

    client = WildberriesReadOnlyClient(os.getenv("WB_API_TOKEN"))
    probes = client.probe_services()
    prices, prices_status = _safe_call("prices", lambda: client.get_prices(limit=1000))
    orders, orders_status = _safe_call("orders", client.get_new_orders)
    supplies, supplies_status = _safe_call(
        "supplies", lambda: client.get_supplies(limit=100).get("supplies", [])
    )
    warehouses, warehouses_status = _safe_call("warehouses", client.get_warehouses)
    statistics, statistics_status = _safe_call(
        "statistics",
        lambda: client.get_statistics_stocks(
            (date.today() - timedelta(days=1)).isoformat()
        ),
    )
    analytics, analytics_status = _safe_call(
        "analytics", client.get_analytics_downloads
    )

    if any(item["method"] != "GET" for item in client.request_audit):
        print("WB_SMOKE_FAILED=write-method", file=sys.stderr)
        return 1

    report = []
    if arguments.matching_report:
        report = build_matching_report(
            prices,
            statistics,
            orders,
            load_erp_product_index(arguments.catalog),
        )
        _write_report(arguments.matching_report, report)

    summary = {
        "analytics": analytics_status,
        "auth": probes,
        "content": {"ok": probes.get("content", {}).get("available", False), "data": "POST_BLOCKED"},
        "matching": {
            "ambiguous": sum(row["status"] == "ambiguous" for row in report),
            "matched": sum(row["status"] == "matched" for row in report),
            "not_found": sum(row["status"] == "not_found" for row in report),
            "rows": len(report),
        },
        "orders": orders_status,
        "prices": prices_status,
        "statistics": statistics_status,
        "stocks": {"ok": bool(statistics_status.get("ok")), "fbs_data": "POST_BLOCKED"},
        "supplies": supplies_status,
        "warehouses": warehouses_status,
        "writes": 0,
    }
    required = (prices_status, orders_status, supplies_status, warehouses_status)
    if arguments.require_all_authorized and (
        not all(item.get("available") for item in probes.values())
        or not all(item.get("ok") for item in required)
    ):
        print("WB_SMOKE_FAILED={}".format(json.dumps(summary, sort_keys=True)), file=sys.stderr)
        return 1
    if arguments.production:
        successful_reads = sum(item.get("ok", False) for item in (
            prices_status, orders_status, supplies_status, warehouses_status,
            statistics_status, analytics_status,
        ))
        if not probes.get("common", {}).get("available") or successful_reads < 2:
            print("WB_SMOKE_FAILED=insufficient-read-access", file=sys.stderr)
            return 1
        token_bytes = os.getenv("WB_API_TOKEN", "").encode("utf-8")
        if not token_bytes:
            print("WB_SMOKE_FAILED=secret-missing", file=sys.stderr)
            return 1
        journal = subprocess.check_output([
            "journalctl", "-u", "clock-erp", "--since", "-10 minutes",
            "--no-pager", "--quiet",
        ])
        if token_bytes in journal:
            print("WB_SMOKE_FAILED=secret-in-journal", file=sys.stderr)
            return 1
        for url in (
            "http://127.0.0.1:5000/login",
            "http://127.0.0.1:5000/register",
        ):
            response = client.session.get(url, timeout=(3.05, 10))
            if response.status_code != 200 or token_bytes in response.content:
                print("WB_SMOKE_FAILED=http-secret-check", file=sys.stderr)
                return 1
    print("WB_SMOKE_OK={}".format(json.dumps(summary, sort_keys=True, separators=(",", ":"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
