import unittest
from pathlib import Path
from unittest import mock

from app import web


class WarehouseProductPhotosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = Path("app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )

    def test_gallery_keeps_real_multiple_images_without_duplicates(self):
        product = {
            "gallery": [
                {"original_url": "https://images.test/one.jpg"},
                {"url": "https://images.test/two.jpg"},
                {"thumbnail_url": "https://images.test/one.jpg"},
            ],
            "bitrix_primary_image_url": "https://images.test/primary.jpg",
        }
        self.assertEqual(
            web.warehouse_product_gallery(product),
            [
                "https://images.test/one.jpg",
                "https://images.test/two.jpg",
            ],
        )

    def test_gallery_restores_bitrix_primary_image_when_gallery_is_empty(self):
        self.assertEqual(
            web.warehouse_product_gallery({
                "gallery": [],
                "bitrix_primary_image_url": "https://images.test/main.jpg",
                "bitrix_thumbnail_url": "https://images.test/thumb.jpg",
            }),
            ["https://images.test/main.jpg"],
        )

    def test_gallery_uses_existing_moysklad_thumbnail_as_last_fallback(self):
        product_id = "11111111-2222-4333-8444-555555555555"
        self.assertEqual(
            web.warehouse_product_gallery({
                "gallery": [],
                "moysklad_product_id": product_id,
            }),
            ["/warehouse/product/{}/thumbnail".format(product_id)],
        )
        self.assertEqual(web.warehouse_product_gallery({"gallery": []}), [])

    def test_detail_endpoint_returns_normalized_existing_source(self):
        product = {
            "id": 42,
            "gallery": [],
            "bitrix_primary_image_url": "https://images.test/main.jpg",
        }
        catalog = mock.Mock()
        catalog.get_product.return_value = product
        with web.app.test_request_context("/warehouse/product/42"):
            with mock.patch.object(web, "ExcelProductCatalog", return_value=catalog):
                response = web.warehouse_product_detail(42)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["gallery"], [
            {"original_url": "https://images.test/main.jpg"}
        ])
        catalog.get_product.assert_called_once_with(42)

    def test_position_card_layout_photo_contract(self):
        self.assertIn('grid-template-areas: "info media"', self.template)
        self.assertIn('"media"\n                    "info"', self.template)
        self.assertIn('id="detailGallerySection" class="product-card-media"', self.template)
        self.assertNotIn(
            'id="detailGallerySection" class="product-card-media" hidden',
            self.template,
        )
        self.assertIn("product-gallery-empty", self.template)
        self.assertIn("product-gallery-counter", self.template)
        self.assertIn("object-fit: contain", self.template)
        self.assertIn('data-thumbnail-url="{{ item.thumbnail_url|e }}"', self.template)

    def test_position_card_preserves_existing_photo_edit_workflow(self):
        self.assertIn("warehouseProductImageAction", self.template)
        self.assertIn("restoreWarehouseProductPhoto()", self.template)
        self.assertIn("const updateBody = new FormData();", self.template)


if __name__ == "__main__":
    unittest.main()
