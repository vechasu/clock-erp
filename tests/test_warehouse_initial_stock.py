import unittest
from io import BytesIO
from unittest import mock

from app import web


class ManualProductCreationDisabledTest(unittest.TestCase):
    def test_old_endpoints_reject_all_payloads_without_catalog_or_network_writes(self):
        with mock.patch.dict(web.app.config, TESTING=True, AUTH_TESTING=False), \
                mock.patch.object(web, "ExcelProductCatalog") as catalog, \
                mock.patch.object(web, "MoySkladClient") as remote:
            client = web.app.test_client()
            for path in ("/warehouse/add", "/api/products", "/api/v1/products", "/receipts/catalog/create"):
                for multipart in (False, True):
                    with self.subTest(path=path, multipart=multipart):
                        values = {"name": "Manual", "kind": "product", "stock": "7", "bitrix_id": "501"}
                        if multipart:
                            values["product_image"] = (BytesIO(b"photo"), "photo.png")
                            response = client.post(path, data=values, content_type="multipart/form-data")
                        else:
                            response = client.post(path, json=values)
                        self.assertEqual(response.status_code, 410)
                        if path.startswith("/api/") or path == "/warehouse/add":
                            self.assertEqual(
                                response.get_json()["code"],
                                "MANUAL_PRODUCT_CREATION_DISABLED",
                            )
            self.assertEqual(client.post("/receipts/create", data={"product_id": "__new__", "new_product_name": "Manual"}).status_code, 410)
            catalog.assert_not_called()
            remote.assert_not_called()
