import re
import unittest
from urllib.parse import urlsplit

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

    def test_warehouse_route_redirects_to_react_products(self):
        response = self.client.get("/warehouse")
        try:
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, "/app/products")
        finally:
            response.close()

    def test_warehouse_route_keeps_query_string(self):
        response = self.client.get("/warehouse?q=chrono&hide_zero=1&per_page=100")
        try:
            self.assertEqual(response.status_code, 302)
            self.assertEqual(
                response.location,
                "/app/products?q=chrono&hide_zero=1&per_page=100",
            )
        finally:
            response.close()

    def test_warehouse_route_preserves_encoded_query(self):
        response = self.client.get(
            "/warehouse?q=Product%20%2B%20Test&brand=Скоро&hide_zero=1"
        )
        try:
            self.assertEqual(response.status_code, 302)
            parsed = urlsplit(response.location)
            self.assertEqual(parsed.path, "/app/products")
            query = dict(
                pair.split("=", 1)
                for pair in parsed.query.split("&")
                if pair
            )
            self.assertEqual(query.get("q"), "Product%20%2B%20Test")
            self.assertEqual(query.get("brand"), "%D0%A1%D0%BA%D0%BE%D1%80%D0%BE")
            self.assertEqual(query.get("hide_zero"), "1")
        finally:
            response.close()

    def test_products_route_redirects_to_warehouse(self):
        response = self.client.get("/products?in_stock=1&per_page=100")
        try:
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, "/warehouse?in_stock=1&per_page=100")
        finally:
            response.close()

    def test_warehouse_route_redirects_to_react_products_without_loop(self):
        first = self.client.get("/warehouse?in_stock=1")
        second = self.client.get(first.location)
        try:
            self.assertEqual(first.status_code, 302)
            self.assertEqual(first.location, "/app/products?in_stock=1")
            self.assertEqual(second.status_code, 200)
            self.assertIn('<div id="root">', second.get_data(as_text=True))
        finally:
            first.close()
            second.close()


if __name__ == "__main__":
    unittest.main()
