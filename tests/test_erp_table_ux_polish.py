import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ErpTableUxPolishTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_products_use_one_toolbar_without_empty_table_floor(self):
        source = self.source("app/templates/warehouse.html")
        toolbar = source.split('id="warehouseSearchForm"', 1)[1].split(
            "</form>", 1
        )[0]
        more = toolbar.split('id="warehouseMoreDropdown"', 1)[1]

        self.assertIn('id="warehouseColumnSettingsTrigger"', toolbar)
        self.assertIn('id="warehouseColumnSettingsPanel"', toolbar)
        self.assertIn('id="warehouseTableReset"', more)
        self.assertIn("Сбросить вид таблицы", more)
        self.assertNotIn('class="warehouse-table-toolbar"', source)

    def test_stock_filter_is_in_drawer_not_stock_header(self):
        source = self.source("app/templates/warehouse.html")
        stock_header = source.split(
            '<th data-column-key="stock">', 1
        )[1].split("</th>", 1)[0]
        drawer = source.split('id="filterDrawer"', 1)[1].split(
            "</aside>", 1
        )[0]

        self.assertNotIn("warehouseInStockToggle", stock_header)
        self.assertNotIn("stock-mini-toggle", stock_header)
        self.assertIn('id="warehouseFilterInStock"', drawer)
        self.assertIn('name="in_stock"', drawer)
        self.assertIn("Только в наличии", drawer)

    def test_resize_scroll_and_action_column_contracts_remain(self):
        contracts = {
            "warehouse.html": "warehouse-column-resize-handle",
            "sales.html": "sales-column-resize-handle",
            "receipts.html": "receipt-column-resize-handle",
        }
        for template, handle in contracts.items():
            with self.subTest(template=template):
                source = self.source("app/templates/" + template)
                self.assertIn(handle, source)
                self.assertIn("data-erp-scroll-hint", source)
                self.assertIn('tabindex="0"', source)
                self.assertIn("js/table-scroll-hint.js", source)

        warehouse = self.source("app/templates/warehouse.html")
        self.assertIn('data-column-key="actions"', warehouse)
        for template in ("sales.html", "receipts.html"):
            source = self.source("app/templates/" + template)
            self.assertIn('data-system-column="actions"', source)

        script = self.source("app/static/js/table-scroll-hint.js")
        self.assertIn("scrollWidth - container.clientWidth", script)
        self.assertIn("is-at-scroll-end", script)
        self.assertIn('event.key !== "ArrowLeft"', script)

    def test_table_headers_and_actions_use_shared_quiet_styles(self):
        css = self.source("app/static/css/erp-components.css")
        self.assertIn(
            ".warehouse-products-table.erp-data-table thead th",
            css,
        )
        self.assertIn(".sales-table.erp-data-table thead th", css)
        self.assertIn(".receipts-table.erp-data-table thead th", css)
        self.assertIn("border-radius: 0 !important", css)
        self.assertIn(".erp-scroll-hint.has-horizontal-overflow", css)
        self.assertIn(".receipt-delete-button:hover", css)
        self.assertIn(".sale-row.is-cancelled td", css)

    def test_report_labels_are_short_and_secondary(self):
        sales = self.source("app/templates/sales.html")
        receipts = self.source("app/templates/receipts.html")
        sales_header = sales.split('id="salesReportLink"', 1)[1].split(
            "</a>", 1
        )[0]
        receipt_toolbar = receipts.split(
            'class="toolbar receipt-filter-toolbar', 1
        )[1].split("</div>\n\n        <section", 1)[0]

        self.assertIn("erp-secondary-action", sales_header)
        self.assertIn("data-report-label>Отчёт", sales_header)
        self.assertNotIn("Сформировать отчёт", sales_header)
        self.assertIn("receipt-report-button erp-secondary-action", receipt_toolbar)
        self.assertIn(">\n                Отчёт\n", receipt_toolbar)

    def test_receipt_column_visibility_reuses_width_storage(self):
        source = self.source("app/templates/receipts.html")
        toolbar = source.split(
            'class="toolbar receipt-filter-toolbar', 1
        )[1].split("</div>\n\n        <section", 1)[0]

        self.assertIn('id="receiptColumnSettingsTrigger"', toolbar)
        self.assertIn('id="receiptColumnSettingsPanel"', toolbar)
        self.assertIn("vechasu-receipts-table-view-v1", source)
        self.assertIn("JSON.stringify({widths, hidden})", source)
        self.assertIn('requiredColumns = ["date", "product"]', source)
        self.assertIn('data-system-column="actions"', source)


if __name__ == "__main__":
    unittest.main()
