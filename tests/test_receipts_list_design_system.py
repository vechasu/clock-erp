import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReceiptsListDesignSystemContractTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_list_uses_shared_erp_primitives(self):
        template = self.source("app/templates/receipts.html")
        for contract in (
            'body class="receipts-page receipts-list-page"',
            "receipt-page-header erp-workspace-header",
            "receipt-kpis erp-stats erp-workspace-metrics",
            "receipt-data-toolbar erp-toolbar-card erp-toolbar",
            "erp-active-filters",
            "receipt-data-card erp-table-card erp-table-shell",
            "erp-table-scroll erp-scroll-hint",
            "receipts-table erp-data-table",
            "receipt-empty-state erp-empty-state",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, template)

    def test_header_kpis_and_existing_list_actions_are_preserved(self):
        template = self.source("app/templates/receipts.html")
        header = template.split("receipt-page-header", 1)[1].split(
            "</header>", 1
        )[0]
        self.assertIn("Приход", header)
        self.assertIn("Приёмка товаров и увеличение остатков", header)
        self.assertIn("Импорт Excel", header)
        self.assertIn("excel_receipt_new", header)
        self.assertIn("+ Новый приход", header)
        self.assertEqual(header.count("erp-workspace-primary"), 1)
        metrics = template.split("receipt-kpis", 1)[1].split(
            "</section>", 1
        )[0]
        self.assertEqual(metrics.count("stat-card erp-stat-card"), 2)
        self.assertIn("Документов прихода", metrics)
        self.assertIn("Принято единиц", metrics)
        self.assertIn("openReceiptsReport()", template)

    def test_filters_period_sort_columns_and_pagination_survive(self):
        template = self.source("app/templates/receipts.html")
        for marker in (
            'id="receiptSearch"',
            'id="receiptDateFrom"',
            'id="receiptDateTo"',
            'id="receiptAdvancedFilters"',
            'id="receiptAdvancedFilterCount"',
            'id="receiptColumnSettingsTrigger"',
            'id="receiptMobileSort"',
            "data-receipt-sort",
            "receipt-column-resize-handle",
            "vechasu-receipts-table-view-v1",
            'render_erp_pagination(pagination, "Страницы приходов")',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, template)
        for parameter in (
            "receipt_brand_id",
            "receipt_category_id",
            "receipt_product_id",
            "receipt_status",
            "date_from",
            "date_to",
            "sort",
            "sort_dir",
            "per_page",
        ):
            self.assertIn(parameter, template + self.source("app/web.py"))

    def test_chips_table_content_and_empty_states_are_explicit(self):
        template = self.source("app/templates/receipts.html")
        chips = template.split(
            "function updateReceiptAdvancedFilterCount", 1
        )[1].split("function filterReceipts", 1)[0]
        self.assertIn("erp-filter-chip", chips)
        self.assertIn("erp-filter-chip-remove", chips)
        self.assertEqual(chips.count('reset.textContent = "Сбросить всё"'), 1)
        self.assertIn("container.hidden = count === 0", chips)
        for content in (
            "receipt-product-thumb",
            "receipt-product-article",
            "receipt-line-total",
            'receipt.number or "—"',
            "status-badge--cancelled",
            "receipt-moysklad-button",
            "Ничего не найдено",
            "Приходов пока нет",
        ):
            self.assertIn(content, template)

    def test_shared_css_contains_overflow_and_responsive_contract(self):
        css = self.source("app/static/css/erp-components.css")
        contract = css.split(
            "Receipts list workspace: shared ERP component contract.", 1
        )[1].split(
            "Settings workspace: shared ERP component contract.", 1
        )[0]
        for token in (
            "var(--erp-canvas)",
            "var(--erp-surface)",
            "var(--erp-border)",
            "var(--erp-text)",
            "var(--erp-primary-soft)",
        ):
            self.assertIn(token, contract)
        self.assertIn("overflow-x: auto", contract)
        self.assertIn("overscroll-behavior-x: contain", contract)
        self.assertIn("position: sticky", contract)
        self.assertIn("@media (max-width: 767px)", contract)
        self.assertIn("@media (max-width: 370px)", contract)

    def test_document_stays_optional_and_write_forms_are_unchanged(self):
        template = self.source("app/templates/receipts.html")
        document = template.split('id="document_number"', 1)[1]
        self.assertNotIn("required", document.split(">", 1)[0])
        for endpoint in (
            'action="/receipts/create"',
            'action="/receipts/delete"',
            '"/api/v1/receipts/"',
            '"PATCH"',
        ):
            self.assertIn(endpoint, template)


if __name__ == "__main__":
    unittest.main()
