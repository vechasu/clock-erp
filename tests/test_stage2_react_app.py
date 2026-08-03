import re
import unittest
from pathlib import Path

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
            "/app/settings",
        ):
            response = self.client.get(path)
            try:
                self.assertEqual(response.status_code, 200, path)
                self.assertIn('<div id="root" data-bootstrap="', response.get_data(as_text=True))
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

    def test_retired_frontend_routes_redirect_without_loading_legacy_ui(self):
        redirects = {
            "/warehouse": "/app/products",
            "/stock-operations": "/app/products",
            "/repair": "/app/products",
            "/sales": "/app/sales",
            "/receipts": "/app/receipts",
            "/settings": "/app/settings",
            "/app/repairs": "/app/products",
        }
        for path, location in redirects.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, location)

    def test_products_legacy_route_preserves_filters_for_react(self):
        response = self.client.get("/products?in_stock=1&per_page=100")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/app/products?in_stock=1&per_page=100")

    def test_production_bundle_has_no_retired_modules_or_stale_chunks(self):
        assets = Path(web.PROJECT_ROOT, "app", "static", "react", "assets")
        names = {path.name for path in assets.iterdir() if path.is_file()}
        self.assertFalse(any("Repair" in name for name in names))
        javascript = "\n".join(
            path.read_text(encoding="utf-8")
            for path in assets.glob("*.js")
        )
        for retired_marker in (
            "RepairsPage",
            "RepairForm",
            'to:`/repairs`',
            "Склад и ячейки",
            "Журнал операций",
            "Раздел ещё не перенесён",
        ):
            self.assertNotIn(retired_marker, javascript)


if __name__ == "__main__":
    unittest.main()
