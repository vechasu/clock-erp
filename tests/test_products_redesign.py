import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductsRedesignStructureTest(unittest.TestCase):
    def source(self, name):
        return (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")

    def test_four_tabs_are_persistent_on_all_catalog_screens(self):
        for name in (
            "warehouse.html", "warehouse_brands.html", "warehouse_categories.html"
        ):
            source = self.source(name)
            for label in ("В наличии", "Нет в наличии", "Бренды", "Категории"):
                self.assertIn(label, source)
            self.assertIn("warehouse-tab-badge", source)
            self.assertNotIn("Часы закончились", source)

    def test_products_header_and_actions_match_compact_design(self):
        source = self.source("warehouse.html")
        self.assertIn("Каталог и складские остатки", source)
        self.assertIn("warehouse-compact-summary", source)
        self.assertIn("Экспортировать найденные", source)
        self.assertIn("Экспортировать все товары", source)
        self.assertIn(">Инвентаризация</a>", source)
        self.assertIn("+ Добавить товар", source)
        self.assertNotIn("Только в наличии", source)

    def test_brand_and_category_lists_offer_empty_toggle_and_correct_metrics(self):
        brands = self.source("warehouse_brands.html")
        categories = self.source("warehouse_categories.html")
        self.assertIn("Показать пустые", brands)
        self.assertIn("Показать пустые", categories)
        for label in ("Позиций", "В наличии", "Единиц", "Категорий"):
            self.assertIn(label, brands)
        for label in ("Брендов", "Позиций", "В наличии", "Единиц"):
            self.assertIn(label, categories)
        self.assertNotIn('<div class="category-heading"><h2>Категории</h2>', categories)

    def test_image_layout_is_contained_and_responsive(self):
        products = self.source("warehouse.html")
        brands = self.source("warehouse_brands.html")
        self.assertIn("width: 48px", products)
        self.assertIn("height: 48px", products)
        self.assertIn("object-fit: contain", products)
        self.assertIn("width:40px", brands)
        self.assertIn("height:40px", brands)
        self.assertIn("overflow-x: auto", products)


if __name__ == "__main__":
    unittest.main()
