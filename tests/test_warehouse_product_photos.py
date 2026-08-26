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

    def test_moysklad_detail_rereads_actual_image_and_versions_thumbnail(self):
        product = {
            "id": 42,
            "gallery": [],
            "moysklad_product_id": "11111111-2222-4333-8444-555555555555",
        }
        catalog = mock.Mock()
        catalog.get_product.return_value = product
        remote = mock.Mock()
        remote.get_product_images.return_value = [{"id": "image-new"}]
        with web.app.test_request_context("/warehouse/product/42"), \
                mock.patch.object(web, "ExcelProductCatalog", return_value=catalog), \
                mock.patch.object(web, "MoySkladClient", return_value=remote):
            response = web.warehouse_product_detail(42)

        self.assertEqual(response.get_json()["gallery"], [{
            "external_file_id": "",
            "kind": "moysklad",
            "is_primary": True,
            "order": 0,
            "original_url": (
                "/warehouse/product/11111111-2222-4333-8444-555555555555/"
                "thumbnail?v=image-new"
            ),
        }])
        remote.get_product_images.assert_called_once_with(
            product["moysklad_product_id"], limit=100
        )

    def test_moysklad_detail_returns_empty_gallery_after_last_photo_removed(self):
        product = {
            "id": 42,
            "gallery": [],
            "moysklad_product_id": "11111111-2222-4333-8444-555555555555",
        }
        catalog = mock.Mock()
        catalog.get_product.return_value = product
        remote = mock.Mock()
        remote.get_product_images.return_value = []
        with web.app.test_request_context("/warehouse/product/42"), \
                mock.patch.object(web, "ExcelProductCatalog", return_value=catalog), \
                mock.patch.object(web, "MoySkladClient", return_value=remote):
            response = web.warehouse_product_detail(42)

        self.assertEqual(response.get_json()["gallery"], [])

    def test_product_projection_exposes_saved_bitrix_file_identity(self):
        product = {
            "id": 42,
            "created_at": "2026-08-26T12:00:00",
            "stock": 3,
            "bitrix_external_product_id": "204699",
            "gallery": [{
                "id": "44610",
                "url": "https://www.tictactoy.ru/upload/watch.jpg",
                "kind": "detail",
            }],
        }

        item = web.build_excel_warehouse_items([product])[0]

        self.assertEqual(item["bitrix_element_id"], "204699")
        self.assertEqual(item["gallery"][0]["external_file_id"], "44610")
        self.assertEqual(
            item["thumbnail_url"],
            "/warehouse/product/42/image/44610",
        )

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
        self.assertIn("syncDetailRowThumbnail(firstUrl)", self.template)
        self.assertIn('image.removeAttribute("src")', self.template)

    def test_position_card_preserves_existing_photo_edit_workflow(self):
        self.assertIn("warehouseProductImageAction", self.template)
        self.assertIn("restoreWarehouseProductPhoto()", self.template)
        self.assertIn("const updateBody = new FormData();", self.template)
        self.assertIn('"bitrix_image_file_id",', self.template)
        self.assertIn("warehouseExistingProductPhotoFileId", self.template)
        self.assertIn(
            'window.confirm("Удалить фотографию товара после сохранения?")',
            self.template,
        )


if __name__ == "__main__":
    unittest.main()
