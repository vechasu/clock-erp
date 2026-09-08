#!/usr/bin/env python3
"""Audit order/card links; add only missing links with two agreeing contacts."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.customer_registry import CustomerRegistry, normalize_email, normalize_phone
from scripts.backfill_customers import BitrixHistoryClient, order_operation


def read_only(path):
    connection = sqlite3.connect("file:{}?mode=ro".format(Path(path).resolve()), uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def audit(connection, orders):
    contacts = defaultdict(set)
    for row in connection.execute(
            "SELECT cc.* FROM customer_contacts cc JOIN customers c ON c.id=cc.customer_id "
            "WHERE cc.masked=0 AND c.merged_into_id IS NULL"):
        contacts[row["kind"], row["normalized_value"]].add(row["customer_id"])
    operations = {(r["source"], r["external_id"]): r["customer_id"] for r in connection.execute(
        "SELECT source,external_id,customer_id FROM customer_operations WHERE operation_type='order'")}
    counts, plan, mismatches = Counter(), [], []
    for identity, row in sorted(orders.items()):
        counts["orders_checked"] += 1
        phone = contacts["phone", normalize_phone(row.get("phone"))]
        email = contacts["email", normalize_email(row.get("email"))]
        candidates = phone | email
        if len(candidates) > 1:
            counts["ambiguous_contacts"] += 1
        if identity not in operations:
            counts["missing_registry"] += 1
            # A match on a single/reused contact is diagnostic, not proof for repair.
            if phone == email and len(phone) == 1 and identity[0] == "tictactoy":
                plan.append({"source": identity[0], "order_id": identity[1],
                             "customer_id": next(iter(phone))})
        elif candidates and operations[identity] not in candidates:
            mismatches.append({"source": identity[0], "order_id": identity[1],
                               "current_customer_id": operations[identity],
                               "candidate_customer_ids": sorted(candidates)})
    return {"counts": dict(counts), "missing_proven_links": plan,
            "mismatches_for_review": mismatches}


def repair(registry, orders, expected_plan, backup_dir):
    with registry.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        plan = audit(connection, orders)["missing_proven_links"]
        if plan != expected_plan:
            raise RuntimeError("Identity evidence changed; rerun the audit")
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / "customers-before-order-links-{}.db".format(int(time.time() * 1000000))
        # Production uses Python 3.6, before Connection.backup was available.
        subprocess.run(["sqlite3", str(registry.path),
                        ".backup '{}'".format(str(backup).replace("'", "''"))], check=True)
        target = read_only(backup)
        try:
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Customer backup failed verification")
        finally:
            target.close()
        backup.chmod(0o600)
        before = [tuple(r) for r in connection.execute("SELECT * FROM customer_operations ORDER BY id")]
        for link in plan:
            row = orders[link["source"], link["order_id"]]
            result = registry.upsert_operation(connection, order_operation(row, "erp"))
            if result["action"] != "matched" or result["customer_id"] != link["customer_id"]:
                raise RuntimeError("Matcher disagreed with evidence; transaction rolled back")
        after = {r[0]: tuple(r) for r in connection.execute("SELECT * FROM customer_operations")}
        if any(after[r[0]] != r for r in before):
            raise RuntimeError("Existing operation changed; transaction rolled back")
        if plan:
            registry.recompute(connection)
        return str(backup)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "instance/customers.db")
    parser.add_argument("--orders-database", type=Path, default=ROOT / "instance/orders.db")
    parser.add_argument("--history-url", default="http://127.0.0.1:81/api/orders-export.php")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "instance/backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    orders = {}
    with read_only(args.orders_database) as connection:
        for row in connection.execute("SELECT order_id,source,external_order_id,payload_json FROM orders_snapshot"):
            payload = json.loads(row["payload_json"])
            payload["id"] = row["external_order_id"] or row["order_id"]
            orders[row["source"], payload["id"]] = payload
    history_count = lost_contacts = 0
    for rows, _cursor in BitrixHistoryClient(args.history_url).pages(limit=200):
        for row in rows:
            history_count += 1
            identity = ("tictactoy", str(row["id"]))
            previous = orders.get(identity)
            if previous and (row.get("phone") or row.get("email")) and not (
                    previous.get("phone") or previous.get("email")):
                lost_contacts += 1
            orders[identity] = row
    with read_only(args.database) as connection:
        report = audit(connection, orders)
    report.update(history_orders=history_count, snapshots_missing_contacts=lost_contacts,
                  mode="apply" if args.apply else "read_only")
    if args.apply:
        report["backup"] = repair(CustomerRegistry(args.database), orders,
                                  report["missing_proven_links"], args.backup_dir)
        report["repaired"] = len(report["missing_proven_links"])
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
