import unittest
from pathlib import Path


class ReceiptsUiRegressionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = Path("app/templates/receipts.html").read_text(
            encoding="utf-8"
        )
        cls.catalog_script = Path(
            "app/static/js/catalog-combobox.js"
        ).read_text(encoding="utf-8")
        cls.submit_script = Path(
            "app/static/js/receipt-submit.js"
        ).read_text(encoding="utf-8")
        cls.components = Path(
            "app/static/css/erp-components.css"
        ).read_text(encoding="utf-8")

    def test_receipt_form_restores_photo_preview_and_optional_document(self):
        self.assertIn('id="receipt_product_image"', self.template)
        self.assertIn('id="receiptImagePreview"', self.template)
        document = self.template.split('id="document_number"', 1)[1]
        self.assertNotIn("required", document.split(">", 1)[0])

    def test_receipt_table_persists_resizable_columns(self):
        self.assertIn('id="receiptsTable"', self.template)
        self.assertIn("receipt-column-resize-handle", self.template)
        self.assertIn("vechasu-receipts-table-view-v1", self.template)
        self.assertNotIn("width: 100% !important", self.template)
        self.assertIn('let total = 112;', self.template)

    def test_receipt_editor_shows_existing_photo_without_edit_controls(self):
        self.assertIn('id="receiptEditModal"', self.template)
        self.assertIn("receipt-card-layout", self.template)
        self.assertIn('id="receiptEditCurrentImage"', self.template)
        self.assertIn("data-receipt-edit-photo-placeholder", self.template)
        self.assertNotIn("data-receipt-edit-image", self.template)
        self.assertNotIn("receipt-edit-photo-field", self.template)

    def test_only_a_new_inline_product_enables_receipt_photo_upload(self):
        self.assertIn('id="receiptImageField"', self.template)
        self.assertIn('id="receiptSelectedPhoto"', self.template)
        self.assertIn("setReceiptNewProductPhotoMode(false)", self.template)
        self.assertIn("setReceiptNewProductPhotoMode(true)", self.template)
        self.assertIn('"shared-catalog:created"', self.template)
        self.assertIn('"shared-catalog:created"', self.catalog_script)

    def test_receipt_editor_uses_full_width_and_closes_only_by_cross(self):
        self.assertIn(
            ".receipt-card-layout {\n"
            "            display: grid;\n"
            "            width: 100%;\n"
            "            min-width: 0;\n"
            "            grid-template-columns: minmax(0, 1fr);",
            self.template,
        )
        self.assertIn("overflow-wrap: anywhere", self.template)
        modal = self.template.split('id="receiptEditModal"', 1)[1]
        modal = modal.split("<!-- RECEIPTS EXCEL IMPORT MODAL V1 -->", 1)[0]
        self.assertEqual(modal.count("data-receipt-edit-close"), 1)
        editor_script = self.template.split("// RECEIPT CARD EDITOR", 1)[1]
        editor_script = editor_script.split(
            "// Excel-like receipt widths", 1
        )[0]
        self.assertNotIn("event.target === receiptEditModal", editor_script)
        self.assertNotIn('event.key === "Escape"', editor_script)

    def test_date_has_a_secondary_time_and_search_has_no_long_hint(self):
        self.assertIn(
            'from "_datetime_cell.html" import render_erp_datetime',
            self.template,
        )
        self.assertIn(
            "render_erp_datetime(receipt_date_value, receipt_created_value)",
            self.template,
        )
        self.assertIn('placeholder="Поиск"', self.template)
        self.assertNotIn(
            'placeholder="Поиск по документу, бренду, категории или товару"',
            self.template,
        )

    def test_new_brand_category_mode_reuses_shared_catalog_cascade(self):
        self.assertIn(
            'data-new-brand-global-categories="true"',
            self.template,
        )
        self.assertIn("globalCategoryOptions", self.catalog_script)
        self.assertIn(
            'data-global-category-options="true"',
            self.template,
        )
        self.assertIn('"category_scope"', self.catalog_script)

    def test_create_and_edit_forms_use_shared_modal_sections(self):
        create_modal = self.template.split('id="receiptModal"', 1)[1].split(
            'id="receiptEditModal"', 1
        )[0]
        edit_modal = self.template.split('id="receiptEditModal"', 1)[1].split(
            "<!-- RECEIPTS EXCEL IMPORT MODAL V1 -->", 1
        )[0]
        for modal in (create_modal, edit_modal):
            self.assertIn("data-erp-modal-shell", modal)
            self.assertIn("data-erp-modal-lock", modal)
            self.assertIn("erp-modal-actions", modal)
        for section in ("Документ", "Товар", "Фото", "Комментарий"):
            self.assertIn(f">{section}</h3>", create_modal)
        for section in ("Документ", "Товар", "Комментарий"):
            self.assertIn(f">{section}</h3>", edit_modal)

    def test_time_is_read_only_and_preserves_server_timestamp_contract(self):
        create_time = self.template.split('id="receipt_time"', 1)[1].split(
            ">", 1
        )[0]
        edit_time = self.template.split('id="receiptEditTime"', 1)[1].split(
            ">", 1
        )[0]
        self.assertIn('type="time"', create_time)
        self.assertIn('value="{{ today_time }}"', create_time)
        self.assertIn("disabled", create_time)
        self.assertNotIn('name="', create_time)
        self.assertIn('type="time"', edit_time)
        self.assertIn("disabled", edit_time)
        self.assertIn("createdTime[1]", self.template)

    def test_product_confirmation_and_photo_validation_are_visible(self):
        for marker in (
            'id="receiptSelectedProductName"',
            'id="receiptSelectedProductMeta"',
            "data-receipt-product-brand",
            "data-receipt-product-category",
            "data-receipt-product-article",
            "data-receipt-product-stock",
            "data-receipt-product-id",
        ):
            self.assertIn(marker, self.template)
        self.assertIn('new Set([\n            "image/jpeg",\n            "image/png",', self.template)
        self.assertIn("3 * 1024 * 1024", self.template)
        self.assertIn("setCustomValidity(error)", self.template)

    def test_pending_double_submit_and_submit_modes_keep_payload_contract(self):
        self.assertIn(
            'receiptForm.dataset.submitting === "true"',
            self.template,
        )
        self.assertIn(
            "receiptEditModalForm.dataset.submitting",
            self.template,
        )
        self.assertIn(
            'form.querySelectorAll("button, input, select, textarea")',
            self.template,
        )
        self.assertIn('form.setAttribute("aria-busy"', self.template)
        self.assertIn("Приход сохраняется…", self.template)
        self.assertIn('headers = {\n            "X-CSRF-Token": csrfToken,\n            "Idempotency-Key": idempotencyKey,', self.submit_script)
        self.assertIn('payload.set(\n            "submit_mode"', self.submit_script)
        self.assertIn('submitMode === "create_next" ? "create_next" : "close"', self.submit_script)

    def test_receipt_modals_have_desktop_and_mobile_overflow_contracts(self):
        self.assertIn("#receiptEditModal .receipt-edit-dialog", self.components)
        self.assertIn("#receiptEditModal .receipt-edit-product-summary", self.components)
        mobile = self.components.split(
            "#receiptModal #receiptImageField[hidden]", 1
        )[1].split("/* ERP Design System V1", 1)[0]
        self.assertIn("#receiptModal .receipt-simple-grid > .field", mobile)
        self.assertIn("#receiptEditModal .receipt-card-grid", mobile)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", mobile)
        self.assertIn("receipt_card_layout_e2e", self.template)
        self.assertIn("create-modal-layout", self.template)


if __name__ == "__main__":
    unittest.main()
