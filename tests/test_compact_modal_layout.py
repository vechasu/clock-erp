import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CompactModalLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.warehouse = (ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        cls.sales = (ROOT / "app/templates/sales.html").read_text(
            encoding="utf-8"
        )
        cls.receipts = (ROOT / "app/templates/receipts.html").read_text(
            encoding="utf-8"
        )
        cls.css = (ROOT / "app/static/css/erp-components.css").read_text(
            encoding="utf-8"
        )

    def test_product_form_keeps_controls_and_mutation_contract(self):
        form = self.warehouse.split('id="warehouseAddForm"', 1)[1].split(
            "</form>", 1
        )[0]
        self.assertIn('action="/warehouse/add"', form)
        for marker in (
            'name="name"',
            '"brand",',
            'id_input_name="brand_id"',
            '"category",',
            'id_input_name="category_id"',
            '"product_id",',
            'name="product_image"',
            'name="product_image_action"',
            'name="article"',
            'name="price"',
            'name="stock"',
            'name="cell"',
        ):
            self.assertIn(marker, form)
        self.assertIn("warehouse-add-grid-catalog", form)
        self.assertIn("warehouse-add-photo-section", form)

    def test_sales_form_keeps_channel_payload_fields_and_endpoint(self):
        form = self.sales.split('id="manualSaleForm"', 1)[1].split(
            "</form>", 1
        )[0]
        self.assertIn("url_for('manual_sale_add')", form)
        for name in (
            "source",
            "product_name",
            "product_brand",
            "brand_id",
            "product_category",
            "category_id",
            "product_id",
            "created_at",
            "quantity",
            "unit_price",
            "order_status",
            "commission",
            "order_number",
            "track_number",
            "delivery_cost",
            "country",
            "region",
            "city",
            "note",
            "sticker_number",
            "recipient_name",
            "platform",
            "invoice_number",
        ):
            self.assertRegex(form, rf'["=]{re.escape(name)}[",]')
        self.assertEqual(form.count('data-source-fields="'), 3)
        self.assertIn("sale-product-summary", form)

    def test_receipt_form_keeps_fields_defaults_and_actions(self):
        form = self.receipts.split('id="receiptForm"', 1)[1].split(
            "</form>", 1
        )[0]
        self.assertIn('action="/receipts/create"', self.receipts)
        for marker in (
            'name="receipt_date"',
            'value="{{ today }}"',
            'name="purchase_price"',
            'name="document_number"',
            '"brand",',
            'id_input_name="brand_id"',
            '"category",',
            'id_input_name="category_id"',
            '"product_id",',
            'name="catalog_product_id"',
            'name="quantity"',
            'value="1"',
            'name="product_image"',
            'name="note"',
            'name="submit_mode"',
            'value="create_next"',
            'value="close"',
        ):
            self.assertIn(marker, form)
        self.assertIn("receipt-quantity-field", form)
        self.assertIn("receipt-comment-field", form)

    def test_layout_uses_grid_without_scaling_or_global_font_shrinking(self):
        compact_css = self.css.split("/* Compact mutation modals:", 1)[1]
        self.assertIn("@media (min-width: 768px)", compact_css)
        self.assertIn("repeat(12, minmax(0, 1fr))", compact_css)
        self.assertIn("repeat(3, minmax(0, 1fr))", compact_css)
        self.assertIn("@media (max-width: 767px)", compact_css)
        self.assertNotIn("transform: scale", compact_css)
        self.assertNotRegex(compact_css, r"\bzoom\s*:")
        self.assertNotIn("font-size:", compact_css)


if __name__ == "__main__":
    unittest.main()
