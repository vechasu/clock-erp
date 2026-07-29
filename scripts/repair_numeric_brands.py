#!/usr/bin/env python3
"""Preview or apply cleanup of numeric ERP product brands."""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.services.numeric_brand_repair import NumericBrandRepair  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args()
    report = NumericBrandRepair(CatalogDatabase()).run(
        apply=bool(args.apply),
        backup_root=args.backup_root,
    )
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))
    return 0 if not report.get("errors") else 2


if __name__ == "__main__":
    sys.exit(main())
