#!/usr/bin/env python3
"""Back up and add partial-inventory metadata without rewriting business data."""

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase, DEFAULT_CATALOG_DATABASE_PATH  # noqa: E402


def scalar(connection, query):
    row = connection.execute(query).fetchone()
    return row[0] if row and row[0] is not None else 0


def snapshot(path):
    with sqlite3.connect(str(path)) as connection:
        return {
            "products": int(scalar(connection, "SELECT COUNT(*) FROM catalog_excel_products")),
            "active_products": int(scalar(
                connection, "SELECT COUNT(*) FROM catalog_excel_products WHERE active = 1"
            )),
            "brands": int(scalar(connection, "SELECT COUNT(*) FROM erp_brands")),
            "categories": int(scalar(connection, "SELECT COUNT(*) FROM erp_categories")),
            "models": int(scalar(connection, "SELECT COUNT(*) FROM erp_models")),
            "brand_categories": int(scalar(
                connection, "SELECT COUNT(*) FROM erp_brand_categories"
            )),
            "stock_total": float(scalar(
                connection, "SELECT COALESCE(SUM(stock), 0) FROM catalog_excel_products"
            )),
            "sales": int(scalar(connection, "SELECT COUNT(*) FROM erp_sales")),
            "receipts": int(scalar(connection, "SELECT COUNT(*) FROM erp_receipts")),
            "legacy_receipts": int(scalar(
                connection, "SELECT COUNT(*) FROM catalog_excel_receipts"
            )),
            "inventory_documents": int(scalar(
                connection, "SELECT COUNT(*) FROM erp_inventory_sessions"
            )),
            "inventory_items": int(scalar(
                connection, "SELECT COUNT(*) FROM erp_inventory_items"
            )),
            "inventory_adjustments": int(scalar(
                connection,
                "SELECT COUNT(*) FROM catalog_stock_movements "
                "WHERE movement_type = 'inventory_adjustment'",
            )),
            "movements": int(scalar(
                connection, "SELECT COUNT(*) FROM catalog_stock_movements"
            )),
        }


def quick_check(path):
    with sqlite3.connect(str(path)) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError("SQLite quick_check failed: {}".format(result))


def backup_database(source, backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / "catalog-before-inventory-scopes-{}.db".format(stamp)
    with sqlite3.connect(str(source)) as source_connection:
        backup_method = getattr(source_connection, "backup", None)
        if backup_method is not None:
            with sqlite3.connect(str(target)) as target_connection:
                backup_method(target_connection)
        else:
            completed = subprocess.run(
                ["sqlite3", str(source), ".backup '{}'".format(
                    str(target).replace("'", "''")
                )],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "SQLite backup failed: {}".format(
                        completed.stderr.strip() or "unknown error"
                    )
                )
    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError("SQLite backup artifact is missing or empty.")
    quick_check(target)
    return target


def inventory_schema(path):
    with sqlite3.connect(str(path)) as connection:
        sessions = {row[1] for row in connection.execute(
            "PRAGMA table_info(erp_inventory_sessions)"
        ).fetchall()}
        items = {row[1] for row in connection.execute(
            "PRAGMA table_info(erp_inventory_items)"
        ).fetchall()}
    return {"sessions": sorted(sessions), "items": sorted(items)}


def migrate(path):
    before = snapshot(path)
    CatalogDatabase(path, cache_initialization=False).initialize()
    after = snapshot(path)
    CatalogDatabase(path, cache_initialization=False).initialize()
    after_second_run = snapshot(path)
    if before != after or after != after_second_run:
        raise RuntimeError("Inventory schema migration changed business counts.")
    quick_check(path)
    schema = inventory_schema(path)
    required_sessions = {
        "scope_type", "category_id", "model_id", "idempotency_key",
        "scope_brand_name", "scope_category_name", "scope_model_name",
    }
    required_items = {
        "snapshot_name", "snapshot_article", "snapshot_brand_id",
        "snapshot_category_id", "snapshot_model_id", "snapshot_brand_name",
        "snapshot_category_name", "snapshot_model_name", "snapshot_photo_url",
    }
    if not required_sessions.issubset(schema["sessions"]):
        raise RuntimeError("Inventory session columns are incomplete.")
    if not required_items.issubset(schema["items"]):
        raise RuntimeError("Inventory item columns are incomplete.")
    return before, after, schema


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_CATALOG_DATABASE_PATH)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    database = arguments.database.resolve()
    if not database.exists():
        raise SystemExit("Catalog database not found: {}".format(database))
    backup = backup_database(database, arguments.backup_dir.resolve())
    if arguments.apply:
        before, after, schema = migrate(database)
    else:
        with tempfile.TemporaryDirectory(prefix="inventory-scope-migration-") as root:
            target = Path(root) / database.name
            rehearsal = backup_database(database, Path(root))
            rehearsal.replace(target)
            before, after, schema = migrate(target)
    print(json.dumps({
        "mode": "apply" if arguments.apply else "rehearsal",
        "backup": str(backup),
        "backup_size": backup.stat().st_size,
        "before": before,
        "after": after,
        "schema": schema,
        "quick_check": "ok",
        "idempotent": True,
    }, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
