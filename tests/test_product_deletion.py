import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
    ProductDeleteBlockedError,
)
from app.services.receipt_inventory import ReceiptInventory, ReceiptInventoryError
from app.services.sales_inventory import SalesInventory, SalesInventoryError
from app.services.shared_catalog import SharedCatalog


def batch_row(stock=0):
    return {
        "excel_row": 2,
        "excel_name": "Deletion Test Watch",
        "excel_brand": "Casio",
        "excel_article": "DELETE-TEST-1",
        "article_quality": "code_like",
        "category": "Часы",
        "stock": float(stock),
        "stock_valid": True,
        "cell": "D-2",
        "product_id": None,
        "match_status": "not_found",
        "match_method": "test",
        "confidence": 0,
        "alternatives": [],
    }


class ProductDeletionTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(self.path)},
        )
        self.environment.start()
        self.database = CatalogDatabase(self.path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES ('delete-tests', 'delete-tests-sha', 'tests.xlsx', "
                "0, 0, 0, 0, 'active', ?, ?)",
                (
                    "2026-08-11T00:00:00+00:00",
                    "2026-08-11T00:00:00+00:00",
                ),
            )
        self.products = ExcelProductCatalog(self.database)
        self.catalog = SharedCatalog(self.database)
        self.sales = SalesInventory(self.database)
        self.receipts = ReceiptInventory(self.database)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def create_product(self, stock=0, article="DELETE-TEST-1"):
        return self.products.create_product(
            name="Deletion Test Watch",
            article=article,
            brand="Casio",
            category="Часы",
            stock=stock,
        )

    def tombstone(self, product_id):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ?",
                (int(product_id),),
            ).fetchone()
        return dict(row)

    def test_normal_delete_zero_creates_irreversible_tombstone(self):
        product = self.create_product(stock=0)

        result = self.products.delete_product(
            product["id"],
            actor_id="employee-1",
        )

        self.assertFalse(result["force"])
        self.assertIsNone(self.products.get_product(product["id"]))
        deleted = self.tombstone(product["id"])
        self.assertEqual(deleted["active"], 0)
        self.assertEqual(deleted["deleted_stock"], 0)
        self.assertEqual(deleted["delete_mode"], "normal")
        self.assertEqual(deleted["deleted_by"], "employee-1")
        self.assertTrue(deleted["deleted_at"])
        self.assertEqual(deleted["deleted_source_key"], product["source_key"])
        self.assertNotEqual(deleted["source_key"], product["source_key"])

    def test_nonzero_requires_force_for_positive_negative_and_fractional(self):
        for stock in (5, -2, 0.5):
            with self.subTest(stock=stock):
                product = self.create_product(
                    stock=0,
                    article="DELETE-{}".format(str(stock).replace(".", "-")),
                )
                with self.database.transaction() as connection:
                    connection.execute(
                        "UPDATE catalog_excel_products SET stock = ? WHERE id = ?",
                        (stock, product["id"]),
                    )
                with self.assertRaises(ProductDeleteBlockedError):
                    self.products.delete_product(product["id"])
                result = self.products.delete_product(
                    product["id"],
                    force=True,
                    actor_id="superadmin-1",
                )
                self.assertEqual(result["stock"], stock)
                deleted = self.tombstone(product["id"])
                self.assertEqual(deleted["deleted_stock"], stock)
                self.assertEqual(deleted["stock"], stock)
                self.assertEqual(deleted["delete_mode"], "force")

    def test_backend_rereads_stock_and_blocks_zero_stock_race(self):
        product = self.create_product(stock=0)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 3 WHERE id = ?",
                (product["id"],),
            )

        with self.assertRaises(ProductDeleteBlockedError):
            self.products.delete_product(product["id"])

        self.assertIsNotNone(self.products.get_product(product["id"]))

    def test_deleted_product_is_absent_from_active_catalog_and_can_be_recreated(self):
        product = self.create_product(stock=0)
        self.products.delete_product(product["id"])

        self.assertEqual(self.products.list_products(query="Deletion Test")["items"], [])
        self.assertEqual(self.catalog.list_products(query="Deletion Test"), [])
        self.assertIsNone(self.catalog.get_product(product["id"]))
        self.assertFalse(
            self.catalog.get_product(product["id"], include_archived=True)["active"]
        )
        for url in (
            "/api/v1/catalog/options?type=product&q=Deletion",
            "/api/v1/sales/catalog?q=Deletion",
            "/api/v1/receipts/catalog?q=Deletion",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["data"], [])

        recreated = self.create_product(stock=0)
        self.assertNotEqual(recreated["id"], product["id"])
        self.assertEqual(recreated["excel_article"], product["excel_article"])

    def test_deleted_product_cannot_be_used_for_new_sale_or_receipt(self):
        product = self.create_product(stock=2)
        self.products.delete_product(product["id"], force=True)

        with self.assertRaises(SalesInventoryError):
            self.sales.create_sale(
                {
                    "id": "new-sale",
                    "product_name": product["excel_name_raw"],
                },
                product["id"],
                1,
                100,
            )
        with self.assertRaises(ReceiptInventoryError):
            self.receipts.create_receipt(
                {"id": "new-receipt", "number": "NEW-1"},
                [{"product_id": product["id"], "quantity": 1}],
            )

    def test_historical_sale_and_ledger_survive_normal_delete(self):
        product = self.create_product(stock=2)
        sale = self.sales.create_sale(
            {
                "id": "historical-sale",
                "created_at": "2026-08-01T10:00:00+00:00",
                "product_name": product["excel_name_raw"],
                "article": product["excel_article"],
                "brand": product["excel_brand"],
                "category": product["excel_category"],
            },
            product["id"],
            2,
            100,
        )
        movements_before = self.sales.list_movements(product["id"])

        self.products.delete_product(product["id"])

        historical = self.sales.get_sale(sale["id"])
        self.assertEqual(historical["product_name"], product["excel_name_raw"])
        self.assertEqual(historical["article"], product["excel_article"])
        self.assertEqual(
            self.sales.list_movements(product["id"]),
            movements_before,
        )

    def test_historical_receipt_and_ledger_survive_force_delete(self):
        product = self.create_product(stock=0)
        receipt = self.receipts.create_receipt(
            {
                "id": "historical-receipt",
                "number": "PR-HISTORY",
                "receipt_date": "2026-08-01",
                "product_name": product["excel_name_raw"],
            },
            [{"product_id": product["id"], "quantity": 3}],
        )
        movements_before = self.sales.list_movements(product["id"])

        self.products.delete_product(product["id"], force=True)

        historical = self.receipts.get_receipt(receipt["id"])
        self.assertEqual(historical["items"][0]["product_id"], product["id"])
        self.assertEqual(
            self.sales.list_movements(product["id"]),
            movements_before,
        )

    def test_excel_batch_does_not_resurrect_deleted_source_key(self):
        service = ExcelProductBatchService(self.database)
        service.apply([batch_row()], "a" * 64, "first.xlsx")
        product = self.products.list_products(query="Deletion Test")["items"][0]
        self.products.delete_product(product["id"])

        service.apply([batch_row()], "b" * 64, "second.xlsx")

        self.assertEqual(self.products.list_products(query="Deletion Test")["items"], [])

    @mock.patch.object(web, "ExcelProductCatalog")
    def test_force_delete_requires_superadmin_and_confirmation(self, catalog_class):
        catalog_class.return_value.delete_product.return_value = {
            "stock": 5,
        }
        employee = {"id": 10, "role": "employee", "email": "e@example.test"}
        with mock.patch.object(web, "current_auth_user", return_value=employee):
            rejected = self.client.post(
                "/warehouse/archive",
                data={
                    "product_id": "42",
                    "force": "1",
                    "force_confirmation": "УДАЛИТЬ",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        self.assertEqual(rejected.status_code, 403)
        catalog_class.return_value.delete_product.assert_not_called()

        admin = {"id": 1, "role": "admin", "email": "a@example.test"}
        with mock.patch.object(web, "current_auth_user", return_value=admin):
            missing_phrase = self.client.post(
                "/warehouse/archive",
                data={"product_id": "42", "force": "1"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            accepted = self.client.post(
                "/warehouse/archive",
                data={
                    "product_id": "42",
                    "force": "1",
                    "force_confirmation": "УДАЛИТЬ",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        self.assertEqual(missing_phrase.status_code, 409)
        self.assertEqual(accepted.status_code, 200)
        catalog_class.return_value.delete_product.assert_called_once_with(
            "42",
            force=True,
            actor_id="1",
        )

    def test_direct_api_force_delete_rejects_employee_and_accepts_superadmin(self):
        product = self.create_product(stock=5, article="FORCE-API")
        payload = {"force": True, "force_confirmation": "УДАЛИТЬ"}
        employee = {"id": 10, "role": "employee", "email": "e@example.test"}
        with mock.patch.object(web, "current_auth_user", return_value=employee):
            rejected = self.client.delete(
                "/api/v1/products/{}".format(product["id"]),
                json=payload,
            )
        self.assertEqual(rejected.status_code, 403)
        self.assertIsNotNone(self.products.get_product(product["id"]))

        admin = {"id": 1, "role": "admin", "email": "a@example.test"}
        with mock.patch.object(web, "current_auth_user", return_value=admin):
            accepted = self.client.delete(
                "/api/v1/products/{}".format(product["id"]),
                json=payload,
            )
        self.assertEqual(accepted.status_code, 200)
        self.assertIsNone(self.products.get_product(product["id"]))

    def test_delete_form_has_csrf_and_force_delete_fields(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "templates"
            / "warehouse.html"
        ).read_text(encoding="utf-8")
        self.assertIn('name="csrf_token"', source)
        self.assertIn('data-can-force-delete=', source)
        self.assertIn("Принудительное удаление товара", source)
        self.assertIn('formData.set("force_confirmation", "УДАЛИТЬ")', source)


if __name__ == "__main__":
    unittest.main()
