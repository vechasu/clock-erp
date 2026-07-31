#!/usr/bin/env python3
"""Audit or idempotently recover a posted receipt in the shared stock ledger."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase, DEFAULT_CATALOG_DATABASE_PATH  # noqa: E402
from app.services.receipt_recovery import ReceiptRecovery  # noqa: E402
from scripts.migrate_unified_catalog import backup_database  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-number", required=True)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_CATALOG_DATABASE_PATH,
    )
    parser.add_argument(
        "--instance-dir",
        type=Path,
        default=PROJECT_ROOT / "instance",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=PROJECT_ROOT / "backups",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    database = CatalogDatabase(args.database)
    recovery = ReceiptRecovery(database, args.instance_dir)
    if not args.apply:
        result = recovery.inspect(args.receipt_number)
        result["mode"] = "dry-run"
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        return 0

    backup = backup_database(args.database, args.backup_dir)
    database.initialize()
    result = recovery.apply(args.receipt_number)
    result["backup"] = str(backup)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
