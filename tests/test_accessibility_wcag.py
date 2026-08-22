import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"


class AccessibilityWcagContractTests(unittest.TestCase):
    def source(self, name):
        return (TEMPLATES / name).read_text(encoding="utf-8")

    def test_core_workspaces_have_skip_target_and_single_page_heading(self):
        sidebar = self.source("_sidebar.html")
        self.assertIn('class="erp-skip-link" href="#main-content"', sidebar)
        self.assertIn('aria-current="page"', sidebar)

        for name in (
            "warehouse.html",
            "sales.html",
            "receipts.html",
            "orders.html",
            "repair.html",
            "journal.html",
            "settings.html",
            "warehouse_inventory.html",
        ):
            with self.subTest(template=name):
                source = self.source(name)
                self.assertEqual(source.count('id="main-content"'), 1)
                if name == "warehouse.html":
                    source += self.source("_products_workspace.html")
                self.assertEqual(len(re.findall(r"<h1(?:\s|>)", source)), 1)

    def test_auth_pages_use_the_form_title_as_the_single_h1(self):
        base = self.source("auth_base.html")
        self.assertIn('id="main-content" tabindex="-1"', base)
        self.assertNotIn("<h1>Работа магазина", base)
        for name in (
            "login.html",
            "register.html",
            "forgot_password.html",
            "reset_password.html",
            "auth_message.html",
            "invitation_created.html",
            "registration_success.html",
        ):
            with self.subTest(template=name):
                self.assertEqual(
                    len(re.findall(r"<h1(?:\s|>)", self.source(name))),
                    1,
                )

    def test_search_and_filter_controls_have_accessible_names(self):
        products = self.source("warehouse.html")
        sales = self.source("sales.html")
        receipts = self.source("receipts.html")
        self.assertRegex(
            products,
            r'id="warehouseSearchInput"[\s\S]{0,180}aria-label="Поиск товаров"',
        )
        self.assertRegex(
            sales,
            r'id="salesSearch"[\s\S]{0,180}aria-label="Поиск продаж"',
        )
        for control in (
            "receiptSearch",
            "receiptBrandFilter",
            "receiptCategoryFilter",
            "receiptStatusFilter",
        ):
            self.assertRegex(
                receipts,
                rf'id="{control}"[^>]*aria-label=',
            )

    def test_no_positive_tabindex_is_introduced(self):
        for path in TEMPLATES.glob("*.html"):
            with self.subTest(template=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r'tabindex="[1-9]\d*"', source))

    def test_shared_accessibility_layer_covers_focus_tables_and_live_regions(self):
        css = (ROOT / "app/static/css/erp-states.css").read_text(encoding="utf-8")
        script = (ROOT / "app/static/js/erp-states.js").read_text(encoding="utf-8")
        self.assertIn(":focus-visible", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn(".erp-skip-link:focus", css)
        self.assertIn('header.setAttribute("scope", "col")', script)
        self.assertIn('header.setAttribute("aria-sort", direction)', script)
        self.assertIn('region.setAttribute("role", "region")', script)
        self.assertIn('region.setAttribute("aria-atomic", "true")', script)

    def test_modal_shell_traps_focus_hides_background_and_restores_focus(self):
        script = (ROOT / "app/static/js/erp-modal-shell.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key !== "Tab"', script)
        self.assertIn("sibling.inert = true", script)
        self.assertIn("returnFocus", script)
        self.assertIn("MutationObserver", script)


if __name__ == "__main__":
    unittest.main()
