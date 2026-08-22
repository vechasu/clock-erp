import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductsRedesignStructureTest(unittest.TestCase):
    def source(self, name):
        return (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")

    def test_four_tabs_are_persistent_on_all_catalog_screens(self):
        workspace = self.source("_products_workspace.html")
        for name in (
            "warehouse.html", "warehouse_analytics.html", "warehouse_brands.html",
            "warehouse_categories.html"
        ):
            source = self.source(name)
            self.assertIn("products_workspace_header", source)
            self.assertNotIn("Часы закончились", source)
        for label in ("Товары", "Бренды", "Категории", "Аналитика"):
            self.assertIn(label, workspace)
        self.assertNotIn("('products', 'В наличии'", workspace)
        self.assertNotIn("('out_of_stock', 'Нет в наличии'", workspace)

    def test_products_header_and_actions_match_compact_design(self):
        source = self.source("warehouse.html")
        workspace = self.source("_products_workspace.html")
        self.assertIn("Каталог и складские остатки", workspace)
        self.assertIn("products-workspace-metrics", workspace)
        self.assertNotIn("Экспортировать найденные", workspace)
        self.assertNotIn("Экспортировать все товары", workspace)
        self.assertIn('name="stock_state"', source)
        self.assertIn(">Инвентаризация</a>", workspace)
        self.assertIn("+ Добавить товар", workspace)
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

    def test_product_detail_inputs_have_accessible_names(self):
        products = self.source("warehouse.html")
        for label in (
            "Название товара", "Артикул товара", "Модель товара", "Цена товара",
            "Остаток товара", "Складская ячейка товара", "Заменить фото товара",
        ):
            self.assertIn('aria-label="{}"'.format(label), products)

    def test_table_has_synchronized_top_scrollbar_and_mobile_overflow(self):
        products = self.source("warehouse.html")
        css = (ROOT / "app/static/css/products-workspace.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="warehouseTableScrollTop"', products)
        self.assertIn('id="warehouseTableScrollBody"', products)
        self.assertIn("initializeWarehouseTableScrollbars", products)
        self.assertIn("target.scrollLeft = source.scrollLeft", products)
        self.assertIn("overflow-x: auto !important", css)
        self.assertIn("contain: layout paint inline-size", css)
        self.assertIn("overflow-x: clip", css)
        self.assertIn("display: table !important", css)
        self.assertIn(".warehouse-page .warehouse-column-settings", css)
        self.assertIn("display: block !important", css)

    def test_tab_state_is_namespaced_by_compatible_view(self):
        script = (ROOT / "app/static/js/products-tabs.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("allowedParameters", script)
        self.assertIn('brands: ["q", "show_empty", "brand_id"]', script)
        self.assertIn("storagePrefix + view", script)
        self.assertIn('url.searchParams.delete("view")', script)
        self.assertIn("event.preventDefault()", script)
        self.assertIn("await fetch(targetUrl", script)
        self.assertIn("document.write(html)", script)
        self.assertIn('window.addEventListener("popstate"', script)

    def test_out_of_stock_checks_tolerate_an_empty_cycle(self):
        products = self.source("warehouse.html")
        self.assertIn(
            'item.out_of_stock_cycle.get("checks", {})', products
        )


if __name__ == "__main__":
    unittest.main()
