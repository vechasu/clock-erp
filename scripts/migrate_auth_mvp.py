#!/usr/bin/env python3
"""Back up and migrate the TicTacToy authentication SQLite database."""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain_schema_migrations import apply_domain_migrations, validate_auth_database


def migrate(database, backup_dir, apply_changes):
    database = Path(database)
    if not database.exists():
        if apply_changes:
            apply_domain_migrations(database, "auth")
        return None

    with sqlite3.connect(str(database), timeout=15) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError("auth.db failed quick_check: {}".format(result))
        connection.execute("PRAGMA wal_checkpoint(FULL)")

    if not apply_changes:
        return None

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "auth-{}.db".format(
        datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    shutil.copy2(str(database), str(backup_path))
    apply_domain_migrations(database, "auth")
    validate_auth_database(database)
    with sqlite3.connect(str(database), timeout=15) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError("migrated auth.db failed quick_check: {}".format(result))
    return backup_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="instance/auth.db")
    parser.add_argument("--backup-dir", default="instance/auth-backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    backup = migrate(args.database, args.backup_dir, args.apply)
    if not args.apply:
        print("AUTH_MIGRATION_DRY_RUN_OK")
    elif backup:
        print("AUTH_MIGRATION_OK backup={}".format(backup))
    else:
        print("AUTH_MIGRATION_OK new_database={}".format(args.database))


if __name__ == "__main__":
    main()
