import re
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
        column_settings = source.split(
            "function initializeWarehouseColumnSettings", 1
        )[1].split("function initializeWarehouseTableView", 1)[0]

        self.assertIn('id="warehouseColumnSettingsTrigger"', toolbar)
        self.assertIn('id="warehouseColumnSettingsPanel"', toolbar)
        self.assertNotIn('id="warehouseMoreMenu"', toolbar)
        self.assertNotIn(">Ещё<", toolbar)
        self.assertIn('reset.id = "warehouseTableReset"', column_settings)
        self.assertIn(
            'reset.textContent = "Сбросить вид таблицы"',
            column_settings,
        )
        self.assertLess(
            column_settings.index("warehouse-column-settings-divider"),
            column_settings.index('reset.id = "warehouseTableReset"'),
        )
        self.assertNotIn('class="warehouse-table-toolbar"', source)

    def test_stock_filter_is_replaced_by_persistent_tabs(self):
        source = self.source("app/templates/warehouse.html")
        stock_header = source.split(
            '<th data-column-key="stock">', 1
        )[1].split("</th>", 1)[0]
        self.assertNotIn("warehouseInStockToggle", stock_header)
        self.assertNotIn("stock-mini-toggle", stock_header)
        self.assertNotIn('id="warehouseFilterInStock"', source)
        self.assertNotIn('name="in_stock"', source)
        self.assertNotIn("Только в наличии", source)
        workspace = self.source("app/templates/_products_workspace.html")
        self.assertIn("'out_of_stock'", workspace)

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
        self.assertIn('<col data-column-key="purchase-price">', source)
        self.assertIn('"purchase-price": 126', source)
        self.assertIn("normalizeWidths(saved.widths)", source)
        self.assertIn("Number.isFinite(numeric) && numeric > 0", source)
        self.assertIn("panel.replaceChildren()", source)
        self.assertIn("receipt-column-settings-reset", source)
        self.assertIn("saveReceiptTableView();\n        initializeReceiptColumnSettings()", source)

    def test_table_storage_isolated_and_ajax_initializer_is_idempotent(self):
        warehouse = self.source("app/templates/warehouse.html")
        sales = self.source("app/templates/sales.html")
        receipts = self.source("app/templates/receipts.html")

        self.assertIn("vechasu.warehouse.table-view.v2", warehouse)
        self.assertIn('data-sales-settings-key="sales_{{ active_source }}"', sales)
        self.assertIn("vechasu-receipts-table-view-v1", receipts)
        self.assertIn("warehouseTableViewController.abort()", warehouse)
        self.assertIn('delete table.dataset.viewReady', warehouse)
        self.assertIn("initializeWarehouseTableView();", warehouse)
        self.assertIn('if (table.dataset.viewReady === "1") return;', warehouse)

    def test_resize_completion_suppresses_sort_and_cleans_pointer_handlers(self):
        contracts = {
            "warehouse.html": "warehouseTableSuppressSortUntil",
            "sales.html": "suppressSortUntil",
            "receipts.html": "suppressSortUntil",
        }
        for template, guard in contracts.items():
            with self.subTest(template=template):
                source = self.source("app/templates/" + template)
                self.assertIn(guard + " = Date.now() + 350", source)
                self.assertRegex(
                    source,
                    re.compile(r'removeEventListener\(\s*"pointerup"'),
                )
                self.assertRegex(
                    source,
                    re.compile(r'removeEventListener\(\s*"pointercancel"'),
                )

    def test_wide_tables_are_contained_by_their_scroll_owners(self):
        css = self.source("app/static/css/erp-components.css")
        self.assertIn(".warehouse-page #warehouseResults", css)
        self.assertIn(".sales-page .sales-data-card", css)
        self.assertIn(".table-card:has(.receipts-table)", css)
        self.assertIn(".sales-page .table-wrap:has(.sales-table)", css)
        self.assertIn("overscroll-behavior-x: contain", css)


if __name__ == "__main__":
    unittest.main()
