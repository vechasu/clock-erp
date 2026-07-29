import re
import unittest
from pathlib import Path

from app import web


class ThemeSystemTest(unittest.TestCase):
    def setUp(self):
        self.app_root = Path(web.app.root_path)
        self.templates = self.app_root / "templates"
        self.static = self.app_root / "static"
        self.theme_css = (
            self.static / "css" / "themes.css"
        ).read_text(encoding="utf-8")
        self.theme_js = (
            self.static / "js" / "theme.js"
        ).read_text(encoding="utf-8")

    def test_two_themes_and_default_are_declared(self):
        self.assertIn(
            'html[data-theme="klok-green"]',
            self.theme_css,
        )
        self.assertIn(
            'html[data-theme="bn0024-white"]',
            self.theme_css,
        )
        self.assertIn(
            'const DEFAULT_THEME = "bn0024-white"',
            self.theme_js,
        )
        self.assertRegex(
            self.theme_js,
            re.compile(
                r'const THEMES = Object\.freeze\(\[\s*'
                r'"klok-green",\s*"bn0024-white"',
                re.MULTILINE,
            ),
        )

    def test_unknown_theme_falls_back_and_is_removed(self):
        self.assertIn(
            "THEMES.includes(value) ? value : DEFAULT_THEME",
            self.theme_js,
        )
        self.assertIn(
            "storage.removeItem(STORAGE_KEY)",
            self.theme_js,
        )

    def test_selected_theme_is_saved_and_restored_before_ui(self):
        self.assertIn(
            'const STORAGE_KEY = "vechasu-erp-theme-v1"',
            self.theme_js,
        )
        self.assertIn(
            "storage.getItem(STORAGE_KEY)",
            self.theme_js,
        )
        self.assertIn(
            "storage.setItem(STORAGE_KEY, theme)",
            self.theme_js,
        )
        self.assertLess(
            self.theme_js.index(
                "const initialTheme = setRootTheme(getStoredTheme())"
            ),
            self.theme_js.index(
                'document.addEventListener(\n'
                '            "DOMContentLoaded"'
            ),
        )

    def test_settings_has_accessible_visual_theme_cards(self):
        settings = (
            self.templates / "settings.html"
        ).read_text(encoding="utf-8")

        self.assertIn("<h2>Оформление</h2>", settings)
        self.assertIn('role="radiogroup"', settings)
        self.assertEqual(
            settings.count('class="theme-option"'),
            2,
        )
        self.assertIn(
            'data-theme-option="klok-green"',
            settings,
        )
        self.assertIn(
            'data-theme-option="bn0024-white"',
            settings,
        )
        self.assertIn("theme-preview-klok", settings)
        self.assertIn("theme-preview-bn", settings)

    def test_theme_assets_cover_auth_and_operational_pages(self):
        auth_base = (
            self.templates / "auth_base.html"
        ).read_text(encoding="utf-8")
        sidebar = (
            self.templates / "_sidebar.html"
        ).read_text(encoding="utf-8")

        for source in (auth_base, sidebar):
            self.assertIn("js/theme.js", source)
            self.assertIn("css/themes.css", source)

        operational_templates = {
            "base.html",
            "orders.html",
            "warehouse.html",
            "excel_products.html",
            "excel_product_detail.html",
            "excel_receipt_upload.html",
            "excel_receipt_preview.html",
            "excel_receipt_detail.html",
            "sales.html",
            "sales_report.html",
            "receipts.html",
            "receipts_report.html",
            "analytics.html",
            "catalog.html",
            "catalog_detail.html",
            "catalog_import_preview.html",
            "catalog_mapping.html",
            "stock_operations.html",
            "repair.html",
            "settings.html",
        }

        for template_name in operational_templates:
            source = (
                self.templates / template_name
            ).read_text(encoding="utf-8")
            self.assertIn(
                '{% include "_sidebar.html" %}',
                source,
                template_name,
            )

    def test_tokens_cover_required_component_states(self):
        tokens = {
            "--theme-app-bg",
            "--theme-sidebar-bg",
            "--theme-surface",
            "--theme-text",
            "--theme-text-muted",
            "--theme-border",
            "--theme-primary",
            "--theme-hover",
            "--theme-active",
            "--theme-selected",
            "--theme-focus",
            "--theme-success",
            "--theme-warning",
            "--theme-error",
            "--theme-shadow",
            "--theme-card-radius",
            "--theme-control-height",
            "--theme-table-head",
            "--theme-table-row",
            "--theme-input-bg",
            "--theme-scrollbar",
        }

        for token in tokens:
            with self.subTest(token=token):
                self.assertIn(token, self.theme_css)

        for selector in (
            "focus-visible",
            ".mobile-erp-navigation",
            ".warehouse-calendar-popup",
            ".modal-backdrop",
            ".catalog-combobox-option",
            ".products-column-resize",
            "@media (max-width: 430px)",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, self.theme_css)


if __name__ == "__main__":
    unittest.main()
