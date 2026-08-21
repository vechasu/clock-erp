#!/usr/bin/env python3
"""Dry-run or apply conservative product-model recognition."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.services.product_model_backfill import ProductModelBackfill  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", help="SQLite catalog path; defaults to CATALOG_DATABASE_PATH")
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument("--backup-dir", help="Backup directory used before apply")
    parser.add_argument("--apply", action="store_true", help="Apply high-confidence empty-model rows")
    parser.add_argument(
        "--confirm-high-confidence",
        action="store_true",
        help="Required second guard for --apply",
    )
    args = parser.parse_args()
    if args.apply and not args.confirm_high_confidence:
        parser.error("--apply requires --confirm-high-confidence")

    service = ProductModelBackfill(CatalogDatabase(args.database) if args.database else None)
    report = service.apply(args.backup_dir) if args.apply else service.dry_run()
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
