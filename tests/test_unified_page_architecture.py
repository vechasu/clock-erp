import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UnifiedPageArchitectureTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_three_pages_use_the_shared_workspace_header(self):
        contracts = {
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

        warehouse = self.source("app/templates/warehouse.html")
        products_workspace = self.source(
            "app/templates/_products_workspace.html"
        )
        self.assertIn("products_workspace_header", warehouse)
        for text in ("Товары", "Каталог и складские остатки", "+ Добавить товар"):
            self.assertIn(text, products_workspace)

    def test_three_pages_use_the_shared_metrics_contract(self):
        for template in ("sales.html", "receipts.html"):
            with self.subTest(template=template):
                source = self.source("app/templates/" + template)
                self.assertIn("erp-workspace-metrics", source)
                self.assertIn("erp-workspace-metric", source)

        warehouse = self.source("app/templates/_products_workspace.html")
        self.assertIn("products-workspace-metrics", warehouse)
        for label in (
            "Всего моделей",
            "Моделей в наличии",
            "Остаток, шт.",
            "Нет моделей",
        ):
            self.assertIn(label, warehouse)
        sales = self.source("app/templates/sales.html")
        sales_metrics = sales.split("erp-workspace-metrics", 1)[1].split(
            "</div>\n\n        <section", 1
        )[0]
        for label in (
            "Продажи", "Продано единиц", "В обработке", "Отправлено",
        ):
            self.assertIn(label, sales_metrics)
        self.assertNotIn("Активных продаж", sales_metrics)
        self.assertNotIn("Выручка", sales_metrics)
        self.assertNotIn("Средний чек", sales_metrics)
        self.assertNotIn("Отменено", sales_metrics)
        self.assertNotIn("Сейчас в работе", sales)
        components = self.source("app/static/css/erp-components.css")
        self.assertIn("--erp-workspace-metric-count: 4", components)

    def test_product_add_action_is_not_inside_the_search_form(self):
        warehouse = self.source("app/templates/warehouse.html")
        search_form = warehouse.split('id="warehouseSearchForm"', 1)[1].split(
            "</form>", 1
        )[0]
        self.assertNotIn("Добавить товар", search_form)
        macro = self.source("app/templates/_products_workspace.html")
        self.assertIn("toggleWarehouseAddCard()", macro)

    def test_receipt_advanced_filters_are_inside_compact_details_panel(self):
        receipts = self.source("app/templates/receipts.html")
        panel = receipts.split('id="receiptAdvancedFilters"', 1)[1].split(
            "</details>", 1
        )[0]
        for control_id in (
            "receiptBrandFilter",
            "receiptCategoryFilter",
            "receiptProductFilter",
            "receiptFilterProductSearch",
            "receiptStatusFilter",
        ):
            self.assertIn('id="{}"'.format(control_id), panel)
        self.assertNotIn('id="receiptDocumentFilter"', panel)
        self.assertNotIn('id="receiptCommentFilter"', panel)
        self.assertIn("resetReceiptAdvancedFilters()", panel)
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

    def test_active_pages_share_page_header_copy(self):
        contracts = {
            "sales.html": (
                "Продажи", "Учёт продаж по всем каналам",
            ),
            "journal.html": (
                "Журнал",
                "История значимых изменений товаров, продаж и приходов.",
            ),
            "repair.html": (
                "Ремонт", "Учёт ремонтных обращений",
            ),
        }

        for template, expected in contracts.items():
            with self.subTest(template=template):
                source = self.source("app/templates/" + template)
                header = source.split("erp-workspace-header", 1)[1].split(
                    "</header>", 1
                )[0]
                self.assertIn("erp-workspace-heading", header)
                for text in expected:
                    self.assertIn(text, header)

        products_workspace = self.source(
            "app/templates/_products_workspace.html"
        )
        self.assertIn("Товары", products_workspace)
        self.assertIn("Каталог и складские остатки", products_workspace)
        for template in (
            "warehouse.html", "warehouse_brands.html", "warehouse_categories.html"
        ):
            self.assertIn(
                "products_workspace_header",
                self.source("app/templates/" + template),
            )

    def test_existing_tabs_use_shared_visual_contract(self):
        for template in ("sales.html", "journal.html", "repair.html"):
            with self.subTest(template=template):
                source = self.source("app/templates/" + template)
                self.assertIn("erp-section-tabs", source)
                self.assertIn("erp-section-tab", source)

        products_workspace = self.source(
            "app/templates/_products_workspace.html"
        )
        self.assertIn("erp-section-tabs", products_workspace)
        self.assertIn("erp-section-tab", products_workspace)

        css = self.source("app/static/css/erp-components.css")
        contract = css.split(
            "Unified page chrome for products, sales, journal and repairs.",
            1,
        )[1]
        for declaration in (
            "min-height: 42px;",
            "border-bottom: 1px solid var(--erp-border);",
            "white-space: nowrap;",
            "overflow-x: auto;",
            "background: var(--erp-primary);",
            "outline: 2px solid var(--erp-primary);",
        ):
            self.assertIn(declaration, contract)

    def test_active_tab_reveal_stays_page_local(self):
        journal = self.source("app/templates/journal.html")
        self.assertIn('.journal-tab[aria-current="page"]', journal)
        self.assertIn("activeTab.scrollIntoView", journal)

        theme_script = self.source("app/static/js/theme.js")
        self.assertNotIn("revealActiveSectionTab", theme_script)


if __name__ == "__main__":
    unittest.main()
