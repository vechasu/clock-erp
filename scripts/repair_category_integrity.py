#!/usr/bin/env python3
"""Diagnose or repair one brand/category pair without touching other duplicates."""

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.category_integrity import (  # noqa: E402
    CategoryIntegrityRepair,
    connect_database,
    report_json,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    connection = connect_database(args.database, read_only=not args.apply)
    try:
        repair = CategoryIntegrityRepair(
            connection, args.brand, args.category
        )
        report = repair.apply() if args.apply else repair.diagnose()
    finally:
        connection.close()
    rendered = report_json(report) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
