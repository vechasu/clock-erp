#!/usr/bin/env python3
"""Resumable, idempotent Bitrix order-history import into the ERP snapshot."""

from __future__ import print_function

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.clients.bitrix_orders import (  # noqa: E402
    BitrixOrdersReadOnlyClient,
    BitrixReadOnlyError,
    normalize_order as normalize_bitrix_order,
)
from app.services.orders_snapshot import OrdersSnapshotStore  # noqa: E402


def normalize_order(raw):
    normalized = normalize_bitrix_order(raw)
    if normalized is None or not normalized.get("external_id"):
        return None
    result = dict(raw)
    result.update(normalized)
    result.update({
        "id": normalized["external_id"],
        "number": normalized.get("number") or normalized["external_id"],
        "date": normalized.get("created_at"),
        "price": normalized.get("total"),
        "source": "tictactoy",
        "source_name": "Tictactoy",
        "track_number": normalized.get("tracking") or "",
        "paid_name": (
            "Оплачен" if str(normalized.get("paid") or "").upper() == "Y"
            else "Не оплачен"
        ),
    })
    return result


def sqlite_backup(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source))
    destination_connection = sqlite3.connect(str(destination))
    try:
        if hasattr(source_connection, "backup"):
            source_connection.backup(destination_connection)
        else:  # Python 3.6 production compatibility.
            destination_connection.close()
            source_connection.execute("BEGIN IMMEDIATE")
            try:
                import shutil
                shutil.copy2(str(source), str(destination))
            finally:
                source_connection.rollback()
            destination_connection = sqlite3.connect(str(destination))
        if destination_connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("orders backup verification failed")
    finally:
        destination_connection.close()
        source_connection.close()


def orders_counts(path):
    connection = sqlite3.connect(str(path))
    try:
        total = int(connection.execute(
            "SELECT COUNT(*) FROM orders_snapshot"
        ).fetchone()[0])
        sources = dict(connection.execute(
            "SELECT source, COUNT(*) FROM orders_snapshot GROUP BY source"
        ).fetchall())
        duplicates = int(connection.execute(
            "SELECT COUNT(*) FROM (SELECT source, external_order_id "
            "FROM orders_snapshot GROUP BY source, external_order_id "
            "HAVING COUNT(*) > 1)"
        ).fetchone()[0])
        return {
            "orders": total,
            "tictactoy": int(sources.get("tictactoy", 0)),
            "wildberries": int(sources.get("wildberries", 0)),
            "duplicates": duplicates,
        }
    finally:
        connection.close()


def sales_evidence(path):
    empty = {"sales": 0, "conducted": 0, "order_links": 0, "links_digest": ""}
    if not path.exists():
        return empty
    connection = sqlite3.connect(str(path))
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "erp_sales" not in tables:
            return empty
        rows = connection.execute(
            "SELECT id, source, external_order_id, status, cancelled_at, deleted_at "
            "FROM erp_sales ORDER BY id"
        ).fetchall()
        links = [
            "{}:{}:{}".format(row[0], row[1] or "", row[2] or "")
            for row in rows if row[2] not in (None, "")
        ]
        return {
            "sales": len(rows),
            "conducted": sum(
                1 for row in rows
                if row[3] in {"completed", "partially_returned"}
                and not row[4] and not row[5]
            ),
            "order_links": len(links),
            "links_digest": hashlib.sha256(
                "\n".join(links).encode("utf-8")
            ).hexdigest(),
        }
    except sqlite3.Error:
        return empty
    finally:
        connection.close()


def existing_payloads(store):
    with store.connection() as connection:
        return {
            str(row["external_order_id"]): json.loads(row["payload_json"])
            for row in connection.execute(
                "SELECT external_order_id, payload_json FROM orders_snapshot "
                "WHERE source='tictactoy'"
            ).fetchall()
        }


