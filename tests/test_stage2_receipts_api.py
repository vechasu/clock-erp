import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web


class Stage2ReceiptsApiTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.receipts_path = self.root / "receipts.json"
        self.operations_path = self.root / "stock_operations.json"
        self.catalog = [
            {
                "id": "ms-1",
                "name": "Casio G-Shock",
                "article": "GA-2100",
                "code": "CASIO-1",
                "brand": "Casio",
                "category": "Часы",
                "cell": "A-1",
                "stock": 3,
                "stock_display": "3",
                "thumbnail_url": "",
            },
            {
                "id": "ms-2",
                "name": "Ремешок",
                "article": "STRAP",
                "code": "STRAP-1",
                "brand": "Vechasu",
                "category": "Ремешки",
                "cell": "B-1",
                "stock": 0,
                "stock_display": "0",
                "thumbnail_url": "",
            },
        ]
        self.patchers = [
            mock.patch.object(web, "get_receipts_path", return_value=self.receipts_path),
            mock.patch.object(
                web,
                "get_stock_operations_path",
                return_value=self.operations_path,
            ),
            mock.patch.object(
                web,
                "get_warehouse_items",
                side_effect=lambda **_kwargs: self.catalog,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()
        self.moysklad = mock.patch.object(web, "MoySkladClient")
        self.client_class = self.moysklad.start()
        self.remote = self.client_class.return_value
        self.remote.create_stock_enter_many.return_value = {
            "id": "enter-1",
            "name": "ОП-0001",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }
        self.remote.update_stock_enter_many.return_value = {
            "id": "enter-1",
            "name": "ОП-0001",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }
        self.remote.delete_stock_enter.return_value = True

    def tearDown(self):
        self.moysklad.stop()
        for patcher in reversed(self.patchers):
            patcher.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp.cleanup()

    def create_receipt(self, positions=None):
        return self.client.post(
            "/api/receipts",
            json={
                "receipt_date": "2026-07-30",
                "note": "Поставка",
                "positions": positions
                or [{
                    "product_id": "ms-1",
                    "brand": "Casio",
                    "category": "Часы",
                    "quantity": 2,
                    "purchase_price": 5000,
                }],
            },
        )

    def test_create_list_search_sort_and_catalog(self):
        created = self.create_receipt()
        self.assertEqual(created.status_code, 201)
        receipt = created.get_json()["data"]
        self.assertEqual(receipt["total_quantity"], 2)
        self.assertEqual(receipt["total_amount"], 10000)
        self.assertEqual(receipt["moysklad_document_id"], "enter-1")
        self.remote.create_stock_enter_many.assert_called_once()

        listing = self.client.get(
            "/api/v1/receipts?q=casio&date_from=2026-07-01"
            "&sort_by=total_amount&sort_dir=desc&page_size=1"
        )
        self.assertEqual(listing.status_code, 200)
        payload = listing.get_json()
        self.assertEqual(payload["meta"]["total"], 1)
        self.assertEqual(payload["meta"]["totals"]["quantity"], 2)
        self.assertEqual(payload["data"][0]["number"], receipt["number"])

        catalog = self.client.get("/api/receipts/catalog?q=strap").get_json()
        self.assertEqual(catalog["meta"]["total"], 1)
        self.assertEqual(catalog["data"][0]["id"], "ms-2")

    def test_patch_and_delete_preserve_remote_first_workflow(self):
        receipt_id = self.create_receipt().get_json()["data"]["id"]
        updated = self.client.patch(
            "/api/receipts/{}".format(receipt_id),
            json={
                "receipt_date": "2026-07-31",
                "note": "Уточнено",
                "product_id": "ms-2",
                "brand": "Vechasu",
                "category": "Ремешки",
                "quantity": 4,
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["data"]["product_name"], "Ремешок")
        self.assertEqual(updated.get_json()["data"]["total_quantity"], 4)
        self.remote.update_stock_enter_many.assert_called_once()

        deleted = self.client.delete("/api/receipts/{}".format(receipt_id))
        self.assertEqual(deleted.status_code, 200)
        self.remote.delete_stock_enter.assert_called_once_with("enter-1")
        self.assertEqual(
            self.client.get("/api/receipts").get_json()["meta"]["total"],
            0,
        )

    def test_validation_conflict_and_remote_failure_are_structured(self):
        invalid = self.client.post(
            "/api/receipts",
            json={"receipt_date": "bad", "positions": []},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(
            invalid.get_json()["code"],
            "RECEIPT_VALIDATION_FAILED",
        )

        self.remote.create_stock_enter_many.side_effect = RuntimeError(
            "remote unavailable"
        )
        failed = self.create_receipt()
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.get_json()["code"], "REMOTE_DOCUMENT_CONFLICT")
        self.assertEqual(web.load_receipts(), [])

    def test_multi_position_receipt_is_not_editable(self):
        created = self.create_receipt([
            {
                "product_id": "ms-1",
                "quantity": 1,
                "purchase_price": 100,
            },
            {
                "product_id": "ms-2",
                "quantity": 2,
                "purchase_price": 50,
            },
        ])
        receipt_id = created.get_json()["data"]["id"]
        response = self.client.patch(
            "/api/receipts/{}".format(receipt_id),
            json={"quantity": 5},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "RECEIPT_NOT_EDITABLE")


if __name__ == "__main__":
    unittest.main()
