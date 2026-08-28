#!/usr/bin/env python3
"""Apply or verify the isolated SMS schema without contacting SmsBliss."""

from __future__ import print_function

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.sms_migrations import migrate_database, verify_database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "verify", "rehearse"))
    parser.add_argument("--database", required=True)
    arguments = parser.parse_args()
    database = Path(arguments.database).resolve()
    if arguments.action == "verify":
        verify_database(database)
        print("SMS_SCHEMA_OK={}".format(database))
        return
    if arguments.action == "apply":
        migrate_database(database)
        print("SMS_MIGRATION_OK={}".format(database))
        return
    rehearsal_root = Path(tempfile.mkdtemp(prefix="sms-migration-rehearsal-"))
    try:
        target = rehearsal_root / "sms.db"
        if database.exists():
            subprocess.check_call([
                "sqlite3", str(database),
                ".backup '{}'".format(str(target).replace("'", "''")),
            ])
        migrate_database(target)
        verify_database(target)
        print("SMS_REHEARSAL_OK")
    finally:
        shutil.rmtree(str(rehearsal_root))


if __name__ == "__main__":
    main()
