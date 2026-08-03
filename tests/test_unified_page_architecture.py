import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UnifiedPageArchitectureTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_three_pages_use_the_shared_workspace_header(self):
        contracts = {
            "warehouse.html": (
                "Товары",
                "Каталог, цены и текущие остатки",
                "+ Добавить товар",
            ),
            "sales.html": (
                "Продажи",
                "Учёт продаж по всем каналам",
                "Добавить продажу",
            ),
            "receipts.html": (
                "Приход",
                "Приёмка товаров и увеличение остатков",
                "+ Новый приход",
            ),
        }

        for template, expected in contracts.items():
            with self.subTest(template=template):
                source = self.source("app/templates/" + template)
                header = source.split("erp-workspace-header", 1)[1].split(
                    "</header>", 1
                )[0]
                self.assertIn("erp-workspace-heading", header)
                self.assertIn("erp-workspace-actions", header)
                self.assertIn("erp-workspace-primary", header)
                for text in expected:
                    self.assertIn(text, header)

    def test_three_pages_use_the_shared_metrics_contract(self):
        for template in ("warehouse.html", "sales.html", "receipts.html"):
            with self.subTest(template=template):
                source = self.source("app/templates/" + template)
                self.assertIn("erp-workspace-metrics", source)
                self.assertIn("erp-workspace-metric", source)

        warehouse = self.source("app/templates/warehouse.html")
        self.assertIn("Позиций", warehouse)
        self.assertIn("Единиц на складе", warehouse)
        sales = self.source("app/templates/sales.html")
        sales_metrics = sales.split("erp-workspace-metrics", 1)[1].split(
            "</div>\n\n        <section", 1
        )[0]
        self.assertIn("Активных продаж", sales_metrics)
        self.assertNotIn("Не отменено", sales_metrics)

    def test_product_add_action_is_not_inside_the_search_form(self):
        warehouse = self.source("app/templates/warehouse.html")
        search_form = warehouse.split('id="warehouseSearchForm"', 1)[1].split(
            "</form>", 1
        )[0]
        self.assertNotIn("Добавить товар", search_form)
        header = warehouse.split("erp-workspace-header", 1)[1].split(
            "</header>", 1
        )[0]
        self.assertIn("toggleWarehouseAddCard()", header)

    def test_receipt_advanced_filters_are_inside_compact_details_panel(self):
        receipts = self.source("app/templates/receipts.html")
        panel = receipts.split('id="receiptAdvancedFilters"', 1)[1].split(
            "</details>", 1
        )[0]
        for control_id in (
            "receiptDocumentFilter",
            "receiptCommentFilter",
            "receiptBrandFilter",
            "receiptCategoryFilter",
            "receiptProductFilter",
            "receiptStatusFilter",
        ):
            self.assertIn('id="{}"'.format(control_id), panel)
        self.assertIn("resetReceiptFilters()", panel)
        self.assertIn('id="receiptAdvancedFilterCount"', panel)
        self.assertIn("updateReceiptAdvancedFilterCount", receipts)
        self.assertIn('placeholder="Поиск"', receipts)

    def test_shared_css_exposes_workspace_primitives(self):
        css = self.source("app/static/css/erp-components.css")
        for class_name in (
            ".erp-workspace-header",
            ".erp-workspace-heading",
            ".erp-workspace-actions",
            ".erp-workspace-primary",
            ".erp-workspace-metrics",
            ".erp-workspace-metric",
        ):
            self.assertIn(class_name, css)
        self.assertIn(".receipt-advanced-filter-panel", css)
        self.assertIn("grid-template-columns: repeat(2", css)


if __name__ == "__main__":
    unittest.main()
