import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SalesProductComboboxContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (
            PROJECT_ROOT / "app" / "templates" / "sales.html"
        ).read_text(encoding="utf-8")
        cls.styles = (
            PROJECT_ROOT / "app" / "static" / "css" / "erp-components.css"
        ).read_text(encoding="utf-8")

    def test_searchable_product_control_keeps_url_backed_select(self):
        self.assertIn('id="salesProductFilter"', self.template)
        self.assertIn('role="combobox"', self.template)
        self.assertIn('role="listbox"', self.template)
        self.assertIn(
            'placeholder="Поиск товара по названию или артикулу..."',
            self.template,
        )
        self.assertIn("populateProductValue(values.product_id)", self.template)
        self.assertIn("product_id: salesProductFilter.value", self.template)

    def test_live_search_uses_name_and_article_without_page_navigation(self):
        self.assertIn(
            'search: `${item.product || ""} ${item.article || ""}`',
            self.template,
        )
        self.assertIn(
            'salesProductComboboxSearch.addEventListener("input"',
            self.template,
        )
        self.assertIn('empty.textContent = "Ничего не найдено"', self.template)
        search_handler = self.template.split(
            'salesProductComboboxSearch.addEventListener("input"', 1
        )[1].split("});", 1)[0]
        self.assertNotIn("navigateSales", search_handler)

    def test_product_options_are_bounded_and_distinguished_by_article(self):
        self.assertIn("matches.slice(0, 100)", self.template)
        self.assertIn("`${name} · ${article}`", self.template)
        self.assertIn("const options = new Map()", self.template)
        self.assertIn("options.has(value)", self.template)

    def test_keyboard_outside_click_and_scroll_contracts_are_present(self):
        self.assertIn('event.key !== "Escape"', self.template)
        self.assertIn("event.stopPropagation()", self.template)
        self.assertIn("!salesProductCombobox.contains(event.target)", self.template)
        self.assertIn("max-height: min(280px, 38vh)", self.styles)
        self.assertIn("overflow-y: auto", self.styles)


if __name__ == "__main__":
    unittest.main()
