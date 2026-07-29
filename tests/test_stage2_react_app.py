import re
import unittest

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


if __name__ == "__main__":
    unittest.main()
