#!/usr/bin/env python3
"""Regenerate the reviewed catalog schema contract from a fresh database."""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.catalog_migration_steps import apply_fresh_catalog_schema  # noqa: E402
from app.schema_migrations import (  # noqa: E402
    LEDGER_SQL,
    _json_structure,
    apply_order_comments_migration,
    apply_inventory_control_migration,
)


def main():
    target = ROOT / "app" / "catalog_schema_manifest.json"
    with tempfile.TemporaryDirectory(prefix="catalog-manifest-") as directory:
        connection = sqlite3.connect(str(Path(directory) / "catalog.db"))
        connection.row_factory = sqlite3.Row
        try:
            apply_fresh_catalog_schema(connection)
            connection.execute(LEDGER_SQL)
            connection.commit()
            apply_order_comments_migration(connection)
            connection.commit()
            apply_inventory_control_migration(connection)
            connection.commit()
            manifest = _json_structure(connection)
        finally:
            connection.close()
    target.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()
