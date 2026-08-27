#!/usr/bin/env python3
"""Idempotently build the customer registry from read-only operation sources."""

from __future__ import print_function

import argparse
import fcntl
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

try:
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib2 import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.customer_registry import CustomerRegistry, migrate_database  # noqa: E402


def clean_amount(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def order_operation(row, origin="bitrix"):
    order_id = str(row.get("id") or row.get("ID") or "").strip()
    status = str(row.get("status") or row.get("STATUS_ID") or "").strip().upper()
    cancelled = bool(row.get("cancelled")) or status == "C"
    return {
        "operation_type": "order", "source": "tictactoy", "external_id": order_id,
        "external_customer_id": row.get("external_customer_id") or row.get("user_id"),
        "local_ref": order_id if origin == "erp" else "",
        "name": row.get("customer") or row.get("client") or row.get("name"),
        "phone": row.get("phone"), "email": row.get("email"), "city": row.get("city"),
        "status": status, "occurred_at": row.get("created_at") or row.get("date") or "",
        "amount": clean_amount(row.get("order_total") if row.get("order_total") is not None else row.get("price")),
        "completed": status in {"D", "F"} and not cancelled,
        "cancelled": cancelled, "active": True,
        "payload": {"number": row.get("number") or order_id, "origin": origin},
    }


class BitrixHistoryClient(object):
    def __init__(self, url, host="tictactoy.ru", retries=3):
        self.url, self.host, self.retries = url, host, retries

    def pages(self, limit=200, start_cursor=0):
        cursor = int(start_cursor or 0)
        while True:
            separator = "&" if "?" in self.url else "?"
            url = self.url + separator + "limit={}&cursor={}".format(limit, cursor)
            error = None
            for attempt in range(self.retries + 1):
                try:
                    request = Request(url, headers={"Host": self.host, "Accept": "application/json"})
                    response = urlopen(request, timeout=30)
                    payload = json.loads(response.read().decode("utf-8"))
                    error = None
                    break
                except Exception as caught:
                    error = caught
                    if attempt < self.retries:
                        time.sleep(min(0.5 * (2 ** attempt), 4))
            if error is not None:
                raise RuntimeError("Bitrix history read failed: {}".format(type(error).__name__))
            rows = payload.get("orders")
            if not isinstance(rows, list):
                raise RuntimeError("Bitrix history response is invalid")
            yield rows, payload.get("next_cursor")
            if not payload.get("has_more"):
                break
            next_cursor = int(payload.get("next_cursor") or 0)
            if next_cursor <= 0 or next_cursor == cursor:
                raise RuntimeError("Bitrix pagination cursor did not advance")
            cursor = next_cursor


def read_local_orders(path):
    if not path.exists():
        return []
    connection = sqlite3.connect(str(path))
    try:
        rows = connection.execute("SELECT order_id,payload_json FROM orders_snapshot ORDER BY order_id").fetchall()
        result = []
        for order_id, payload in rows:
            try:
                row = json.loads(payload)
            except (TypeError, ValueError):
                continue
            row.setdefault("id", order_id)
            result.append(order_operation(row, "erp"))
        return result
    finally:
        connection.close()


def read_sales(path):
    if not path.exists():
        return []
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id,source,status,created_at,metadata_json,external_order_id,cancelled_at,deleted_at FROM erp_sales ORDER BY id"
        ).fetchall()
        result = []
        for stored in rows:
            try:
                data = json.loads(stored["metadata_json"] or "{}")
            except (TypeError, ValueError):
                data = {}
            source = str(stored["source"] or data.get("source") or "erp").strip().casefold()
            external_order_id = str(stored["external_order_id"] or data.get("order_number") or "").strip()
            cancelled = bool(stored["cancelled_at"] or stored["deleted_at"])
            amount = data.get("total_amount")
            if amount is None and data.get("unit_price") is not None:
                try:
                    amount = float(data.get("unit_price")) * float(data.get("quantity") or 1)
                except (TypeError, ValueError):
                    amount = None
            result.append({
                "operation_type": "sale", "source": source or "erp", "external_id": str(stored["id"]),
                "related_order_source": "tictactoy" if source == "tictactoy" and external_order_id else "",
                "related_order_id": external_order_id, "local_ref": str(stored["id"]),
                "name": data.get("customer") or data.get("client_name") or data.get("recipient_name") or data.get("recipient"),
                "phone": data.get("phone") or data.get("client_phone"),
                "email": data.get("email") or data.get("client_email"), "city": data.get("city"),
                "status": stored["status"], "occurred_at": stored["created_at"], "amount": clean_amount(amount),
                "completed": not cancelled and stored["status"] in {"completed", "partially_returned"},
                "cancelled": cancelled, "active": not bool(stored["deleted_at"]),
                "payload": {"order_number": external_order_id},
            })
        return result
    finally:
        connection.close()


def load_json_compat(path):
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, ValueError):
            continue
    raise RuntimeError("local JSON source is unreadable")


def read_repairs(path):
    if not path.exists():
        return []
    payload = load_json_compat(path)
    rows = payload.get("items") or payload.get("cases") or [] if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        repair_id = str(row.get("id") or "repair-{}".format(index))
        order_id = str(row.get("order_id") or row.get("order_number") or "").strip()
        status = str(row.get("status") or "").strip().casefold()
        result.append({
            "operation_type": "repair", "source": "erp-repair", "external_id": repair_id,
            "related_order_source": "tictactoy" if order_id else "", "related_order_id": order_id,
            "local_ref": repair_id, "name": row.get("client_name"), "phone": row.get("client_phone"),
            "email": row.get("client_email"), "city": row.get("city"), "status": status,
            "occurred_at": row.get("created_at") or row.get("request_at") or "",
            "amount": clean_amount(row.get("payment_amount") or row.get("agreed_cost")),
            "completed": status == "completed", "cancelled": status == "cancelled", "active": True,
            "payload": {"order_number": order_id},
        })
    return result


