#!/usr/bin/env python3
"""Dry-run and apply the additive Customers migration for orders.db."""

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.customer_identity import analyze_orders, backfill_customers  # noqa: E402
from app.domain_schema_migrations import apply_domain_migrations  # noqa: E402
from app.services.orders_snapshot import OrdersSnapshotStore  # noqa: E402


def read_orders(path):
    if not path.exists():
        return []
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='orders_snapshot'"
        ).fetchone()
        if not table:
            return []
        rows = connection.execute(
            "SELECT order_id, source, payload_json FROM orders_snapshot ORDER BY order_id"
        ).fetchall()
        orders = []
        for row in rows:
            order = json.loads(row["payload_json"])
            order.setdefault("id", row["order_id"])
            order.setdefault("source", row["source"])
            orders.append(order)
        return orders
    finally:
        connection.close()


def snapshot_ids(path):
    connection = sqlite3.connect(str(path))
    try:
        return [row[0] for row in connection.execute(
            "SELECT order_id FROM orders_snapshot ORDER BY order_id"
        ).fetchall()]
    finally:
        connection.close()


def backup_database(path, backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / "orders-before-customers-{}.db".format(stamp)
    source = sqlite3.connect(str(path))
    try:
        backup_method = getattr(source, "backup", None)
        if backup_method is not None:
            destination = sqlite3.connect(str(target))
            try:
                backup_method(destination)
            finally:
                destination.close()
        else:
            # Python 3.6 does not expose Connection.backup(). The deploy flow
            # stops the service before --apply; checkpoint any WAL pages before
            # making the byte-for-byte fallback copy.
            source.execute("PRAGMA wal_checkpoint(FULL)").fetchall()
    finally:
        source.close()
    if not target.exists():
        shutil.copy2(str(path), str(target))
    target.chmod(0o600)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "instance" / "orders.db")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    orders = read_orders(args.database)
    dry_report = analyze_orders(orders)["report"]
    print("CUSTOMERS_DRY_RUN=" + json.dumps(dry_report, ensure_ascii=False, sort_keys=True))
    if not args.apply:
        return 0
    if not args.database.exists():
        print("CUSTOMERS_MIGRATION=skipped_missing_database")
        return 0
    if not args.backup_dir:
        parser.error("--backup-dir is required with --apply")

    before_ids = snapshot_ids(args.database)
    backup = backup_database(args.database, args.backup_dir)
    apply_domain_migrations(args.database, "orders")
    store = OrdersSnapshotStore(args.database)
    store.initialize()
    with store.connection() as connection:
        result = backfill_customers(connection)
    after_ids = snapshot_ids(args.database)
    if before_ids != after_ids:
        raise RuntimeError("Order identifiers changed during customer backfill")
    with store.connection() as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    if quick_check != "ok":
        raise RuntimeError("SQLite quick_check failed: {}".format(quick_check))
    result["orders_total_before"] = len(before_ids)
    result["orders_total_after"] = len(after_ids)
    result["backup"] = str(backup)
    result["quick_check"] = quick_check
    print("CUSTOMERS_BACKFILL=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    print("CUSTOMERS_MIGRATION=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