def dry_run_action(store, existing, order):
    order_id = str(order.get("id") or "")
    previous = existing.get(order_id)
    if previous is None:
        existing[order_id] = order
        return "added"
    merged = store._merge_bitrix_payload(
        previous, order, preserve_existing_local=True
    )
    merged.update({
        "id": order_id,
        "external_id": order_id,
        "source": "tictactoy",
        "source_name": merged.get("source_name") or "Tictactoy",
    })
    previous_canonical = dict(previous)
    previous_canonical.setdefault("id", order_id)
    previous_canonical.setdefault("external_id", order_id)
    previous_canonical["source"] = "tictactoy"
    previous_canonical["source_name"] = (
        previous_canonical.get("source_name") or "Tictactoy"
    )
    existing[order_id] = merged
    return "skipped" if merged == previous_canonical else "updated"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database", type=Path, default=ROOT / "instance" / "orders.db"
    )
    parser.add_argument(
        "--catalog-database", type=Path,
        default=ROOT / "instance" / "catalog.db",
    )
    parser.add_argument(
        "--url", default=os.getenv(
            "BITRIX_ORDERS_HISTORY_URL",
            "http://127.0.0.1:81/api/orders-export.php",
        )
    )
    parser.add_argument("--host", default="tictactoy.ru")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument(
        "--backup-dir", type=Path,
        default=ROOT / "instance" / "backups",
    )
    args = parser.parse_args(argv)

    store = OrdersSnapshotStore(args.database).initialize()
    lock_path = args.database.with_suffix(".bitrix-history.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("BITRIX_ORDERS_BACKFILL=already_running")
        return 2

    before = orders_counts(args.database)
    sales_before = sales_evidence(args.catalog_database)
    checkpoint = store.history_checkpoint()
    if args.apply and checkpoint["complete"] and not args.restart:
        print("BITRIX_ORDERS_BACKFILL=" + json.dumps({
            "mode": "apply", "status": "already_complete", "before": before,
            "after": before, "sales_before": sales_before,
            "sales_after": sales_before,
        }, ensure_ascii=False, sort_keys=True))
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        return 0

    backup_path = None
    if args.apply:
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = args.backup_dir / "orders-before-bitrix-history-{}.db".format(
            int(time.time())
        )
        sqlite_backup(args.database, backup_path)

    session = requests.Session()
    session.headers.update({"Host": args.host})
    client = BitrixOrdersReadOnlyClient(
        history_url=args.url,
        token=os.getenv("BITRIX_ORDERS_TOKEN"),
        max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
        session=session,
    )
    start_cursor = 0
    if args.apply and not args.restart:
        start_cursor = checkpoint["cursor"]
    actions = Counter()
    available = 0
    pages = 0
    seen = set()
    existing = existing_payloads(store) if not args.apply else None
    try:
        for page in client.history_pages(args.limit, start_cursor=start_cursor):
            pages += 1
            normalized = []
            for raw in page["orders"]:
                order = normalize_order(raw)
                if order is None:
                    actions["invalid"] += 1
                    continue
                identity = str(order["id"])
                if identity in seen:
                    actions["duplicate_in_export"] += 1
                    continue
                seen.add(identity)
                normalized.append(order)
            available += len(normalized)
            if args.apply:
                result = store.upsert_bitrix(
                    normalized,
                    time.time(),
                    checkpoint_cursor=page["next_cursor"],
                    checkpoint_complete=not page["has_more"],
                    preserve_existing_local=True,
                )
                actions.update(result)
            else:
                for order in normalized:
                    actions[dry_run_action(store, existing, order)] += 1
    except BitrixReadOnlyError as error:
        print("BITRIX_ORDERS_BACKFILL=" + json.dumps({
            "mode": "apply" if args.apply else "dry_run",
            "status": "failed", "error": str(error), "pages_completed": pages,
            "orders_read": available, "actions": dict(actions),
        }, ensure_ascii=False, sort_keys=True))
        return 1
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

    after = orders_counts(args.database)
    sales_after = sales_evidence(args.catalog_database)
    result = {
        "mode": "apply" if args.apply else "dry_run",
        "status": "complete",
        "bitrix_available": available,
        "pages": pages,
        "before": before,
        "actions": dict(actions),
        "after": after,
        "sales_before": sales_before,
        "sales_after": sales_after,
        "sales_links_preserved": sales_before == sales_after,
    }
    if backup_path is not None:
        result["backup"] = str(backup_path)
    print("BITRIX_ORDERS_BACKFILL=" + json.dumps(
        result, ensure_ascii=False, sort_keys=True
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
