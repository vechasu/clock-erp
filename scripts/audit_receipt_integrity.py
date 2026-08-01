#!/usr/bin/env python3
"""Read-only audit of receipt documents, quantities, and the shared stock ledger."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def scalar(connection, query, parameters=()):
    row = connection.execute(query, parameters).fetchone()
    return int(row[0] or 0) if row else 0


def load_receipts(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def audit(database_path, receipts_path):
    uri = "file:{}?mode=ro".format(database_path.resolve().as_posix())
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required = {
            "erp_receipts",
            "erp_receipt_items",
            "catalog_stock_movements",
            "catalog_excel_products",
            "erp_brands",
            "erp_categories",
        }
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError("Отсутствуют таблицы: {}".format(", ".join(missing)))

        report = {
            "managed_receipts": scalar(connection, "SELECT COUNT(*) FROM erp_receipts"),
            "posted_without_post_movements": scalar(
                connection,
                "SELECT COUNT(*) FROM erp_receipts r WHERE r.status = 'posted' "
                "AND NOT EXISTS (SELECT 1 FROM catalog_stock_movements m "
                "WHERE m.receipt_id = r.id AND m.movement_type = 'receipt')",
            ),
            "movements_without_receipt": scalar(
                connection,
                "SELECT COUNT(*) FROM catalog_stock_movements m "
                "WHERE m.receipt_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM erp_receipts r WHERE r.id = m.receipt_id)",
            ),
            "duplicate_idempotency_keys": scalar(
                connection,
                "SELECT COUNT(*) FROM (SELECT idempotency_key FROM "
                "catalog_stock_movements WHERE idempotency_key IS NOT NULL "
                "GROUP BY idempotency_key HAVING COUNT(*) > 1)",
            ),
            "duplicate_source_operations": scalar(
                connection,
                "SELECT COUNT(*) FROM (SELECT tenant_id, source_type, source_id, "
                "source_line_id, operation_kind FROM catalog_stock_movements "
                "WHERE source_type IS NOT NULL GROUP BY tenant_id, source_type, "
                "source_id, source_line_id, operation_kind HAVING COUNT(*) > 1)",
            ),
            "fractional_receipt_quantities": scalar(
                connection,
                "SELECT COUNT(*) FROM erp_receipt_items "
                "WHERE abs(quantity - round(quantity)) > 0.000001",
            ),
            "nonpositive_receipt_quantities": scalar(
                connection,
                "SELECT COUNT(*) FROM erp_receipt_items WHERE quantity <= 0",
            ),
            "negative_product_stock": scalar(
                connection,
                "SELECT COUNT(*) FROM catalog_excel_products WHERE stock < 0",
            ),
            "ledger_latest_stock_mismatches": scalar(
                connection,
                "SELECT COUNT(*) FROM catalog_excel_products p JOIN "
                "catalog_stock_movements m ON m.id = (SELECT m2.id FROM "
                "catalog_stock_movements m2 WHERE m2.product_id = p.id "
                "ORDER BY m2.created_at DESC, m2.rowid DESC LIMIT 1) "
                "WHERE abs(p.stock - m.stock_after) > 0.000001",
            ),
            "brands_without_categories": scalar(
                connection,
                "SELECT COUNT(*) FROM erp_brands b WHERE b.active = 1 AND NOT EXISTS "
                "(SELECT 1 FROM erp_categories c WHERE c.brand_id = b.id AND c.active = 1)",
            ),
            "duplicate_categories_same_brand": scalar(
                connection,
                "SELECT COUNT(*) FROM (SELECT brand_id, normalized_name FROM "
                "erp_categories GROUP BY brand_id, normalized_name HAVING COUNT(*) > 1)",
            ),
        }

    legacy = load_receipts(receipts_path)
    document_numbers = Counter(
        str(item.get("number") or "").strip().casefold()
        for item in legacy
        if isinstance(item, dict) and str(item.get("number") or "").strip()
    )
    report.update({
        "legacy_json_receipts": len(legacy),
        "duplicate_document_numbers": sum(
            1 for count in document_numbers.values() if count > 1
        ),
        "ambiguous_number_comment_records": sum(
            1
            for item in legacy
            if isinstance(item, dict)
            and str(item.get("number") or "").strip()
            and not str(item.get("note") or "").strip()
            and len(str(item.get("number") or "").strip()) > 40
        ),
    })
    report["safe_to_auto_repair"] = False
    report["repair_note"] = (
        "Неоднозначные исторические записи не изменяются автоматически; "
        "для однозначного прихода используйте recover_receipt_inventory.py --dry-run."
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = audit(arguments.database, arguments.receipts)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
