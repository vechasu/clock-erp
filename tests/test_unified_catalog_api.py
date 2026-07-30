import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)


class UnifiedCatalogApiTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "catalog.db"
        self.receipts_path = self.root / "receipts.json"
        self.operations_path = self.root / "stock_operations.json"
        self.manual_sales_path = self.root / "manual_sales.json"
        self.overrides_path = self.root / "automatic_sales_overrides.json"
        self.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(self.database_path)},
        )
        self.environment.start()
        self.patchers = [
            mock.patch.object(
                web,
                "get_receipts_path",
                return_value=self.receipts_path,
            ),
            mock.patch.object(
                web,
                "get_stock_operations_path",
                return_value=self.operations_path,
            ),
            mock.patch.object(
                web,
                "get_manual_sales_path",
                return_value=self.manual_sales_path,
            ),
            mock.patch.object(
                web,
                "get_automatic_sales_overrides_path",
                return_value=self.overrides_path,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        database = CatalogDatabase(self.database_path)
        ExcelProductBatchService(database).apply(
            [{
                "excel_row": 2,
                "excel_name": "Служебная карточка",
                "excel_brand": "Служебный бренд",
                "excel_article": "SEED",
                "article_quality": "code_like",
                "category": "Служебная категория",
                "stock": 0.0,
                "stock_valid": True,
                "cell": "A-1",
                "product_id": None,
                "match_status": "not_found",
                "match_method": "test",
                "confidence": 0,
                "alternatives": [],
            }],
            "c" * 64,
            "unified.xlsx",
        )
        self.product = ExcelProductCatalog(database).create_product(
            name="Casio A168",
            article="A168",
            brand="Casio",
            category="Наручные часы",
            stock=0,
        )
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()
        self.moysklad_patch = mock.patch.object(web, "MoySkladClient")
        self.moysklad_class = self.moysklad_patch.start()
        self.remote = self.moysklad_class.return_value
        self.remote.create_product.return_value = {"id": "ms-casio-a168"}
        self.remote.create_stock_enter_many.return_value = {
            "id": "enter-1",
            "name": "ПР-1",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }
        self.remote.update_stock_enter_many.return_value = {
            "id": "enter-1",
            "name": "ПР-1",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }
        self.remote.delete_stock_enter.return_value = True

    def tearDown(self):
        self.moysklad_patch.stop()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.environment.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp.cleanup()

    def stock(self):
        return ExcelProductCatalog(
            CatalogDatabase(self.database_path)
        ).get_product(self.product["id"])["stock"]

    def receipt_payload(self, quantity=10):
        return {
            "receipt_date": "2026-07-30",
            "note": "Интеграционный тест",
            "idempotency_key": "receipt-api-once",
            "positions": [{
                "product_id": str(self.product["id"]),
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "quantity": quantity,
                "purchase_price": 500,
            }],
        }

    def test_full_api_flow_uses_one_card_and_one_stock_ledger(self):
        receipt_response = self.client.post(
            "/api/v1/receipts",
            json=self.receipt_payload(),
        )
        self.assertEqual(receipt_response.status_code, 201)
        receipt = receipt_response.get_json()["data"]
        self.assertEqual(self.stock(), 10)
        self.remote.create_product.assert_called_once()
        remote_positions = (
            self.remote.create_stock_enter_many.call_args.kwargs["positions"]
        )
        self.assertEqual(
            remote_positions[0]["product_id"],
            "ms-casio-a168",
        )

        repeated = self.client.post(
            "/api/v1/receipts",
            json=self.receipt_payload(),
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.get_json()["data"]["id"], receipt["id"])
        self.remote.create_stock_enter_many.assert_called_once()
        self.assertEqual(self.stock(), 10)

        sale_response = self.client.post(
            "/api/v1/sales",
            json={
                "created_at": "2026-07-30",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "quantity": 3,
                "unit_price": 1000,
                "order_number": "ORDER-API-1",
            },
        )
        self.assertEqual(sale_response.status_code, 201)
        sale = sale_response.get_json()["data"]
        self.assertEqual(self.stock(), 7)

        edited = self.client.patch(
            "/api/v1/sales/{}".format(sale["id"]),
            json={
                "created_at": "2026-07-30",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "quantity": 2,
                "unit_price": 1000,
                "order_number": "ORDER-API-1",
            },
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(self.stock(), 8)

        cancelled = self.client.delete(
            "/api/v1/sales/{}".format(sale["id"])
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.stock(), 10)

        changed_payload = self.receipt_payload(quantity=6)
        changed_payload["idempotency_key"] = "receipt-update-once"
        changed_receipt = self.client.patch(
            "/api/v1/receipts/{}".format(receipt["id"]),
            json=changed_payload,
        )
        self.assertEqual(changed_receipt.status_code, 200)
        self.assertEqual(self.stock(), 6)

        movements = self.client.get(
            "/api/v1/products/{}/movements".format(self.product["id"])
        )
        self.assertEqual(movements.status_code, 200)
        movement_rows = movements.get_json()["data"]
        self.assertTrue(
            any(item.get("sale_id") == sale["id"] for item in movement_rows)
        )
        self.assertTrue(
            any(
                item.get("receipt_id") == receipt["id"]
                for item in movement_rows
            )
        )
        self.assertEqual(
            {item["type"] for item in movement_rows},
            {"receipt", "sale", "manual_adjustment", "cancellation"},
        )

        renamed = self.client.patch(
            "/api/v1/brands/{}".format(self.product["brand_id"]),
            json={"name": "Casio Japan"},
        )
        self.assertEqual(renamed.status_code, 200)
        receipt_listing = self.client.get("/api/v1/receipts").get_json()
        self.assertEqual(receipt_listing["data"][0]["brand"], "Casio Japan")

    def test_product_duplicate_returns_existing_card(self):
        response = self.client.post(
            "/api/v1/products",
            json={
                "name": "  casio a168 ",
                "article": "ANOTHER",
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "stock": 0,
            },
        )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["code"], "PRODUCT_ALREADY_EXISTS")
        self.assertEqual(
            payload["fields"]["existing"]["id"],
            str(self.product["id"]),
        )


if __name__ == "__main__":
    unittest.main()
