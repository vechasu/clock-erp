import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog


ROOT = Path(__file__).resolve().parents[1]


class DynamicProductAnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.database_path)}
        )
        self.environment.start()
        self.database = CatalogDatabase(self.database_path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches "
                "(id,file_sha256,source_filename,row_count,total_stock,"
                "positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','analytics.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-25T10:00:00+00:00", "2026-08-25T10:00:00+00:00"),
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.alpha_x_stock = self._create(
            "Alpha Model X", "AX-1", "Alpha", "Часы", "Model X", 5
        )
        self.alpha_x_zero = self._create(
            "Alpha Model X Zero", "AX-0", "Alpha", "Часы", "Model X", 0
        )
        self.alpha_y = self._create(
            "Alpha Model Y", "AY-2", "Alpha", "Часы", "Model Y", 2
        )
        self.alpha_strap = self._create(
            "Alpha Strap", "AS-1", "Alpha", "Ремешки", "Strap", 1
        )
        self.beta_stock = self._create(
            "Beta Classic", "BC-4", "Beta", "Часы", "Classic", 4
        )
        self.beta_zero = self._create(
            "Beta Empty", "BE-0", "Beta", "Часы", "", 0
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET model=NULL "
                "WHERE id=?", (self.beta_zero["id"],)
            )
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def _create(self, name, article, brand, category, model, stock):
        return self.catalog.create_product(
            name=name, article=article, brand=brand, category=category,
            model=model, stock=stock,
        )

    def _metrics(self, query=""):
        response = self.client.get("/warehouse" + ("?" + query if query else ""))
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        values = dict(re.findall(
            r'data-product-metric="([^"]+)"[^>]*>([^<]*)</strong>', html
        ))
        return {
            "positions": int(values["positions"]),
            "in-stock": int(values["in-stock"]),
            "units": float(values["units"].replace(" ", "").replace(",", ".")),
            "out-of-stock": int(values["out-of-stock"]),
        }, html

    def _ids(self, product):
        return "brand_id={}&category_id={}&model_id={}".format(
            product["brand_id"], product["category_id"], product["model_id"]
        )

    def test_metrics_follow_all_filter_combinations_and_empty_results(self):
        scenarios = (
            ("", (6, 4, 12, 2)),
            ("brand_id={}".format(self.alpha_x_stock["brand_id"]), (4, 3, 8, 1)),
            (
                "brand_id={}&category_id={}".format(
                    self.alpha_x_stock["brand_id"], self.alpha_x_stock["category_id"]
                ),
                (3, 2, 7, 1),
            ),
            (self._ids(self.alpha_x_stock), (2, 1, 5, 1)),
            ("stock_state=in", (4, 4, 12, 0)),
            ("stock_state=out", (2, 0, 0, 2)),
            (
                "brand_id={}&stock_state=in".format(self.alpha_x_stock["brand_id"]),
                (3, 3, 8, 0),
            ),
            ("q=Alpha%20Model", (3, 2, 7, 1)),
            (
                "q=Alpha%20Model&brand_id={}".format(self.alpha_x_stock["brand_id"]),
                (3, 2, 7, 1),
            ),
            (
                "q=Alpha%20Model&brand_id={}&category_id={}&stock_state=in".format(
                    self.alpha_x_stock["brand_id"], self.alpha_x_stock["category_id"]
                ),
                (2, 2, 7, 0),
            ),
            ("q=missing-value", (0, 0, 0, 0)),
        )
        for query, expected in scenarios:
            with self.subTest(query=query):
                metrics, html = self._metrics(query)
                self.assertEqual(tuple(metrics.values()), expected)
                total = re.search(r'id="warehouseResultTotal"[^>]*>([^<]+)', html)
                if total:
                    self.assertEqual(
                        int(total.group(1).replace(" ", "")), expected[0]
                    )

    def test_pagination_and_page_size_never_limit_metrics(self):
        base = "brand_id={}".format(self.alpha_x_stock["brand_id"])
        first, _ = self._metrics(base + "&page=1&per_page=1")
        second, _ = self._metrics(base + "&page=2&per_page=1")
        large, _ = self._metrics(base + "&page=1&per_page=200")
        self.assertEqual(first, second)
        self.assertEqual(second, large)
        self.assertEqual(first, {
            "positions": 4, "in-stock": 3, "units": 8.0, "out-of-stock": 1,
        })

    def test_api_returns_same_total_and_metrics_for_model_and_page_filters(self):
        query = self._ids(self.alpha_x_stock) + "&page_size=1"
        first = self.client.get("/api/v1/products?" + query + "&page=1").get_json()
        second = self.client.get("/api/v1/products?" + query + "&page=2").get_json()
        for payload in (first, second):
            self.assertEqual(payload["meta"]["total"], 2)
            self.assertEqual(payload["meta"]["stats"], {
                "positions": 2,
                "total_stock": 5,
                "positive_positions": 1,
                "zero_positions": 1,
                "matched_positions": 0,
            })

    def test_invalid_filter_id_is_a_safe_validation_error(self):
        response = self.client.get("/api/v1/products?model_id=not-a-number")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["code"], "PRODUCT_FILTER_INVALID")

    def test_frontend_updates_every_metric_and_rejects_stale_responses(self):
        source = (ROOT / "app/templates/warehouse.html").read_text(encoding="utf-8")
        self.assertIn('captureWarehouseProductMetrics(documentCopy)', source)
        self.assertIn('root.querySelectorAll("[data-product-metric]")', source)
        self.assertIn("requestId !== warehouseSearchRequestId", source)
        self.assertIn("warehouseSearchController.abort()", source)
        self.assertIn('setWarehouseProductMetricsState("updating")', source)
        self.assertIn('setWarehouseProductMetricsState("error")', source)
        self.assertIn("Не удалось обновить товары и аналитику.", source)
        self.assertNotIn("nextCounter && currentCounter", source)


if __name__ == "__main__":
    unittest.main()
