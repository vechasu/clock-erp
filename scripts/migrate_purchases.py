#!/usr/bin/env python3
from __future__ import print_function

import argparse
import json
import sqlite3

from app.purchases_migrations import migrate_database, verify_database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("apply", "verify"))
    parser.add_argument("--database", required=True)
    arguments = parser.parse_args()
    if arguments.mode == "apply":
        migrate_database(arguments.database)
    else:
        verify_database(arguments.database)
    connection = sqlite3.connect(arguments.database)
    try:
        counts = {
            table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in ("purchase_requests", "purchase_plan_items", "supplier_orders")
        }
    finally:
        connection.close()
    print(json.dumps({"ok": True, "counts": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
