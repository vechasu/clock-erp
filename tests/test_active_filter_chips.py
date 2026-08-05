import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ActiveFilterChipsTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def setUp(self):
        self.warehouse = self.source("app/templates/warehouse.html")
        self.sales = self.source("app/templates/sales.html")
        self.receipts = self.source("app/templates/receipts.html")
        self.css = self.source("app/static/css/erp-components.css")

    def test_two_warehouse_filters_render_as_separate_typed_chips(self):
        self.assertIn("Бренд: {{ selected_brand }}", self.warehouse)
        self.assertIn("Категория: {{ selected_category }}", self.warehouse)
        self.assertIn('data-warehouse-filter="brand"', self.warehouse)
        self.assertIn('data-warehouse-filter="category"', self.warehouse)

    def test_warehouse_chip_removes_only_its_own_url_parameters(self):
        self.assertIn('brand: ["brand", "brand_id"]', self.warehouse)
        self.assertIn(
            'category: ["category", "category_id"]',
            self.warehouse,
        )
        self.assertIn('url.searchParams.delete("page")', self.warehouse)

    def test_reset_all_is_only_rendered_for_two_or_more_filters(self):
        self.assertIn(
            "{% if warehouse_active_filter_count >= 2 %}",
            self.warehouse,
        )
        self.assertIn("if (filters.length >= 2)", self.sales)
        self.assertIn("if (count >= 2)", self.receipts)

    def test_active_filter_rows_hide_when_empty(self):
        for source, container_id in (
            (self.warehouse, "warehouseActiveFilters"),
            (self.sales, "salesActiveFilters"),
            (self.receipts, "receiptActiveFilters"),
        ):
            with self.subTest(container_id=container_id):
                self.assertIn('id="{}"'.format(container_id), source)
                self.assertIn("erp-active-filters", source)
        self.assertIn("container.hidden = filters.length === 0", self.sales)
        self.assertIn("container.hidden = count === 0", self.receipts)

    def test_large_primary_toolbar_reset_is_removed(self):
        self.assertNotIn('id="warehouseFilterReset"', self.warehouse)
        self.assertNotIn("× Сбросить", self.warehouse)
        self.assertNotIn('id="clearSalesDateFilter"', self.sales)
        self.assertNotIn("filter-pill", self.warehouse)

    def test_filter_badges_update_from_the_same_active_values(self):
        self.assertIn("updateSalesFilterSummary();", self.sales)
        self.assertIn("renderSalesActiveFilters();", self.sales)
        self.assertIn("updateReceiptAdvancedFilterCount();", self.receipts)
        self.assertIn("badge.textContent = String(count)", self.receipts)

    def test_removing_filters_updates_url_rows_and_statistics(self):
        self.assertIn("syncFilterUrls(selectedFrom, selectedTo)", self.sales)
        self.assertIn("updateVisibleStats(visibleRows)", self.sales)
        self.assertIn("window.history.replaceState({}, \"\", url)", self.receipts)
        self.assertIn('"receiptStatDocuments"', self.receipts)
        self.assertIn('"receiptStatQuantity"', self.receipts)

    def test_receipt_filter_urls_are_restored_and_cleared_individually(self):
        for parameter in (
            "receipt_document",
            "receipt_comment",
            "receipt_brand",
            "receipt_category",
            "receipt_product",
            "receipt_status",
        ):
            self.assertIn(parameter, self.receipts)
        self.assertIn("url.searchParams.delete(parameter)", self.receipts)
        self.assertIn("receiptUrlFilters.has(parameter)", self.receipts)

    def test_search_and_stock_toggle_remain_available(self):
        self.assertIn('id="warehouseSearchInput"', self.warehouse)
        self.assertIn('id="warehouseInStockToggle"', self.warehouse)
        reset = self.warehouse.split(
            "function resetWarehouseTableFilters()", 1
        )[1].split("function clearWarehouseFilter", 1)[0]
        self.assertNotIn('"q"', reset)
        self.assertIn('in_stock: ["in_stock"]', self.warehouse)

    def test_shared_chip_design_wraps_and_is_keyboard_native(self):
        self.assertIn(".erp-active-filters", self.css)
        self.assertIn("flex-wrap: wrap", self.css)
        self.assertIn("max-width: 100%", self.css)
        self.assertIn(".erp-filter-chip:focus-visible", self.css)
        for source in (self.warehouse, self.sales, self.receipts):
            self.assertIn('type="button"', source)

    def test_period_is_one_logical_filter_in_every_section(self):
        self.assertIn(
            "bool(created_date_from or created_date_to)",
            self.source("app/web.py"),
        )
        self.assertIn(
            "salesDateFrom?.value || salesDateTo?.value",
            self.sales,
        )
        self.assertIn(
            "activeFilters.length + (periodLabel ? 1 : 0)",
            self.receipts,
        )

    def test_period_labels_cover_both_open_ended_variants(self):
        for source in (self.warehouse, self.sales, self.receipts):
            with self.subTest(source=source[:20]):
                self.assertIn("Период: с ", source)
                self.assertIn("Период: до ", source)
        self.assertIn('"–" + displaySalesDate(dateTo)', self.sales)
        self.assertIn('"–" + formatDate(dateTo)', self.receipts)

    def test_period_chip_clears_both_dates_only(self):
        sales_period = self.sales.split(
            'else {\n                    salesDateFrom.value = "";', 1
        )[1].split("updateSalesFilterSummary();", 1)[0]
        self.assertIn('salesDateTo.value = ""', sales_period)
        self.assertNotIn("appliedSaleState", sales_period)

        receipt_period = self.receipts.split(
            'button.dataset.receiptFilter = "period";', 1
        )[1].split("container.appendChild(button);", 1)[0]
        self.assertIn("clearReceiptPeriodFilter();", receipt_period)
        receipt_clear = self.receipts.split(
            "function clearReceiptPeriodFilter()", 1
        )[1].split("function receiptPeriodFilterLabel", 1)[0]
        self.assertIn('"receiptDateFrom").value = ""', receipt_clear)
        self.assertIn('"receiptDateTo").value = ""', receipt_clear)
        self.assertIn("receiptPeriodPicker?.updateLabel()", receipt_clear)

    def test_reset_all_includes_period_and_stock_but_not_search_or_sort(self):
        warehouse_reset = self.warehouse.split(
            "function resetWarehouseTableFilters()", 1
        )[1].split("function clearWarehouseFilter", 1)[0]
        for parameter in ("date_from", "date_to", "in_stock"):
            self.assertIn('"{}"'.format(parameter), warehouse_reset)
        for parameter in ('"q"', '"sort_by"', '"sort_dir"'):
            self.assertNotIn(parameter, warehouse_reset)
        self.assertIn("resetSalesActiveFilters", self.sales)
        self.assertIn("resetReceiptActiveFilters", self.receipts)

    def test_stock_chip_matches_visible_toggle_and_is_not_in_sales(self):
        self.assertIn(
            '<span class="erp-filter-chip-label">Только в наличии</span>',
            self.warehouse,
        )
        self.assertIn('id="warehouseInStockToggle"', self.warehouse)
        self.assertIn('in_stock: ["in_stock"]', self.warehouse)
        self.assertNotIn('data-sales-filter="in_stock"', self.sales)


if __name__ == "__main__":
    unittest.main()
