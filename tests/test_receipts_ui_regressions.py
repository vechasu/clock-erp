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
        self.assertIn("receipt-date-time", self.template)
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
        self.assertIn("newBrandUsesGlobalCategories", self.catalog_script)
        self.assertIn('"category_scope"', self.catalog_script)


if __name__ == "__main__":
    unittest.main()
