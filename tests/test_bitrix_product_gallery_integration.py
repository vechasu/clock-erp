import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.clients.bitrix_catalog import (
    BitrixCatalogClient,
    BitrixCatalogWriteError,
    normalize_product,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeImageResponse:
    content = b"image-bytes"
    headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, get_payload, post_payload=None, post_status=200):
        self.get_payload = get_payload
        self.post_payload = post_payload or {"ok": True, "affected_file_id": "77"}
        self.post_status = post_status
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        payload = self.get_payload[
            min(len(self.get_calls) - 1, len(self.get_payload) - 1)
        ] if isinstance(self.get_payload, list) else self.get_payload
        return FakeResponse(payload)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse(self.post_payload, self.post_status)


class BitrixProductGalleryIntegrationTest(unittest.TestCase):
    def setUp(self):
        web.BITRIX_GALLERY_CACHE.clear()

    def test_real_bitrix_fields_keep_primary_order_and_remove_duplicates(self):
        product = normalize_product({
            "id": "204699",
            "images": [
                {"id": "44610", "url": "/main.jpg", "type": "preview", "sort": 0},
                {"id": "44610", "url": "/main-copy.jpg", "type": "detail", "sort": 1},
                {"id": "44611", "url": "/one.jpg", "type": "gallery", "sort": 2},
                {"id": "44612", "url": "/two.jpg?cache=1", "type": "gallery", "sort": 3},
                {"id": "999", "url": "/two.jpg#duplicate", "type": "gallery", "sort": 4},
            ],
        }, "https://www.tictactoy.ru/")
        self.assertEqual(
            [image["id"] for image in product["images"]],
            ["44610", "44611", "44612"],
        )
        self.assertEqual(
            [image["kind"] for image in product["images"]],
            ["preview", "gallery", "gallery"],
        )

    def test_single_product_read_uses_saved_bitrix_id(self):
        session = FakeSession({"products": [{"id": "204699", "name": "Lady"}]})
        client = BitrixCatalogClient(
            "https://www.tictactoy.ru/api/catalog-export.php",
            token="secret-token",
            session=session,
        )
        product = client.get_product("204699")
        self.assertEqual(product["external_product_id"], "204699")
        params = session.get_calls[0][1]["params"]
        self.assertEqual(params["product_id"], "204699")
        self.assertNotIn("secret-token", repr(params))

    def test_write_keeps_token_in_header_and_rereads_gallery(self):
        session = FakeSession([
            {"products": [{
                "id": "204699",
                "images": [{"id": "44610", "url": "/old.jpg"}],
            }]},
            {"products": [{
                "id": "204699",
                "images": [{"id": "77", "url": "/new.jpg"}],
            }]},
        ])
        client = BitrixCatalogClient(
            "https://www.tictactoy.ru/api/catalog-export.php",
            token="secret-token",
            session=session,
        )
        product, result = client.mutate_product_image(
            "204699",
            "replace",
            image={
                "filename": "watch.jpg",
                "content": b"image",
                "mime_type": "image/jpeg",
            },
            file_id="44610",
        )
        post = session.post_calls[0][1]
        self.assertEqual(post["data"]["file_id"], "44610")
        self.assertEqual(post["headers"]["Authorization"], "Bearer secret-token")
        self.assertNotIn("secret-token", repr(post["data"]))
        self.assertEqual(product["images"][0]["id"], "77")
        self.assertEqual(result["affected_file_id"], "77")
        self.assertEqual(len(session.get_calls), 2)

    def test_add_replace_and_remove_use_one_bitrix_product_endpoint(self):
        for action in ("add", "replace", "remove"):
            with self.subTest(action=action):
                before_images = (
                    [] if action == "add"
                    else [{"id": "44610", "url": "/old.jpg"}]
                )
                after_images = (
                    [] if action == "remove"
                    else [{"id": "77", "url": "/new.jpg"}]
                )
                session = FakeSession([
                    {"products": [{"id": "204699", "images": before_images}]},
                    {"products": [{"id": "204699", "images": after_images}]},
                ])
                client = BitrixCatalogClient(
                    "https://www.tictactoy.ru/api/catalog-export.php",
                    token="secret-token",
                    session=session,
                )
                image = None if action == "remove" else {
                    "filename": "watch.webp",
                    "content": b"RIFFphotoWEBP",
                    "mime_type": "image/webp",
                }
                client.mutate_product_image(
                    "204699", action, image=image, file_id="44610"
                )
                self.assertEqual(session.post_calls[0][1]["data"], {
                    "product_id": "204699",
                    "action": action,
                    "file_id": "44610",
                })

    def test_replace_preserves_four_photo_order_for_every_position(self):
        before_ids = ["10", "11", "12", "13"]
        for position in (0, 1, 3):
            with self.subTest(position=position):
                after_ids = list(before_ids)
                after_ids[position] = "77"
                session = FakeSession([
                    {"products": [{
                        "id": "204699",
                        "images": [
                            {"id": image_id, "url": "/{}.jpg".format(image_id)}
                            for image_id in before_ids
                        ],
                    }]},
                    {"products": [{
                        "id": "204699",
                        "images": [
                            {"id": image_id, "url": "/{}.jpg".format(image_id)}
                            for image_id in after_ids
                        ],
                    }]},
                ])
                client = BitrixCatalogClient(
                    "https://www.tictactoy.ru/api/catalog-export.php",
                    token="secret-token",
                    session=session,
                )
                product, result = client.mutate_product_image(
                    "204699",
                    "replace",
                    image={
                        "filename": "new.jpg",
                        "content": b"new",
                        "mime_type": "image/jpeg",
                    },
                    file_id=before_ids[position],
                )
                self.assertEqual(
                    [image["id"] for image in product["images"]], after_ids
                )
                self.assertEqual(result["affected_file_id"], "77")

    def test_replace_rejects_http_success_when_gallery_did_not_change(self):
        unchanged = {"products": [{
            "id": "204699",
            "images": [
                {"id": "10", "url": "/10.jpg"},
                {"id": "11", "url": "/11.jpg"},
            ],
        }]}
        client = BitrixCatalogClient(
            "https://www.tictactoy.ru/api/catalog-export.php",
            session=FakeSession([unchanged, unchanged]),
        )
        with self.assertRaises(BitrixCatalogWriteError) as raised:
            client.mutate_product_image(
                "204699",
                "replace",
                image={"filename": "new.jpg", "content": b"new"},
                file_id="11",
            )
        self.assertEqual(
            raised.exception.context["reason"],
            "replacement_gallery_state_mismatch",
        )

    def test_bitrix_error_does_not_report_replace_success(self):
        before = {"products": [{
            "id": "204699",
            "images": [{"id": "10", "url": "/10.jpg"}],
        }]}
        client = BitrixCatalogClient(
            "https://www.tictactoy.ru/api/catalog-export.php",
            session=FakeSession(
                before,
                post_payload={"error": "image_save_failed"},
                post_status=500,
            ),
        )
        with self.assertRaises(BitrixCatalogWriteError) as raised:
            client.mutate_product_image(
                "204699",
                "replace",
                image={"filename": "new.jpg", "content": b"new"},
                file_id="10",
            )
        self.assertEqual(raised.exception.context["http_status"], 500)
        self.assertEqual(
            raised.exception.context["response"],
            {"error": "image_save_failed"},
        )

    def test_lazy_endpoint_returns_all_live_bitrix_images_through_proxy(self):
        stored = {
            "id": 9037,
            "bitrix_external_product_id": "204699",
            "gallery": [],
            "moysklad_product_id": None,
        }
        live_gallery = [
            {"id": "44610", "url": "https://www.tictactoy.ru/upload/main.jpg"},
            {"id": "44611", "url": "https://www.tictactoy.ru/upload/one.jpg"},
            {"id": "44612", "url": "https://www.tictactoy.ru/upload/two.jpg"},
        ]
        live = {
            "images": live_gallery,
            "external_product_id": "204699",
        }
        catalog = mock.Mock()
        catalog.get_product.return_value = stored
        with web.app.test_request_context(
            "/warehouse/product/9037",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ), mock.patch.object(web, "ExcelProductCatalog", return_value=catalog), \
                mock.patch.object(web, "_live_bitrix_product", return_value=live) as fetch, \
                mock.patch.object(web, "persist_live_bitrix_gallery") as persist:
            response = web.warehouse_product_detail(9037)
        payload = response.get_json()
        self.assertEqual(len(payload["gallery"]), 3)
        self.assertEqual(payload["source"], "bitrix")
        self.assertTrue(payload["editable"])
        self.assertEqual(
            payload["gallery"][2]["original_url"],
            "/warehouse/product/9037/image/44612",
        )
        fetch.assert_called_once_with(stored)
        persist.assert_not_called()

    def test_proxy_accepts_live_file_without_persisting_on_get(self):
        product = {
            "id": 9037,
            "bitrix_external_product_id": "204699",
            "gallery": [],
        }
        live = {"images": [{
            "id": "44613",
            "url": "https://www.tictactoy.ru/upload/live.jpg",
        }]}
        catalog = mock.Mock()
        catalog.get_product.return_value = product
        with web.app.test_request_context(
            "/warehouse/product/9037/image/44613"
        ), mock.patch.dict(
            "os.environ",
            {"BITRIX_CATALOG_URL": "https://www.tictactoy.ru/api/catalog-export.php"},
        ), mock.patch.object(
            web, "ExcelProductCatalog", return_value=catalog
        ), mock.patch.object(
            web, "_live_bitrix_product", return_value=live
        ) as fetch, mock.patch.object(
            web, "persist_live_bitrix_gallery"
        ) as persist, mock.patch.object(
            web.requests, "get", return_value=FakeImageResponse()
        ):
            response = web.warehouse_bitrix_product_image(9037, "44613")
        self.assertEqual(response.status_code, 200)
        fetch.assert_called_once_with(product)
        persist.assert_not_called()

    def test_lazy_xhr_is_not_redirected_but_legacy_navigation_is(self):
        with web.app.test_request_context(
            "/warehouse/product/9037",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ):
            self.assertIsNone(web.redirect_retired_frontend())
        with web.app.test_request_context("/warehouse/product/9037"):
            response = web.redirect_retired_frontend()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/app/products"))

    def test_proxy_allows_only_saved_file_on_configured_upload_host(self):
        product = {
            "id": 9037,
            "bitrix_external_product_id": "204699",
            "gallery": [{
                "id": "44610",
                "url": "https://www.tictactoy.ru/upload/main.jpg",
            }],
        }
        catalog = mock.Mock()
        catalog.get_product.return_value = product
        with web.app.test_request_context(
            "/warehouse/product/9037/image/44610"
        ), mock.patch.dict(
            "os.environ",
            {"BITRIX_CATALOG_URL": "https://www.tictactoy.ru/api/catalog-export.php"},
        ), mock.patch.object(
            web, "ExcelProductCatalog", return_value=catalog
        ), mock.patch.object(
            web.requests, "get", return_value=FakeImageResponse()
        ) as request_image:
            response = web.warehouse_bitrix_product_image(9037, "44610")
        self.assertEqual(response.mimetype, "image/jpeg")
        request_image.assert_called_once_with(
            "https://www.tictactoy.ru/upload/main.jpg",
            timeout=(3.05, 15),
        )

    def test_proxy_rejects_unsaved_or_cross_host_file(self):
        product = {
            "id": 9037,
            "bitrix_external_product_id": "204699",
            "gallery": [{
                "id": "44610",
                "url": "https://attacker.example/upload/main.jpg",
            }],
        }
        catalog = mock.Mock()
        catalog.get_product.return_value = product
        for file_id in ("missing", "44611", "44610"):
            with self.subTest(file_id=file_id), web.app.test_request_context(
                "/warehouse/product/9037/image/{}".format(file_id)
            ), mock.patch.dict(
                "os.environ",
                {"BITRIX_CATALOG_URL": "https://www.tictactoy.ru/api/catalog-export.php"},
            ), mock.patch.object(
                web, "ExcelProductCatalog", return_value=catalog
            ), mock.patch.object(web, "_live_bitrix_product", return_value=None):
                with self.assertRaises(Exception) as raised:
                    web.warehouse_bitrix_product_image(9037, file_id)
                self.assertEqual(raised.exception.code, 404)

    def test_bitrix_write_failure_rereads_actual_state(self):
        product = {
            "id": 42,
            "bitrix_external_product_id": "204699",
            "excel_name_raw": "Lady",
            "gallery": [],
        }
        catalog = mock.Mock()
        catalog.get_product.return_value = product
        client = mock.Mock()
        client.mutate_product_image.side_effect = BitrixCatalogWriteError("failed")
        with web.app.test_request_context("/api/products/42", method="PATCH"), \
                mock.patch.object(web, "require_csrf_when_authenticated"), \
                mock.patch.object(web, "ExcelProductCatalog", return_value=catalog), \
                mock.patch.object(web, "api_product_update_request_payload", return_value=(
                    {}, {"filename": "x.jpg", "content": b"x", "mime_type": "image/jpeg"},
                    "replace", "44610",
                )), mock.patch.object(web, "BitrixCatalogClient", return_value=client), \
                mock.patch.object(web, "_live_bitrix_product", return_value={"images": []}), \
                mock.patch.object(web, "persist_live_bitrix_gallery", return_value=product):
            response, status = web.api_product_resource(42)
        self.assertEqual(status, 502)
        self.assertEqual(response.get_json()["code"], "PRODUCT_IMAGE_UPLOAD_FAILED")

    def test_card_edit_controls_are_staged_until_save(self):
        template = Path("app/templates/warehouse.html").read_text(encoding="utf-8")
        self.assertIn('id="detailPhotoAdd"', template)
        self.assertIn('id="detailPhotoReplace"', template)
        self.assertIn('id="detailPhotoRemove"', template)
        self.assertIn('form.dataset.photoAction = "remove"', template)
        self.assertIn('method: "PATCH"', template)
        self.assertIn("resetDetailPhotoChanges(true)", template)
        self.assertIn("warehousePhotoGalleryCache.delete(detailUrl)", template)
        self.assertIn('form.dataset.submitting === "true"', template)
        self.assertIn("submitButton.disabled = true", template)

    def test_endpoint_contract_uses_real_gallery_property_and_safe_file_rules(self):
        endpoint = Path("bitrix/catalog-export.php").read_text(encoding="utf-8")
        self.assertIn("const CATALOG_EXPORT_GALLERY_PROPERTY = 'GALLERY';", endpoint)
        self.assertIn("'PREVIEW_PICTURE'", endpoint)
        self.assertIn("'DETAIL_PICTURE'", endpoint)
        self.assertIn("image/jpeg", endpoint)
        self.assertIn("image/png", endpoint)
        self.assertIn("image/webp", endpoint)
        self.assertIn("CATALOG_EXPORT_MAX_IMAGE_BYTES", endpoint)
        self.assertIn("array_shift($galleryIds)", endpoint)
        self.assertIn("galleryFileEntries($productId)", endpoint)
        self.assertIn("'old_file' => (int) $entry['file_id']", endpoint)
        self.assertIn("mutateGalleryFiles(", endpoint)
        self.assertNotIn("$values[] = $fileId", endpoint)
        self.assertNotIn("MORE_PHOTO", endpoint)


if __name__ == "__main__":
    unittest.main()
