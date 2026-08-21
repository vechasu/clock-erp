import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SalesFormDesignSystemContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "app/templates/sales.html").read_text(
            encoding="utf-8"
        )
        cls.styles = (ROOT / "app/static/css/erp-components.css").read_text(
            encoding="utf-8"
        )
        cls.catalog_script = (
            ROOT / "app/static/js/catalog-combobox.js"
        ).read_text(encoding="utf-8")
        cls.modal = cls.template.split('id="manualSaleModal"', 1)[1].split(
            'id="saleReturnModal"', 1
        )[0]

    def test_form_uses_shared_modal_and_four_sections(self):
        for marker in (
            "data-erp-modal-shell",
            "data-erp-modal-lock",
            "erp-modal-dialog",
            "erp-modal-header",
            "erp-modal-body",
            "erp-modal-actions",
        ):
            self.assertIn(marker, self.modal)
        self.assertEqual(self.modal.count('id="manualSaleForm"'), 1)
        for section in ("Товар", "Продажа", "Данные канала"):
            self.assertEqual(self.modal.count(f">{section}</h3>"), 1)
        self.assertEqual(
            self.modal.count('<span class="erp-form-subsection-title">Дополнительно</span>'),
            3,
        )

    def test_channels_are_explicit_accessible_and_keep_contextual_action(self):
        self.assertIn('role="radiogroup"', self.modal)
        self.assertEqual(self.modal.count('role="radio"'), 3)
        for source, label in (
            ("tictactoy", "Tictactoy"),
            ("wildberries", "Wildberries"),
            ("amazon", "Amazon"),
        ):
            self.assertIn(f'data-sale-source="{source}"', self.modal)
            self.assertIn(f'data-source-fields="{source}"', self.modal)
            self.assertIn(f'{source}: "{label}"', self.template)
        self.assertIn('aria-checked="false"', self.modal)
        self.assertIn("clearSaleFormSource()", self.template)
        self.assertIn('button.setAttribute("aria-checked", String(active))', self.template)
        self.assertIn('"Добавить продажу в " + sourceLabels[sourceKey]', self.template)
        self.assertIn("sourceChoicePanel.addEventListener(\"keydown\"", self.template)
        for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
            self.assertIn(f'"{key}"', self.template)

    def test_catalog_is_server_backed_stock_filtered_and_has_product_summary(self):
        self.assertIn('data-shared-catalog-scope', self.modal)
        self.assertIn('data-catalog-in-stock="true"', self.modal)
        self.assertIn('shared_catalog_kind="product"', self.modal)
        self.assertNotIn("sales_product_options", self.modal)
        for marker in (
            "AbortController",
            "window.setTimeout(function()",
            'classList.toggle("is-loading", loading)',
            'emptyMessage.textContent = "Загрузка…"',
            '"Ничего не найдено"',
            '"Не удалось загрузить значения"',
            "Number(item?.stock) > 0",
        ):
            self.assertIn(marker, self.catalog_script)
        for marker in (
            'id="saleProductPhoto"',
            'id="saleProductPhotoImages"',
            'id="saleProductPhotoText"',
            'id="productMeta"',
            '"Бренд: "',
            '"Категория: "',
            '"Артикул: "',
            '"Доступно: "',
        ):
            self.assertIn(marker, self.template)

    def test_payload_field_names_and_write_contracts_are_preserved(self):
        names = set(re.findall(r'name="([^"]+)"', self.modal))
        direct_names = {
            "csrf_token",
            "idempotency_key",
            "source",
            "product_name",
            "created_at",
            "quantity",
            "original_unit_price",
            "discount_type",
            "discount_value",
            "discount_reason",
            "unit_price",
            "order_number",
            "track_number",
            "delivery_cost",
            "sticker_number",
            "recipient_name",
            "invoice_number",
            "note",
        }
        self.assertTrue(direct_names.issubset(names), direct_names - names)
        for combobox_name in (
            "product_id",
            "brand_id",
            "category_id",
            "order_status",
            "commission",
            "country",
            "region",
            "city",
            "platform",
        ):
            self.assertIn(f'"{combobox_name}"', self.modal)
        self.assertIn("new FormData(manualSaleForm)", self.template)
        self.assertIn('"Idempotency-Key": saleEditIdempotencyKey', self.template)
        self.assertIn("if (saleEditSavePending)", self.template)
        self.assertIn("setSaleFormPending(true", self.template)
        self.assertIn('saleSubmitButton.textContent = "Сохраняем…"', self.template)

    def test_validation_error_and_unrelated_actions_remain_distinct(self):
        self.assertIn('id="saleProductError"', self.modal)
        self.assertIn('id="saleQuantityError"', self.modal)
        self.assertIn("saleQuantityInput.setCustomValidity", self.template)
        self.assertIn("setSaleFormPending(false)", self.template)
        self.assertIn('action="{{ url_for(\'sale_return\') }}"', self.template)
        self.assertIn('action="{{ url_for(\'sale_delete\') }}"', self.template)

    def test_css_keeps_internal_scroll_sticky_actions_and_mobile_fit(self):
        contract = self.styles.split(
            "Sales mutation form: shared ERP modal and form contract.", 1
        )[1]
        for marker in (
            "#manualSaleModal .sales-form-dialog",
            "max-height: calc(100dvh - 32px)",
            "overflow-y: auto",
            "overflow-x: hidden",
            "#manualSaleModal .sales-form-actions",
            "position: sticky",
            "height: 100dvh",
            "grid-template-columns: minmax(0, 1fr)",
            "env(safe-area-inset-bottom)",
            "overflow-wrap: anywhere",
            "var(--erp-focus-ring)",
        ):
            self.assertIn(marker, contract)


if __name__ == "__main__":
    unittest.main()
