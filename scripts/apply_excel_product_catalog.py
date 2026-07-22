#!/usr/bin/env python3
"""Preview, safely apply, or roll back an Excel-authoritative product batch."""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.services.excel_product_catalog import ExcelProductBatchService  # noqa: E402
from scripts.reconcile_bitrix_excel_catalog import (  # noqa: E402
    build_payload,
    validate_controls,
)


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
        raise SystemExit("--excel is required for preview or apply")
    payload = build_payload(args.excel, args.catalog_db, args.sheet)
    if not args.skip_control_check:
        validate_controls(payload["summary"])
    if not args.apply:
        print(json.dumps({
            "action": "preview_only",
            "batch": payload["batch"],
            "summary": payload["summary"],
            "writes_performed": 0,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.skip_control_check:
        raise SystemExit("refusing apply with --skip-control-check")
    expected_sha = payload["batch"]["file_sha256"]
    if args.confirm_file_sha != expected_sha:
        raise SystemExit(
            "refusing apply: pass --confirm-file-sha {}".format(expected_sha)
        )
    result = service.apply(
        payload["rows"], expected_sha, args.excel.name, args.sheet,
    )
    print(json.dumps({
        "action": "apply_internal_catalog_only",
        "batch": result,
        "bitrix_writes": 0,
        "moysklad_writes": 0,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
