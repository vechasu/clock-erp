#!/usr/bin/env python3
"""Repeatable, read-only HTTP benchmark for the four MVP sections.

The benchmark creates production-shaped data in a temporary directory. It never
uses project ``instance`` files and does not call Bitrix or MoySklad.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import web  # noqa: E402
from app.catalog_db import CatalogDatabase  # noqa: E402
from app.services.excel_product_catalog import ExcelProductBatchService  # noqa: E402


def product_result(index):
    return {
        "excel_row": index + 2,
        "excel_name": "Товар {:05d}".format(index),
        "excel_brand": "Бренд {:02d}".format(index % 20),
        "excel_article": "PERF-{:05d}".format(index),
        "article_quality": "code_like",
        "category": "Категория {:02d}".format(index % 40),
        "stock": float(index % 25),
        "stock_valid": True,
        "cell": "A-{:03d}".format(index % 200),
        "product_id": None,
        "match_status": "not_found",
        "match_method": "performance-benchmark",
        "confidence": 0,
        "alternatives": [],
    }


def sale_record(index, product_count):
    product_index = index % product_count
    return {
        "id": "sale-{:06d}".format(index),
        "created_at": "2026-07-{:02d}".format((index % 28) + 1),
        "source": "Tictactoy",
        "product_id": str(product_index + 1),
        "product_name": "Товар {:05d}".format(product_index),
        "quantity": 1,
        "unit_price": 1000,
        "order_number": "ORDER-{:06d}".format(index),
        "status": "completed",
        "note": "Performance benchmark",
    }


def receipt_record(index, product_count):
    product_index = index % product_count
    quantity = (index % 5) + 1
    purchase_price = 500 + (index % 100)
    return {
        "id": "receipt-{:06d}".format(index),
        "number": "PR-2026-{:06d}".format(index),
        "created_at": "2026-07-{:02d} 12:00".format((index % 28) + 1),
        "receipt_date": "2026-07-{:02d}".format((index % 28) + 1),
        "status": "posted",
        "status_label": "Проведён",
        "note": "Performance benchmark",
        "positions": [{
            "product_id": str(product_index + 1),
            "product_name": "Товар {:05d}".format(product_index),
            "brand": "Бренд {:02d}".format(product_index % 20),
            "category": "Категория {:02d}".format(product_index % 40),
            "quantity": quantity,
            "purchase_price": purchase_price,
            "line_total": quantity * purchase_price,
            "stock_before": product_index % 25,
            "stock_after": (product_index % 25) + quantity,
        }],
        "total_quantity": quantity,
        "total_amount": quantity * purchase_price,
    }


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def sql_kind(statement):
    normalized = statement.lstrip().upper()
    if normalized.startswith(("SELECT", "WITH")):
        return "select"
    if normalized.startswith(("CREATE", "ALTER", "DROP", "PRAGMA")):
        return "schema"
    return "other"


def query_plans(database_path):
    database = CatalogDatabase(database_path)
    plans = {}
    queries = {
        "products_page": (
            "SELECT p.id FROM catalog_excel_products p "
            "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
            "WHERE p.active = 1 "
            "ORDER BY p.excel_name_raw COLLATE NOCASE, p.id LIMIT 50",
            [],
        ),
        "products_category": (
            "SELECT p.id FROM catalog_excel_products p "
            "WHERE p.category_id = ? AND p.active = 1 ORDER BY p.id LIMIT 50",
            [1],
        ),
        "legacy_receipt_links": (
            "SELECT entity_id, position_index, product_id "
            "FROM erp_legacy_catalog_links "
            "WHERE entity_type = ? AND entity_id IN (?, ?)",
            ["receipt", "receipt-000001", "receipt-000002"],
        ),
    }
    with database.connect() as connection:
        for name, (query, parameters) in queries.items():
            plans[name] = [
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN " + query,
                    parameters,
                ).fetchall()
            ]
    return plans


def run_benchmark(product_count, sale_count, receipt_count, runs):
    original_config = dict(web.app.config)
    sql_statements = []
    original_connect = CatalogDatabase.connect

    def traced_connect(database):
        connection = original_connect(database)
        connection.set_trace_callback(sql_statements.append)
        return connection

    with tempfile.TemporaryDirectory(prefix="vechasu-mvp-performance-") as root_name:
        root = Path(root_name)
        database_path = root / "catalog.db"
        manual_sales_path = root / "manual_sales.json"
        operations_path = root / "stock_operations.json"
        overrides_path = root / "automatic_sales_overrides.json"
        receipts_path = root / "receipts.json"
        settings_path = root / "settings.json"
        navigation_path = root / "navigation.json"

        ExcelProductBatchService(CatalogDatabase(database_path)).apply(
            [product_result(index) for index in range(product_count)],
            "d" * 64,
            "mvp-performance.xlsx",
        )
        write_json(
            manual_sales_path,
            [sale_record(index, product_count) for index in range(sale_count)],
        )
        write_json(operations_path, [])
        write_json(overrides_path, {})
        write_json(
            receipts_path,
            [receipt_record(index, product_count) for index in range(receipt_count)],
        )
        write_json(settings_path, web.DEFAULT_APP_SETTINGS)
        write_json(navigation_path, web.get_default_navigation_settings())

        web._cached_api_sales_records.cache_clear()
        web._cached_api_receipt_records.cache_clear()
        web._load_app_settings_cached.cache_clear()
        web._load_navigation_settings_cached.cache_clear()
        CatalogDatabase._schema_cache.clear()
        web.app.config.update(TESTING=True, AUTH_TESTING=False)

        scenarios = {
            "products_open": "/api/v1/products?page=1&page_size=50",
            "products_search": (
                "/api/v1/products?search=PERF-04599&page=1&page_size=50"
            ),
            "products_filter_sort": (
                "/api/v1/products?brand_id=1&category_id=1&sort=stock"
                "&order=desc&page=1&page_size=50"
            ),
            "sales_open": "/api/v1/sales?page=1&page_size=50",
            "sales_search": (
                "/api/v1/sales?search=ORDER-019999&page=1&page_size=50"
            ),
            "receipts_open": "/api/v1/receipts?page=1&page_size=50",
            "receipts_search": (
                "/api/v1/receipts?search=PR-2026-019999&page=1&page_size=50"
            ),
            "settings_open": "/settings",
        }
        result = {
            "dataset": {
                "products": product_count,
                "sales": sale_count,
                "receipts": receipt_count,
            },
            "scenarios": {},
        }

        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(
                os.environ,
                {"CATALOG_DATABASE_PATH": str(database_path)},
            ))
            stack.enter_context(mock.patch.object(
                CatalogDatabase,
                "connect",
                traced_connect,
            ))
            for name, path in (
                ("get_manual_sales_path", manual_sales_path),
                ("get_stock_operations_path", operations_path),
                ("get_automatic_sales_overrides_path", overrides_path),
                ("get_receipts_path", receipts_path),
                ("get_app_settings_path", settings_path),
                ("get_navigation_settings_path", navigation_path),
            ):
                stack.enter_context(mock.patch.object(
                    web,
                    name,
                    return_value=path,
                ))

            client = web.app.test_client()
            for scenario, path in scenarios.items():
                measurements = []
                for _ in range(runs):
                    sql_statements.clear()
                    started = time.perf_counter()
                    response = client.get(path)
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    payload = response.get_json(silent=True) or {}
                    meta = payload.get("meta") or {}
                    measurements.append({
                        "milliseconds": round(elapsed_ms, 2),
                        "status": response.status_code,
                        "response_bytes": len(response.data),
                        "page_items": len(payload.get("data") or []),
                        "total": meta.get("total"),
                        "sql_total": len(sql_statements),
                        "sql_select": sum(
                            sql_kind(statement) == "select"
                            for statement in sql_statements
                        ),
                        "sql_schema": sum(
                            sql_kind(statement) == "schema"
                            for statement in sql_statements
                        ),
                    })
                result["scenarios"][scenario] = measurements
            result["query_plans"] = query_plans(database_path)

    web.app.config.clear()
    web.app.config.update(original_config)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", type=int, default=4600)
    parser.add_argument("--sales", type=int, default=20000)
    parser.add_argument("--receipts", type=int, default=20000)
    parser.add_argument("--runs", type=int, default=3)
    arguments = parser.parse_args()
    result = run_benchmark(
        arguments.products,
        arguments.sales,
        arguments.receipts,
        arguments.runs,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
