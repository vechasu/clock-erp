import unittest

from app import web


class FaviconRouteSmokeTests(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def test_auth_and_main_erp_pages_publish_favicon_links(self):
        for path in (
            "/login",
            "/register",
            "/app/products",
            "/app/sales",
            "/app/receipts",
            "/app/repairs",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                head = html.split("</head>", 1)[0]
                self.assertIn("/static/favicon.svg?v=", head)
                self.assertIn("/static/favicon-32x32.png?v=", head)
                self.assertIn("/static/favicon-16x16.png?v=", head)
                self.assertIn("/static/favicon.ico?v=", head)

    def test_favicon_assets_are_served_with_expected_types(self):
        for path, mimetype in (
            ("/static/favicon.svg", "image/svg+xml"),
            ("/static/favicon-32x32.png", "image/png"),
            ("/static/favicon-16x16.png", "image/png"),
            ("/static/favicon.ico", "image/x-icon"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, mimetype)


if __name__ == "__main__":
    unittest.main()
