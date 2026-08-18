#!/usr/bin/env python3
"""Apply the additive brand-inventory schema migration to the catalog database."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase, DEFAULT_CATALOG_DATABASE_PATH


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=str(DEFAULT_CATALOG_DATABASE_PATH))
    arguments = parser.parse_args()
    database = CatalogDatabase(Path(arguments.database))
    database.initialize()
    with database.connect() as connection:
        required = {"erp_inventory_sessions", "erp_inventory_items"}
        present = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        movement_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='catalog_stock_movements'"
        ).fetchone()[0]
        missing = sorted(required - present)
        if missing or "inventory_adjustment" not in movement_sql:
            raise SystemExit("Migration verification failed: {}".format(
                ", ".join(missing) or "movement type"
            ))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise SystemExit("Migration created foreign key violations")
    print("Brand inventory schema is ready: {}".format(database.path))


if __name__ == "__main__":
    main()
