import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ErpDesignSystemV1Test(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_tokens_cover_layout_semantics_focus_and_layers(self):
        css = self.source("app/static/css/erp-components.css")
        tokens = css.split(":root {", 1)[1].split("}", 1)[0]
        for token in (
            "--erp-canvas: var(--theme-workspace-bg",
            "--erp-primary: var(--theme-primary",
            "--erp-success: var(--theme-success",
            "--erp-warning: var(--theme-warning",
            "--erp-danger: var(--theme-error",
            "--erp-control-height: var(--theme-control-height",
            "--erp-focus-ring:",
            "--erp-z-dropdown:",
            "--erp-z-drawer:",
            "--erp-z-modal:",
            "--erp-z-notification:",
        ):
            with self.subTest(token=token):
                self.assertIn(token, tokens)

    def test_active_sections_share_page_shell_and_tab_contracts(self):
        brands = self.source("app/templates/warehouse_brands.html")
        journal = self.source("app/templates/journal.html")
        sales = self.source("app/templates/sales.html")

        self.assertIn('<body class="brands-page">', brands)
        self.assertIn('<body class="journal-page">', journal)
        self.assertIn("journal-header erp-workspace-header", journal)
        self.assertIn("journal-tabs erp-section-tabs", journal)
        self.assertIn('class="journal-tab{% if filters.entity_type', journal)
        self.assertIn("sales-tabs erp-section-tabs", sales)
        self.assertIn("sales-tab erp-section-tab", sales)

    def test_settings_is_one_form_composed_as_three_visual_sections(self):
        settings = self.source("app/templates/settings.html")
        self.assertIn("settings-header", settings)
        self.assertIn("Параметры системы и оформления", settings)
        self.assertEqual(settings.count('id="settingsForm"'), 1)
        self.assertEqual(settings.count('class="settings-section"'), 3)
        for heading in ("Оформление", "Компания", "Склад"):
            self.assertIn(f"<h2>{heading}</h2>", settings)
        for field in ("company_name", "erp_name", "low_stock_threshold"):
            self.assertIn(f'name="{field}"', settings)
        self.assertEqual(settings.count('type="submit"'), 1)

    def test_v1_layer_is_scoped_and_does_not_override_table_engine(self):
        css = self.source("app/static/css/erp-components.css")
        v1 = css.split("ERP Design System V1", 1)[1]
        self.assertNotIn("!important", v1)
        self.assertNotIn(".erp-data-table", v1)
        self.assertNotIn("localStorage", v1)
        for scope in (".brands-page", ".journal-page", ".settings-page"):
            self.assertIn(scope, v1)

    def test_theme_names_and_persistence_key_are_unchanged(self):
        settings = self.source("app/templates/settings.html")
        theme_script = self.source("app/static/js/theme.js")
        for theme in ("classic", "klok-green", "bn0024-white"):
            self.assertIn(f'data-theme-option="{theme}"', settings)
        self.assertIn('const STORAGE_KEY = "vechasu-erp-theme-v1";', theme_script)

    def test_global_keyboard_focus_uses_one_visible_ring(self):
        themes = self.source("app/static/css/themes.css")
        focus = themes.split("):focus-visible {", 1)[1].split("}", 1)[0]
        self.assertIn("outline: 2px solid var(--theme-focus)", focus)
        self.assertIn("box-shadow: none", focus)
        self.assertNotIn("0 0 0 4px", focus)


if __name__ == "__main__":
    unittest.main()
