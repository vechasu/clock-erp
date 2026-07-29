import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductBatchService


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
        "match_method": "performance-test",
        "confidence": 0,
        "alternatives": [],
    }


def sale_record(index):
    return {
        "id": "sale-{:06d}".format(index),
        "sale_type": "manual",
        "sale_type_label": "Ручная",
        "is_manual": True,
        "inventory_managed": True,
        "created_at": "2026-07-{:02d}".format((index % 28) + 1),
        "source": "Tictactoy",
        "source_key": "tictactoy",
        "order_number": "ORDER-{:06d}".format(index),
        "product_id": str(index % 10000),
        "product_name": "Товар {:05d}".format(index % 10000),
        "brand": "Бренд {:02d}".format(index % 20),
        "category": "Категория {:02d}".format(index % 40),
        "quantity_value": 1,
        "quantity_display": "1",
        "net_quantity_value": 1,
        "returned_quantity": 0,
        "return_available_quantity": 1,
        "unit_price": 1000,
        "total_amount": 1000,
        "gross_total_amount": 1000,
        "returned_amount": 0,
        "order_status": "completed",
        "order_status_label": "Выполнен",
        "is_cancelled": False,
    }


class Stage2LargeDatasetsTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(self.database_path)},
        )
        self.environment.start()
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def test_products_api_pages_ten_thousand_rows(self):
        ExcelProductBatchService(CatalogDatabase(self.database_path)).apply(
            [product_result(index) for index in range(10000)],
            "c" * 64,
            "stage2-products.xlsx",
        )
        started = time.perf_counter()
        response = self.client.get(
            "/api/products?page=200&page_size=50&sort_by=name&sort_dir=asc"
        )
        elapsed = time.perf_counter() - started
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["meta"]["total"], 10000)
        self.assertEqual(len(payload["data"]), 50)
        self.assertLess(elapsed, 5.0)

    def test_sales_api_pages_one_hundred_thousand_rows(self):
        records = [sale_record(index) for index in range(100000)]
        with mock.patch.object(web, "api_sales_records", return_value=records):
            started = time.perf_counter()
            response = self.client.get(
                "/api/sales?page=2000&page_size=50"
                "&sort_by=created_at&sort_dir=desc"
            )
            elapsed = time.perf_counter() - started
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["meta"]["total"], 100000)
        self.assertEqual(len(payload["data"]), 50)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
