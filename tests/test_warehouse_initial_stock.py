import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog


class WarehouseInitialStockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.database_path)
        self.database.initialize()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, sheet_name, row_count, "
                "total_stock, positive_rows, zero_rows, status, created_at, "
                "applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "active-batch",
                    "a" * 64,
                    "initial.xlsx",
                    "Импорт",
                    0,
                    0,
                    0,
                    0,
                    "active",
                    "2026-07-29T10:00:00+00:00",
                    "2026-07-29T10:00:00+00:00",
                ),
            )
        self.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(self.database_path)},
        )
        self.environment.start()
        self.claim_request = mock.patch.object(
            web,
            "claim_warehouse_add_request",
            return_value=True,
        )
        self.claim_request.start()
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.catalog = ExcelProductCatalog(self.database)

    def tearDown(self):
        self.claim_request.stop()
        self.environment.stop()
        self.temp.cleanup()

    def add_product(self, name, stock):
        return self.client.post(
            "/warehouse/add",
            data={
                "request_id": "request-{}".format(name),
                "name": name,
                "article": "ART-{}".format(name),
                "brand": "Test",
                "category": "Часы",
                "cell": "A-01",
                "stock": stock,
            },
        )

    def test_creates_products_with_zero_one_and_larger_initial_stock(self):
        for name, stock in (
            ("Stock Zero", "0"),
            ("Stock One", "1"),
            ("Stock Many", "12"),
        ):
            with self.subTest(stock=stock):
                response = self.add_product(name, stock)
                self.assertEqual(response.status_code, 302)
                self.assertIn("notice=success", response.headers["Location"])
                product = self.catalog.list_products(query=name)["items"][0]
                self.assertEqual(product["stock"], float(stock))

        operations = self.catalog.list_manual_stock_operations()
        self.assertEqual(
            {(item["stock_before"], item["stock_after"]) for item in operations},
            {(0.0, 1.0), (0.0, 12.0)},
        )

    def test_rejects_empty_negative_fractional_and_nonnumeric_stock(self):
        for stock in ("", "-1", "1.5", "abc"):
            with self.subTest(stock=stock):
                response = self.add_product("Invalid {}".format(stock), stock)
                self.assertEqual(response.status_code, 302)
                page = self.client.get(response.headers["Location"])
                html = page.get_data(as_text=True)
                self.assertEqual(page.status_code, 200)
                self.assertIn('id="addStockError"', html)
                self.assertIn(
                    "Начальный остаток должен быть целым числом от 0 и выше.",
                    html,
                )

        self.assertEqual(self.catalog.list_products()["total"], 0)

    def test_initial_stock_remains_visible_after_page_reload(self):
        response = self.add_product("Reload Stock", "7")
        self.assertEqual(response.status_code, 302)

        page = self.client.get("/warehouse?q=Reload%20Stock")
        html = page.get_data(as_text=True)
        compact_html = "".join(html.split())
        self.assertEqual(page.status_code, 200)
        self.assertIn("Reload Stock", html)
        self.assertIn('data-stock="7.0"', html)
        self.assertIn(">7</td>", compact_html)


if __name__ == "__main__":
    unittest.main()
