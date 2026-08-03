import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UnifiedErpVisualSystemTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_shared_typography_tokens_cover_active_erp(self):
        css = self.source("app/static/css/erp-components.css")
        expected = {
            "--erp-font-family": 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            "--erp-type-page-title-size": "28px",
            "--erp-type-page-title-weight": "800",
            "--erp-type-metric-value-size": "24px",
            "--erp-type-control-size": "13px",
            "--erp-type-table-head-size": "12px",
            "--erp-type-table-cell-size": "13px",
            "--erp-type-modal-title-size": "24px",
            "--erp-type-section-title-size": "15px",
            "--erp-type-field-value-size": "14px",
        }
        for token, value in expected.items():
            with self.subTest(token=token):
                self.assertIn(f"{token}: {value}", css)

        settings = self.source("app/templates/settings.html")
        self.assertIn('class="settings-page"', settings)
        self.assertIn("css/erp-components.css", settings)

    def test_three_tables_share_one_authoritative_header_contract(self):
        css = self.source("app/static/css/erp-components.css")
        contract = css.split(
            "Final table contract stays authoritative", 1
        )[1]
        for selector in (
            ".warehouse-products-table.erp-data-table thead th",
            ".sales-table.erp-data-table thead th",
            ".receipts-table.erp-data-table thead th",
        ):
            self.assertIn(selector, contract)
        self.assertIn("background: #f3f6fa", contract)
        self.assertIn("background: transparent !important", contract)
        self.assertIn("height: 46px", contract)
        self.assertIn("border-radius: 0 !important", contract)
        self.assertIn("height: 72px", contract)

        for template in ("warehouse.html", "sales.html", "receipts.html"):
            source = self.source("app/templates/" + template)
            self.assertIn("erp-data-table", source)
            self.assertIn("column-resize-handle", source)
            self.assertIn("data-erp-scroll-hint", source)

    def test_product_add_form_is_one_modal_overlay(self):
        source = self.source("app/templates/warehouse.html")
        modal = source.split('id="warehouseAddModal"', 1)[1].split(
            'id="warehouseBulkPanel"', 1
        )[0]
        self.assertIn("data-erp-modal-shell", modal)
        self.assertIn("data-erp-modal-lock", modal)
        self.assertIn('role="dialog"', modal)
        self.assertIn('aria-modal="true"', modal)
        self.assertIn('id="warehouseAddCard"', modal)
        self.assertEqual(modal.count('id="warehouseAddForm"'), 1)
        self.assertIn("closeWarehouseAddModal()", modal)
        self.assertNotIn(" is-hidden", modal)
        self.assertIn('id="openWarehouseAddModal"', source)
        self.assertIn("document.body.classList.add(\"modal-open\")", source)

    def test_shared_modal_shell_keeps_page_visible_and_locked(self):
        css = self.source("app/static/css/erp-components.css")
        themes = self.source("app/static/css/themes.css")
        modal_css = css.split("Unified ERP visual system", 1)[1].split(
            "Final table contract", 1
        )[0]
        self.assertIn("background: rgba(15, 23, 42, 0.4)", modal_css)
        self.assertIn("backdrop-filter: blur(2px)", modal_css)
        self.assertIn("max-height: 88dvh", modal_css)
        self.assertIn("overflow-y: auto", modal_css)
        self.assertIn("position: sticky", modal_css)
        self.assertIn("height: 100dvh", modal_css)
        self.assertIn("align-items: stretch", modal_css)
        self.assertNotIn("\n    .modal,", themes)
        self.assertIn(".modal:not([data-erp-modal-shell])", themes)

        script = self.source("app/static/js/erp-modal-shell.js")
        self.assertIn('event.key === "Escape"', script)
        self.assertIn("event.stopImmediatePropagation()", script)
        self.assertIn("event.target === modal", script)
        self.assertIn('event.key !== "Tab"', script)
        self.assertIn("last.focus()", script)
        self.assertIn("first.focus()", script)

    def test_sale_source_is_compact_and_inside_main_modal(self):
        source = self.source("app/templates/sales.html")
        modal = source.split('id="manualSaleModal"', 1)[1].split(
            'id="saleReturnModal"', 1
        )[0]
        self.assertIn("data-erp-modal-shell", modal)
        self.assertIn('id="saleSourceChoice"', modal)
        self.assertIn('id="manualSaleForm"', modal)
        self.assertNotIn('class="sale-form is-hidden"', modal)
        self.assertNotIn('id="cancelManualSaleModal"', modal)
        for source_key in ("tictactoy", "wildberries", "amazon"):
            self.assertIn(f'data-sale-source="{source_key}"', modal)
        self.assertIn("vechasu-sales-last-source", source)
        self.assertIn("Object.hasOwn(sourceLabels, preferredSource)", source)
        self.assertIn("button.classList.toggle(\"is-active\", active)", source)
        for section in ("Товар", "Продажа", "Данные канала", "Дополнительно"):
            self.assertIn(section, modal)

    def test_receipt_modal_preserves_import_photo_and_submit_contracts(self):
        source = self.source("app/templates/receipts.html")
        modal = source.split('id="receiptModal"', 1)[1].split(
            'id="receiptEditModal"', 1
        )[0]
        self.assertIn("data-erp-modal-shell", modal)
        document = modal.split('id="document_number"', 1)[1].split(
            "</div>", 1
        )[0]
        self.assertNotIn("required", document)
        self.assertIn("Импортировать товары из Excel", modal)
        self.assertIn("Импортировать", modal)
        self.assertIn('name="product_image"', modal)
        self.assertIn("JPEG или PNG, не больше 3 МБ", modal)
        self.assertIn('name="submit_mode"', modal)
        self.assertIn('value="create_next"', modal)
        self.assertIn("Провести и добавить ещё", modal)
        self.assertIn('value="close"', modal)
        self.assertIn("Провести приход", modal)
        for section in ("Документ", "Товар", "Фото", "Комментарий"):
            self.assertIn(f">{section}</h3>", modal)

    def test_shared_modal_script_is_loaded_by_all_three_pages(self):
        for template in ("warehouse.html", "sales.html", "receipts.html"):
            with self.subTest(template=template):
                source = self.source("app/templates/" + template)
                self.assertIn("js/erp-modal-shell.js", source)


if __name__ == "__main__":
    unittest.main()
