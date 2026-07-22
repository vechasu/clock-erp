#!/usr/bin/env python3
"""Create a receipt draft or roll back a legacy Excel-authoritative batch.

Direct catalog apply is intentionally disabled. A draft must be posted through
the explicit receipt confirmation workflow in the application.
"""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.services.excel_product_catalog import ExcelProductBatchService  # noqa: E402
from app.services.excel_receipt_import import ExcelReceiptImportService  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-db", required=True, type=Path)
    parser.add_argument("--excel", type=Path)
    parser.add_argument("--sheet", default="Импорт")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-file-sha")
    parser.add_argument("--rollback-batch")
    parser.add_argument("--skip-control-check", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    service = ExcelProductBatchService(CatalogDatabase(args.catalog_db))
    if args.rollback_batch:
        if args.apply or args.excel or args.confirm_file_sha:
            raise SystemExit("rollback cannot be combined with Excel apply arguments")
        result = service.rollback(args.rollback_batch)
        print(json.dumps({"action": "rollback", "batch": result}, ensure_ascii=False, indent=2))
        return
    if not args.excel:
        raise SystemExit("--excel is required for a receipt draft")
    if args.apply or args.confirm_file_sha:
        raise SystemExit(
            "direct Excel apply is disabled; open /products and explicitly post "
            "the validated receipt draft"
        )
    draft = ExcelReceiptImportService(CatalogDatabase(args.catalog_db)).preview(
        args.excel.read_bytes(), args.excel.name, args.sheet or None,
    )
    print(json.dumps({
        "action": "receipt_draft_only",
        "draft": {
            key: draft[key] for key in (
                "id", "file_sha256", "source_filename", "status", "row_count",
                "valid_rows", "error_rows", "excluded_rows", "new_rows",
                "matched_rows", "total_quantity",
            )
        },
        "catalog_writes": 0,
        "stock_writes": 0,
        "bitrix_writes": 0,
        "moysklad_writes": 0,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
