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

    def test_retired_receipt_write_api_is_stock_neutral(self):
        response=self.create_receipt()
        self.assertEqual(response.status_code,410)
        self.remote.create_stock_enter_many.assert_not_called()
        self.assertEqual(self.client.get('/api/v1/receipts').get_json()['data'],[])


    def test_legacy_patch_and_delete_are_retired(self):
        for response in (self.client.patch('/api/v1/receipts/old',json={'quantity':10}),self.client.delete('/api/v1/receipts/old')):
            self.assertEqual(response.status_code,410)
        self.remote.update_stock_enter_many.assert_not_called()
        self.remote.delete_stock_enter.assert_not_called()




    def test_unchanged_receipts_are_serialized_once_for_repeated_pages(self):
        self.create_receipt()
        web._cached_api_receipt_records.cache_clear()
        with mock.patch.object(
            web,
            "load_receipts",
            wraps=web.load_receipts,
        ) as load_receipts:
            first = self.client.get("/api/v1/receipts?page=1&page_size=1")
            second = self.client.get("/api/v1/receipts?page=2&page_size=1")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        load_receipts.assert_called_once_with()

    def test_missing_product_thumbnail_is_a_cacheable_empty_response(self):
        self.remote.download_product_thumbnail.return_value = None

        response = self.client.get(
            "/warehouse/product/"
            "11111111-1111-1111-1111-111111111111/thumbnail"
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers["Cache-Control"],
            "private, max-age=300",
        )


if __name__ == "__main__":
    unittest.main()
