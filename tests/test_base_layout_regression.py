import unittest
from pathlib import Path

from app import web


ROOT = Path(__file__).resolve().parents[1]


class BaseLayoutRegressionTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_exact_navigation_contract_is_fixed(self):
        self.assertEqual(
            [
                (item["key"], item["label"], item["href"])
                for item in web.NAVIGATION_DEFINITIONS
            ],
            [
                ("products", "Товары", "/app/products"),
                ("sales", "Продажи", "/app/sales"),
                ("receipts", "Приход", "/app/receipts"),
                ("journal", "Журнал", "/app/journal"),
                ("settings", "Настройки", "/app/settings"),
            ],
        )
        sidebar = self.source("app/templates/_sidebar.html")
        self.assertIn('class="sidebar-logo-mark"', sidebar)
        self.assertIn('id="sidebarToggle"', sidebar)
        self.assertIn('class="sidebar-system-status"', sidebar)
        self.assertIn('class="sidebar-user"', sidebar)

    def test_base_layout_visual_contract_is_present(self):
        sidebar_css = self.source("app/static/css/sidebar.css")
        self.assertIn("--sidebar-width: 228px", sidebar_css)
        self.assertIn("--sidebar-collapsed-width: 72px", sidebar_css)
        self.assertIn("--sidebar-bg: #0d2a49", sidebar_css)
        self.assertIn("overflow-x: hidden", sidebar_css)

        products = self.source("app/templates/warehouse.html")
        self.assertIn('id="warehouseSearchInput"', products)
        self.assertIn('id="warehouseFilterTrigger"', products)
        self.assertIn('id="filterDrawer"', products)
        self.assertIn('id="warehouseAddForm"', products)
        self.assertIn('id="editDrawer"', products)
        self.assertIn('class="warehouse-products-table', products)
        self.assertNotIn("page-header__eyebrow", products)
        self.assertNotIn("summary-grid", products)

        for template in ("sales.html", "receipts.html", "settings.html"):
            source = self.source("app/templates/" + template)
            self.assertIn('{% include "_sidebar.html" %}', source)
            self.assertNotIn("page-header__eyebrow", source)

    def test_stage2_shell_and_chunks_are_absent(self):
        for path in (
            "frontend/src/components/ErpShell.tsx",
            "frontend/src/features/products/ProductsPage.tsx",
            "frontend/src/features/sales/SalesPage.tsx",
            "frontend/src/features/receipts/ReceiptsPage.tsx",
        ):
            self.assertFalse((ROOT / path).exists(), path)

        assets = ROOT / "app" / "static" / "react"
        javascript = "\n".join(
            path.read_text(encoding="utf-8")
            for path in assets.glob("**/*.js")
        )
        for marker in (
            "ErpShell",
            "ProductsPage",
            "SalesPage",
            "ReceiptsPage",
            "RepairsPage",
            "Склад и ячейки",
            "Журнал операций",
        ):
            self.assertNotIn(marker, javascript)

    def test_retired_routes_do_not_restore_removed_modules(self):
        redirects = {
            "/repair": "/app/products",
            "/stock-operations": "/app/products",
            "/app/repairs": "/app/products",
            "/app/warehouse": "/app/products",
            "/app/operations": "/app/products",
            "/receipt": "/app/receipts",
        }
        for path, location in redirects.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, location)


if __name__ == "__main__":
    unittest.main()
