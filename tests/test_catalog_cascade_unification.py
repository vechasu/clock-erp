from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CatalogCascadeUnificationTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_current_sections_share_one_catalog_combobox_script(self):
        for page in (
            "app/templates/warehouse.html",
            "app/templates/sales.html",
            "app/templates/receipts.html",
        ):
            with self.subTest(page=page):
                source = self.source(page)
                self.assertIn("js/catalog-combobox.js", source)
                self.assertIn("css/catalog-combobox.css", source)

    def test_shared_component_keeps_search_inline_create_and_resets(self):
        source = self.source("app/static/js/catalog-combobox.js")
        self.assertIn("initializeCatalogComboboxComponent", source)
        self.assertIn("catalog-combobox:change", source)
        self.assertIn("initializeSharedCatalogInlineCreation", source)
        self.assertIn("clearSharedCatalogCombobox(category)", source)
        self.assertIn("clearSharedCatalogCombobox(product)", source)

    def test_uncategorized_zero_id_is_not_treated_as_an_empty_selection(self):
        source = self.source("app/static/js/catalog-combobox.js")
        self.assertIn("function sharedCatalogIdValue(value)", source)
        self.assertIn(
            'value === null || value === undefined',
            source,
        )
        self.assertIn('categoryId === ""', source)
        self.assertNotIn("String(item.id || \"\")", source)

    def test_global_category_backend_contract_is_preserved(self):
        source = self.source("app/services/shared_catalog.py")
        self.assertIn("normalized_name", source)
        self.assertIn("list_category_options", source)
        self.assertIn("create_category", source)

    def test_product_and_receipt_creation_use_global_category_options(self):
        for page in (
            "app/templates/warehouse.html",
            "app/templates/receipts.html",
        ):
            with self.subTest(page=page):
                self.assertIn(
                    'data-global-category-options="true"',
                    self.source(page),
                )
        self.assertNotIn(
            'data-global-category-options="true"',
            self.source("app/templates/sales.html"),
        )

    def test_taxonomy_create_action_is_sticky_and_exact_match_safe(self):
        template = self.source("app/templates/_catalog_combobox.html")
        script = self.source("app/static/js/catalog-combobox.js")
        stylesheet = self.source("app/static/css/catalog-combobox.css")
        options_markup = template.split('role="listbox"', 1)[1]
        action_position = options_markup.index("catalog-combobox-action")
        option_position = options_markup.index("{% if include_all %}")

        self.assertLess(action_position, option_position)
        self.assertIn("position: sticky", stylesheet)
        self.assertIn("exactMatch", script)
        self.assertIn("catalogInlineCreating", script)
        self.assertIn("Категории этого бренда", script)
        self.assertIn("Другие категории", script)
        self.assertIn('"новый бренд"', script)
        self.assertIn('"новую категорию"', script)

    def test_base_templates_are_active_and_stage2_pages_are_absent(self):
        for name in ("warehouse.html", "sales.html", "receipts.html"):
            self.assertTrue((ROOT / "app" / "templates" / name).exists())
        for name in (
            "frontend/src/features/products/ProductsPage.tsx",
            "frontend/src/features/sales/SalesPage.tsx",
            "frontend/src/features/receipts/ReceiptsPage.tsx",
        ):
            self.assertFalse((ROOT / name).exists())


if __name__ == "__main__":
    unittest.main()
