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

    def test_category_stock_analytics_reconciles_brands_models_and_edge_values(self):
        catalog = ExcelProductCatalog(CatalogDatabase(self.database_path))
        baseline = catalog.stock_analytics()
        watches_id = next(
            item["id"] for item in baseline["categories"]
            if item["name"] == "Часы"
        )
        catalog.create_product(
            "Beta Watch", article="BETA-1", brand="Beta",
            category_id=watches_id, model="Beta Model", stock=1000000,
        )
        catalog.create_product(
            "No Brand Watch", article="", brand="",
            category_id=watches_id, model="", stock=5,
        )

        analytics = catalog.stock_analytics(watches_id)

        self.assertEqual(analytics["summary"]["total_stock"], 1001004)
        self.assertEqual(analytics["summary"]["brands"], 3)
        self.assertEqual(analytics["summary"]["models"], 4)
        self.assertEqual(
            analytics["summary"]["average_stock"], 1001004 / 4
        )
        self.assertEqual(
            sum(item["stock"] for item in analytics["brands"]),
            analytics["summary"]["total_stock"],
        )
        alpha = next(
            item for item in analytics["brands"] if item["name"] == "Alpha"
        )
        self.assertEqual(alpha["stock"], 999)
        self.assertEqual(alpha["models"], 2)
        self.assertEqual(
            sum(
                item["stock"] for item in analytics["models"]
                if item["brand_key"] == alpha["key"]
            ),
            alpha["stock"],
        )
        missing_brand = next(
            item for item in analytics["brands"]
            if item["name"] == "Без бренда"
        )
        missing_model = next(
            item for item in analytics["models"]
            if item["brand_key"] == missing_brand["key"]
        )
        self.assertEqual(missing_model["article"], "")
        self.assertEqual(analytics["models"][0]["stock"], 1000000)

    def test_category_stock_analytics_handles_empty_missing_and_uncategorized(self):
        catalog = ExcelProductCatalog(CatalogDatabase(self.database_path))
        with catalog.database.transaction() as connection:
            brand_id = connection.execute(
                "SELECT id FROM erp_brands ORDER BY id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO erp_categories "
                "(brand_id, name, normalized_name, active, created_at, updated_at) "
                "VALUES (?, 'Пустая', 'пустая', 1, '2026-01-01', '2026-01-01')",
                (brand_id,),
            )
            empty_id = connection.execute(
                "SELECT id FROM erp_categories WHERE normalized_name = 'пустая'"
            ).fetchone()[0]
        catalog.create_product(
            "Без категории", article="NO-CATEGORY", brand="Alpha", stock=0
        )

        empty = catalog.stock_analytics(empty_id)
        missing = catalog.stock_analytics("not-an-id")
        uncategorized = catalog.stock_analytics(0)

        self.assertEqual(empty["summary"], {
            "total_stock": 0,
            "brands": 0,
            "models": 0,
            "average_stock": 0.0,
        })
        self.assertTrue(missing["category_missing"])
        self.assertIsNone(missing["selected_category"])
        self.assertEqual(uncategorized["selected_category"]["name"], "Без категории")
        self.assertEqual(uncategorized["summary"]["models"], 1)
        self.assertEqual(uncategorized["summary"]["total_stock"], 0)

    def test_stock_analytics_page_supports_category_modes_and_brand_drilldown(self):
        catalog = ExcelProductCatalog(CatalogDatabase(self.database_path))
        watches_id = next(
            item["id"] for item in catalog.stock_analytics()["categories"]
            if item["name"] == "Часы"
        )
        analytics = catalog.stock_analytics(watches_id)
        alpha = next(
            item for item in analytics["brands"] if item["name"] == "Alpha"
        )

        brands_response = self.client.get(
            "/app/products?view=analytics&category_id={}".format(watches_id)
        )
        models_response = self.client.get(
            "/app/products?view=analytics&category_id={}&mode=models".format(
                watches_id
            )
        )
        detail_response = self.client.get(
            "/app/products?view=analytics&category_id={}&mode=brands&brand={}".format(
                watches_id, alpha["key"]
            )
        )
        brands_html = brands_response.get_data(as_text=True)

        self.assertEqual(brands_response.status_code, 200)
        self.assertIn("Аналитика остатков", brands_html)
        self.assertIn("Всего в категории", brands_html)
        self.assertIn("Доля от категории", brands_html)
        self.assertIn("<progress", brands_html)
        self.assertIn("Модель", models_response.get_data(as_text=True))
        self.assertIn("← Все бренды", detail_response.get_data(as_text=True))
        self.assertIn("Доля бренда", detail_response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
