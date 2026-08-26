import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SalesListDesignSystemContractTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_list_uses_shared_erp_primitives(self):
        template = self.source("app/templates/sales.html")
        for contract in (
            'body class="sales-page sales-list-page"',
            "sales-page-header erp-workspace-header",
            "sales-tabs erp-section-tabs",
            "sales-tab erp-section-tab",
            "sales-kpis erp-stats erp-workspace-metrics",
            "sales-data-toolbar erp-toolbar-card erp-toolbar",
            "sales-active-filters erp-active-filters",
            "erp-table-card erp-table-shell",
            "erp-table-scroll erp-scroll-hint",
            "sales-table erp-data-table",
            "sales-empty-state {{ 'erp-no-results-state' if has_sales_query_state else 'erp-empty-state' }}",
            "mobile-erp-card sales-mobile-card",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, template)

    def test_channels_kpis_and_one_primary_list_action_are_preserved(self):
        template = self.source("app/templates/sales.html")
        web = self.source("app/web.py")
        for label in (
            "Все продажи", "Tictactoy", "Wildberries", "Amazon",
            "Продажи", "Продано единиц", "В обработке", "Отправлено",
        ):
            with self.subTest(label=label):
                self.assertIn(label, template + web)
        metrics = template.split("sales-kpis", 1)[1].split(
            "</div>\n\n        <section", 1
        )[0]
        self.assertEqual(metrics.count("stat-card erp-stat-card"), 4)
        header = template.split("sales-page-header", 1)[1].split(
            "</header>", 1
        )[0]
        self.assertEqual(header.count("erp-workspace-primary"), 1)
        self.assertIn("Добавить продажу", header)

    def test_filters_sort_columns_focus_and_pagination_contract_survive(self):
        template = self.source("app/templates/sales.html")
        for marker in (
            'id="salesSearch"', 'id="salesDateFrom"', 'id="salesDateTo"',
            'id="salesFilterTrigger"', 'id="salesColumnSettingsTrigger"',
            'id="salesFocusModeToggle"', "data-sort-field",
            "data-column-visibility-key", "sales-column-resize-handle",
            "vechasu.erp.sales.focus-mode.v1",
            "vechasu-sales-table-scroll-x",
            'render_erp_pagination(pagination, "Страницы продаж")',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, template)
        web = self.source("app/web.py")
        for query_key in (
            '"q"', '"date_from"', '"date_to"', '"sort"', '"sort_dir"',
            '"brand_id"', '"category_id"', '"product_id"', '"status"',
            '"per_page"', '"today"',
        ):
            self.assertIn(query_key, web)

    def test_active_filters_have_individual_and_single_global_reset(self):
        template = self.source("app/templates/sales.html")
        script = template.split("function renderSalesActiveFilters", 1)[1]
        self.assertIn("erp-filter-chip", script)
        self.assertIn("erp-filter-chip-remove", script)
        self.assertIn("erp-active-filters-reset", script)
        self.assertIn("container.hidden = filters.length === 0", script)
        self.assertEqual(script.count('reset.textContent = "Сбросить всё"'), 1)

    def test_table_photos_empty_states_and_write_flows_are_unchanged(self):
        template = self.source("app/templates/sales.html")
        self.assertIn("sales-product-thumb", template)
        self.assertIn('loading="lazy"', template)
        self.assertIn("sales_product_images", template)
        self.assertIn('"missing-product-photo"', self.source("app/web.py"))
        self.assertIn("Продаж пока нет", template)
        self.assertIn("Ничего не найдено", template)
        for endpoint in (
            "sale_archive", "manual_sale_add", "manual_sale_update",
            "sale_return", "sale_cancel", "sale_delete",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, template)
        self.assertGreaterEqual(template.count('name="csrf_token"'), 6)

    def test_edit_sale_action_is_compact_accessible_and_keeps_existing_flow(self):
        template = self.source("app/templates/sales.html")
        desktop_action = template.split(
            'class="sales-edit-action sales-row-edit"', 1
        )[1].split("</button>", 1)[0]
        self.assertIn('aria-label="Редактировать продажу"', desktop_action)
        self.assertIn('data-tooltip="Редактировать продажу"', desktop_action)
        self.assertIn('onclick="openSaleEditor(this)"', desktop_action)
        self.assertIn('data-sale="{{ sale|tojson|forceescape }}"', desktop_action)
        self.assertNotIn("sales-edit-label", desktop_action)
        self.assertNotIn(">Редактировать<", desktop_action)

        for contract in (
            "width: 116px;",
            "width: 44px;",
            "height: 44px;",
            "gap: 8px;",
            "const actionColumnWidth = () => 116;",
            ".sales-edit-action[data-tooltip]:hover::after",
            ".sales-edit-action[data-tooltip]:focus-visible::after",
            ".sales-edit-action:active:not(:disabled)",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, template)

    def test_shared_css_is_tokenized_responsive_and_bounds_overflow(self):
        css = self.source("app/static/css/erp-components.css")
        contract = css.split(
            "Sales list workspace: shared ERP component contract.", 1
        )[1].split("Settings workspace: shared ERP component contract.", 1)[0]
        for token in (
            "var(--erp-canvas)", "var(--erp-surface)",
            "var(--erp-border)", "var(--erp-text)",
            "var(--erp-primary)", "var(--erp-focus-ring)",
            "var(--erp-control-height)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, contract)
        self.assertIn("grid-template-columns: repeat(4", contract)
        self.assertIn("overflow-x: auto", contract)
        self.assertIn("overscroll-behavior-x: contain", contract)
        self.assertIn("@media (max-width: 1100px)", contract)
        self.assertIn("@media (max-width: 767px)", contract)
        self.assertIn("@media (max-width: 560px)", contract)
        self.assertIn("@media (max-width: 370px)", contract)


if __name__ == "__main__":
    unittest.main()
