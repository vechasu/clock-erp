from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProductsFilterPresentationTest(unittest.TestCase):
    def test_products_brand_options_are_loaded_only_inside_compact_dropdown(self):
        template = (ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        brand_call = template.split('"filterBrandCombobox"', 1)[1].split(
            ") }}", 1
        )[0]

        self.assertIn('"brand",\n                [],', brand_call)
        self.assertIn('shared_catalog_kind="brand"', brand_call)
        self.assertIn('search_placeholder="Поиск бренда..."', brand_call)
        self.assertIn(
            "v='catalog-combobox-20260824-product-hierarchy'",
            template,
        )

    def test_children_stay_rendered_and_disabled_until_parent_selection(self):
        template = (ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "app/static/js/catalog-combobox.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('"filterCategoryCombobox"', template)
        self.assertIn("disabled=not selected_brand_id", template)
        self.assertIn('"filterModelCombobox"', template)
        self.assertIn(
            "disabled=not (selected_brand_id and selected_category_id)",
            template,
        )
        self.assertIn('? "Сначала выберите категорию"', script)

    def test_filter_dropdown_has_a_bounded_internal_scroll_area(self):
        template = (ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("#filterDrawer .brand-combobox-options", template)
        self.assertIn("max-height: min(320px, calc(100vh - 220px));", template)
        self.assertIn("overflow-y: auto;", template)
        self.assertIn("overscroll-behavior: contain;", template)


if __name__ == "__main__":
    unittest.main()
