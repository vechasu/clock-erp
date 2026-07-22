#!/usr/bin/env python3
"""Create a read-only Bitrix/Excel reconciliation report (JSON and CSV)."""

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from openpyxl import load_workbook

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.product_reconciliation import (  # noqa: E402
    ProductReconciler,
    alternatives_json,
    batch_id_for,
    file_sha256,
    summarize_reconciliation,
    text,
)


EXPECTED_HEADERS = ["Название", "Артикул", "Бренд", "Категория", "Остаток", "Ячейка"]
REPORT_COLUMNS = [
    "excel_row", "excel_name", "excel_brand", "excel_article", "article_quality",
    "stock", "cell", "category", "product_id", "bitrix_product_id",
    "bitrix_xml_id", "bitrix_name", "bitrix_brand", "match_status",
    "match_method", "confidence", "alternatives", "reason",
]


def read_excel_rows(path, sheet_name="Импорт"):
    workbook = load_workbook(str(path), read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError("sheet is missing: {}".format(sheet_name))
        sheet = workbook[sheet_name]
        headers = [text(cell.value) for cell in next(sheet.iter_rows(min_row=1, max_row=1))[:6]]
        if headers != EXPECTED_HEADERS:
            raise ValueError("unexpected headers: {}".format(headers))
        rows = []
        for excel_row, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            values = list(values[:6]) + [None] * max(0, 6 - len(values))
            name, article, brand, category, stock, cell = values[:6]
            if not any(value not in (None, "") for value in values[:6]):
                continue
            rows.append({
                "excel_row": excel_row,
                "excel_name": text(name),
                "excel_article": text(article),
                "excel_brand": text(brand),
                "category": text(category),
                "stock": stock,
                "cell": text(cell),
            })
        return rows, list(workbook.sheetnames)
    finally:
        workbook.close()


def load_catalog_read_only(path):
    uri = "file:{}?mode=ro&immutable=1".format(Path(path).resolve().as_posix())
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("catalog database quick_check failed")
        rows = connection.execute(
            """
            SELECT p.id, p.external_product_id, p.external_xml_id, p.name,
                   p.article, p.brand, p.normalized_payload_json,
                   c.name AS category
            FROM catalog_products p
            LEFT JOIN catalog_categories c ON c.id = p.primary_category_id
            ORDER BY p.id
            """
        ).fetchall()
        products = []
        for row in rows:
            product = dict(row)
            try:
                payload = json.loads(product.pop("normalized_payload_json") or "{}")
            except (TypeError, ValueError):
                payload = {}
            product["article"] = product.get("article") or payload.get("external_sku") or ""
            products.append(product)
        return products
    finally:
        connection.close()


def _csv_value(result, column):
    if column == "alternatives":
        return alternatives_json(result)
    value = result.get(column)
    return "" if value is None else value


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row, column) for column in REPORT_COLUMNS})


def write_outputs(output_dir, payload):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reconciliation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_csv(output_dir / "all_rows.csv", payload["rows"])
    groups = {
        "automatic_matches.csv": {"exact", "high_confidence"},
        "needs_review.csv": {"ambiguous", "invalid"},
        "not_found.csv": {"not_found"},
        "conflicts.csv": {"ambiguous"},
        "duplicate_excel.csv": {"duplicate_excel"},
    }
    for filename, statuses in groups.items():
        write_csv(
            output_dir / filename,
            [row for row in payload["rows"] if row["match_status"] in statuses],
        )
    (output_dir / "batch_manifest.json").write_text(
        json.dumps(payload["batch"], ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_payload(excel_path, catalog_path, sheet_name="Импорт"):
    rows, sheets = read_excel_rows(excel_path, sheet_name)
    products = load_catalog_read_only(catalog_path)
    excel_sha = file_sha256(excel_path)
    catalog_sha = file_sha256(catalog_path)
    reconciled = ProductReconciler(products).reconcile(rows)
    file_metrics = {
        "file_sha256": excel_sha,
        "file_size": Path(excel_path).stat().st_size,
        "sheet": sheet_name,
        "sheets": sheets,
        "catalog_sha256": catalog_sha,
        "catalog_products": len(products),
    }
    summary = summarize_reconciliation(reconciled, products, file_metrics)
    return {
        "batch": {
            "batch_id": batch_id_for(excel_sha),
            "file_sha256": excel_sha,
            "catalog_sha256": catalog_sha,
            "status": "dry_run",
            "writes_performed": 0,
            "row_count": len(reconciled),
        },
        "summary": summary,
        "rows": reconciled,
    }


def validate_controls(summary):
    expected = {
        "rows_total": 3313,
        "stock_total": 1912,
        "positive_stock_rows": 836,
        "zero_stock_rows": 2477,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": summary.get(key)}
        for key, expected_value in expected.items()
        if summary.get(key) != expected_value
    }
    if mismatches:
        raise ValueError("Excel control values differ: {}".format(mismatches))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", required=True, type=Path)
    parser.add_argument("--catalog-db", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sheet", default="Импорт")
    parser.add_argument("--skip-control-check", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    payload = build_payload(args.excel, args.catalog_db, args.sheet)
    if not args.skip_control_check:
        validate_controls(payload["summary"])
    write_outputs(args.output_dir, payload)
    print(json.dumps({
        "batch_id": payload["batch"]["batch_id"],
        "output_dir": str(args.output_dir.resolve()),
        "summary": payload["summary"],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
