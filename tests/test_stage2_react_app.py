import re
import unittest
from unittest import mock

from app import web


class Stage2ReactAppTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def test_spa_routes_serve_the_react_build(self):
        for path in (
            "/app",
            "/app/",
            "/app/products",
            "/app/receipts",
            "/app/sales",
            "/app/repairs",
        ):
            response = self.client.get(path)
            try:
                self.assertEqual(response.status_code, 200, path)
                self.assertIn('<div id="root"></div>', response.get_data(as_text=True))
            finally:
                response.close()

    def test_compiled_asset_is_served_directly(self):
        index_response = self.client.get("/app/")
        try:
            index = index_response.get_data(as_text=True)
        finally:
            index_response.close()
        match = re.search(r'src="(/app/assets/[^"]+\.js)"', index)
        self.assertIsNotNone(match)
        response = self.client.get(match.group(1))
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn("javascript", response.content_type)
        finally:
            response.close()

    def test_warehouse_route_serves_the_legacy_jinja_shell(self):
        catalog = {
            "items": [],
            "brand_groups": [],
            "category_groups": [],
            "cell_groups": [],
            "total": 0,
            "stats": {"total_stock": 0},
            "pages": 0,
            "page": 1,
        }
        with (
            mock.patch.object(
                web.ExcelProductCatalog,
                "list_products",
                return_value=catalog,
            ),
            mock.patch.object(web, "get_excel_warehouse_items", return_value=[]),
            mock.patch.object(
                web,
                "load_catalog_taxonomy",
                return_value={"brands": [], "categories": []},
            ),
            mock.patch.object(web, "get_catalog_stock_history", return_value=[]),
        ):
            response = self.client.get("/warehouse?q=chrono")
        try:
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.location)
            self.assertIn('<div class="app">', html)
            self.assertIn('class="sidebar"', html)
            self.assertNotIn('<div id="root"></div>', html)
            self.assertNotIn("/app/products", html)
        finally:
            response.close()

    def test_products_route_redirects_to_warehouse(self):
        response = self.client.get("/products?in_stock=1&per_page=100")
        try:
            self.assertEqual(response.status_code, 302)
            self.assertEqual(
                response.location,
                "/warehouse?in_stock=1&per_page=100",
            )
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
