import unittest
from pathlib import Path


class ProductPhotoUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        templates = Path("app/templates")
        cls.warehouse = (templates / "warehouse.html").read_text(
            encoding="utf-8"
        )
        cls.receipts = (templates / "receipts.html").read_text(
            encoding="utf-8"
        )
        cls.sales = (templates / "sales.html").read_text(encoding="utf-8")

    def test_products_card_stages_replace_remove_and_cancel(self):
        self.assertIn('id="warehouseProductImagePreview"', self.warehouse)
        self.assertIn('id="warehouseProductImagePlaceholder"', self.warehouse)
        self.assertIn('id="warehouseProductImageChoose"', self.warehouse)
        self.assertIn('id="warehouseProductImageCancel"', self.warehouse)
        self.assertIn('id="warehouseProductImageRemove"', self.warehouse)
        self.assertIn('name="product_image_action"', self.warehouse)
        self.assertIn(').value = "remove"', self.warehouse)
        self.assertIn("restoreWarehouseProductPhoto()", self.warehouse)
        self.assertIn("const updateBody = new FormData();", self.warehouse)
        self.assertNotIn("image.disabled = editing", self.warehouse)

    def test_products_photo_preview_is_compact_and_non_stretching(self):
        self.assertIn("width: 72px;", self.warehouse)
        self.assertIn("height: 72px;", self.warehouse)
        self.assertIn("object-fit: cover;", self.warehouse)
        self.assertIn("min-width: 0;", self.warehouse)

    def test_receipt_existing_product_photo_is_read_only(self):
        editor = self.receipts.split('id="receiptEditModal"', 1)[1]
        editor = editor.split("<!-- RECEIPTS EXCEL IMPORT MODAL V1 -->", 1)[0]
        self.assertIn('id="receiptEditCurrentImage"', editor)
        self.assertNotIn('type="file"', editor)
        self.assertNotIn('name="product_image"', editor)

    def test_sales_selected_product_photo_is_read_only(self):
        self.assertIn('id="saleProductPhoto"', self.sales)
        self.assertIn('id="saleProductPhotoImages"', self.sales)
        self.assertIn("normalizeCatalogProductImageUrls(product)", self.sales)
        self.assertIn("sale-product-photo-placeholder[hidden]", self.sales)
        self.assertIn("renderSaleProductPhoto(item)", self.sales)
        form = self.sales.split('id="manualSaleForm"', 1)[1]
        form = form.split("</form>", 1)[0]
        self.assertNotIn('name="product_image"', form)
        self.assertNotIn('type="file"', form)

    def test_shared_product_picker_normalizes_photo_sources(self):
        component = Path("app/static/js/catalog-combobox.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("normalizeCatalogProductImageUrls", component)
        self.assertIn("seenUrls.has(normalizedUrl)", component)
        self.assertIn("seenIds.has(imageId)", component)
        self.assertIn("product.DETAIL_PICTURE", component)
        self.assertIn("product.PREVIEW_PICTURE", component)
        self.assertIn("product.gallery", component)


if __name__ == "__main__":
    unittest.main()
