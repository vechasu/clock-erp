#!/usr/bin/env python3
"""Apply or verify the isolated mailbox schema."""

from __future__ import print_function

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mail_migrations import migrate_database, validate_database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("apply", "verify"))
    parser.add_argument("--database", default="instance/mail.db")
    args = parser.parse_args()
    if args.command == "apply":
        migrate_database(args.database)
    version = validate_database(args.database)
    print("MAIL_MIGRATION={}".format(version))


if __name__ == "__main__":
    main()