def registry_counts(path):
    if not path.exists():
        return {"customers": 0, "operations": 0, "conflicts": 0}
    connection = sqlite3.connect(str(path))
    try:
        return {
            "customers": int(connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]),
            "operations": int(connection.execute("SELECT COUNT(*) FROM customer_operations").fetchone()[0]),
            "conflicts": int(connection.execute("SELECT COUNT(*) FROM customer_identity_conflicts").fetchone()[0]),
        }
    finally:
        connection.close()


def process(registry, operations, report, failure_after=0):
    with registry.connection() as connection:
        for operation in operations:
            result = registry.upsert_operation(connection, operation)
            report[result["action"]] += 1
            report["matched_" + result.get("matched_by", "unknown")] += int(
                result["action"] in {"matched", "updated"}
            )
            if (not operation.get("external_customer_id") and not operation.get("phone")
                    and not operation.get("email")):
                report["without_reliable_identity"] += 1
            if result.get("reason"):
                report["identity_conflicts"] += 1
            if failure_after and sum(report.values()) >= failure_after:
                raise RuntimeError("controlled failure")
        registry.recompute(connection)


def checkpoint(registry, cursor=None, complete=False):
    with registry.connection() as connection:
        state = {"complete": bool(complete), "cursor": int(cursor or 0), "updated_at": int(time.time())}
        connection.execute(
            "INSERT OR REPLACE INTO registry_meta(key,value) VALUES('bitrix_backfill_checkpoint',?)",
            (json.dumps(state, sort_keys=True),),
        )


def resume_cursor(registry):
    with registry.connection() as connection:
        row = connection.execute(
            "SELECT value FROM registry_meta WHERE key='bitrix_backfill_checkpoint'"
        ).fetchone()
    if not row:
        return 0
    try:
        state = json.loads(row[0])
    except (TypeError, ValueError):
        return 0
    return 0 if state.get("complete") else int(state.get("cursor") or 0)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "instance/customers.db")
    parser.add_argument("--orders-database", type=Path, default=ROOT / "instance/orders.db")
    parser.add_argument("--catalog-database", type=Path, default=ROOT / "instance/catalog.db")
    parser.add_argument("--repairs", type=Path, default=ROOT / "instance/repair_cases.json")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "instance/backups")
    parser.add_argument("--bitrix-url", default="http://127.0.0.1:81/api/orders-export.php")
    parser.add_argument("--bitrix-host", default="tictactoy.ru")
    parser.add_argument("--skip-bitrix", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--failure-after", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    lock_path = args.database.with_suffix(".backfill.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("CUSTOMERS_BACKFILL=already_running")
        return 2

    target = args.database
    temporary_dir = None
    backup_path = None
    if args.apply and not args.no_backup:
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = args.backup_dir / "customers-before-backfill-{}.db".format(int(time.time()))
        if args.database.exists():
            shutil.copy2(str(args.database), str(backup_path))
        else:
            # A first installation has no registry file yet.  Keep a verified,
            # restorable empty-schema baseline before the first data write.
            migrate_database(backup_path)
        check = sqlite3.connect(str(backup_path))
        try:
            if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("customer registry backup verification failed")
        finally:
            check.close()
    if not args.apply:
        temporary_dir = Path(tempfile.mkdtemp(prefix="customers-dry-run-"))
        target = temporary_dir / "customers.db"
        if args.database.exists():
            shutil.copy2(str(args.database), str(target))
    before = registry_counts(target) if target.exists() else {"customers": 0, "operations": 0, "conflicts": 0}
    migrate_database(target)
    registry = CustomerRegistry(target)
    report = Counter()
    source_counts = Counter()
    try:
        if not args.skip_bitrix:
            start_cursor = resume_cursor(registry) if args.apply else 0
            for rows, next_cursor in BitrixHistoryClient(args.bitrix_url, args.bitrix_host).pages(start_cursor=start_cursor):
                operations = [order_operation(row) for row in rows if str(row.get("id") or "").strip()]
                source_counts["bitrix_orders"] += len(operations)
                process(registry, operations, report, args.failure_after)
                if args.apply:
                    checkpoint(registry, next_cursor, complete=not bool(next_cursor))
        local_orders = read_local_orders(args.orders_database)
        source_counts["erp_orders"] = len(local_orders)
        process(registry, local_orders, report, args.failure_after)
        sales = read_sales(args.catalog_database)
        source_counts["erp_sales"] = len(sales)
        process(registry, sales, report, args.failure_after)
        repairs = read_repairs(args.repairs)
        source_counts["erp_repairs"] = len(repairs)
        process(registry, repairs, report, args.failure_after)
        after = registry_counts(target)
        result = {
            "mode": "apply" if args.apply else "dry_run", "sources": dict(source_counts),
            "before": before, "after": after, "actions": dict(report),
            "unique_operations": after["operations"],
            "operation_duplicates": sum(source_counts.values()) - after["operations"],
            "excluded_test_operations": 0,
            "data_safety": "source_databases_read_only",
        }
        if backup_path is not None:
            result["backup"] = str(backup_path)
        print("CUSTOMERS_BACKFILL=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        if temporary_dir is not None:
            shutil.rmtree(str(temporary_dir), ignore_errors=True)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
