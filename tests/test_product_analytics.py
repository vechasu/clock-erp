import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)


def product(row, name, stock, brand, category, cell):
    return {
        "excel_row": row,
        "excel_name": name,
        "excel_brand": brand,
        "excel_article": "AN-{}".format(row),
        "article_quality": "code_like",
        "category": category,
        "stock": stock,
        "stock_valid": True,
        "cell": cell,
        "product_id": None,
        "match_status": "not_found",
        "match_method": "test",
        "confidence": 0,
        "alternatives": [],
    }


class ProductAnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.database_path)}
        )
        self.environment.start()
        ExcelProductBatchService(CatalogDatabase(self.database_path)).apply(
            [
                product(2, "Alpha One", 999, "Alpha", "Часы", "A-1"),
                product(3, "Alpha Two", 0, "Alpha", "Часы", "A-1"),
                product(4, "Beta One", 2, "Beta", "Ремни", ""),
                product(5, "Archived One", 5000, "Archived", "Архив", "Z-9"),
            ],
            "a" * 64,
            "analytics.xlsx",
        )
        with CatalogDatabase(self.database_path).transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET active = 0 "
                "WHERE excel_article = 'AN-5'"
            )
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def test_analytics_uses_active_catalog_values_without_normalizing_stock(self):
        analytics = ExcelProductCatalog(
            CatalogDatabase(self.database_path)
        ).product_analytics()

        self.assertEqual(analytics["top_brands"][0]["name"], "Alpha")
        self.assertEqual(analytics["top_brands"][0]["positions"], 2)
        self.assertEqual(analytics["top_brands"][0]["units"], 999)
        self.assertEqual(
            [item["positions"] for item in analytics["stock_bands"]],
            [1, 0, 1, 0, 1],
        )
        self.assertEqual(analytics["top_cells"][0]["name"], "A-1")
        self.assertNotIn(
            "Archived", [item["name"] for item in analytics["top_brands"]]
        )

    def test_analytics_page_has_shared_navigation_and_readable_sections(self):
        response = self.client.get("/app/products?view=analytics")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('aria-current="page">Аналитика', html)
        self.assertIn("Бренды по числу позиций", html)
        self.assertIn("Распределение остатков", html)
        self.assertIn("Загрузка складских ячеек", html)
        self.assertIn(">999<", html)

    def test_empty_analytics_uses_standard_empty_states(self):
        empty_path = Path(self.temp.name) / "empty.db"
        with mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(empty_path)}
        ):
            response = self.client.get("/app/products?view=analytics")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(html.count("erp-empty-state"), 3)


if __name__ == "__main__":
    unittest.main()
