#!/usr/bin/env python3
"""Audit, dry-run, or transactionally consolidate global ERP categories."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.category_consolidation import (  # noqa: E402
    CategoryConsolidation,
)
from app.services.category_integrity import connect_database  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.expected_plan_sha256 and not args.apply:
        parser.error("--expected-plan-sha256 requires --apply")
    connection = connect_database(args.database, read_only=not args.apply)
    try:
        consolidation = CategoryConsolidation(connection)
        report = (
            consolidation.apply(args.expected_plan_sha256)
            if args.apply else consolidation.build_plan()
        )
    finally:
        connection.close()
    rendered = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    sys.stdout.buffer.write(rendered.encode("utf-8"))


if __name__ == "__main__":
    main()
