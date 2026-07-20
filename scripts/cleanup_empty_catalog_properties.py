#!/usr/bin/env python3
"""Preview or safely delete only semantically empty catalog property rows."""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.services.catalog_data_quality import property_audit  # noqa: E402


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cleanup(database, apply=False, backup_root=Path("/var/backups/clock-erp-catalog")):
    with database.connect() as connection:
        before = property_audit(connection)
    report = {
        "mode": "apply" if apply else "preview",
        "rows_before": before["total_rows"],
        "filled_rows": before["filled_rows"],
        "rows_to_delete": before["empty_rows"],
        "rows_deleted": 0,
        "backup_path": None,
        "quick_check": None,
        "foreign_key_errors": None,
    }
    if not apply:
        return report

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_directory = Path(backup_root) / (timestamp + "-property-cleanup")
    backup_directory.mkdir(parents=True, exist_ok=False)
    backup_path = backup_directory / "catalog.db"
    shutil.copy2(str(database.path), str(backup_path))
    if file_hash(database.path) != file_hash(backup_path):
        raise RuntimeError("Catalog backup checksum mismatch")
    report["backup_path"] = str(backup_path)

    empty_ids = before["empty_row_ids"]
    with database.transaction() as connection:
        for offset in range(0, len(empty_ids), 500):
            batch = empty_ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            connection.execute(
                "DELETE FROM catalog_product_property_values WHERE id IN ({})".format(placeholders),
                batch,
            )
    with database.connect() as connection:
        after = property_audit(connection)
        report["quick_check"] = connection.execute("PRAGMA quick_check").fetchone()[0]
        report["foreign_key_errors"] = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    report["rows_deleted"] = before["total_rows"] - after["total_rows"]
    report["rows_after"] = after["total_rows"]
    report["remaining_empty_rows"] = after["empty_rows"]
    if report["rows_deleted"] != report["rows_to_delete"]:
        raise RuntimeError("Unexpected property cleanup count")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-root", type=Path, default=Path("/var/backups/clock-erp-catalog"))
    args = parser.parse_args()
    report = cleanup(CatalogDatabase(), apply=args.apply, backup_root=args.backup_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
