#!/usr/bin/env python3
"""Apply or verify the deploy-time customer registry migration."""

from __future__ import print_function

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.customer_registry_migrations import SCHEMA_VERSION, migrate_database  # noqa: E402
from app.services.customer_registry import validate_database  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "verify"))
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "apply":
        migrate_database(args.database)
    validate_database(args.database)
    connection = sqlite3.connect(str(args.database))
    try:
        version = connection.execute("SELECT value FROM registry_meta WHERE key='schema_version'").fetchone()[0]
        customers = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        operations = connection.execute("SELECT COUNT(*) FROM customer_operations").fetchone()[0]
    finally:
        connection.close()
    if version != SCHEMA_VERSION:
        raise RuntimeError("unexpected schema version")
    print("CUSTOMERS_MIGRATION={} customers={} operations={}".format(version, customers, operations))


if __name__ == "__main__":
    main()
