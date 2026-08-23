import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.brand_inventory import BrandInventory
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.receipt_inventory import ReceiptInventory, ReceiptInventoryError
from app.services.sales_inventory import SalesInventory, SalesInventoryError
from app.services.sales_inventory import ReturnConflictError
from app.services.shared_catalog import CatalogReferenceError, SharedCatalog


class InventoryLockModeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,"
                "total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','test.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-18T09:00:00+00:00", "2026-08-18T09:00:00+00:00"),
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.shared = SharedCatalog(self.database)
        self.inventory = BrandInventory(self.database)
        self.sales = SalesInventory(self.database)
        self.receipts = ReceiptInventory(self.database)
        self.first = self.catalog.create_product(
            name="Alpha One", article="LOCK-1", brand="Alpha",
            category="Часы", stock=3,
        )
        self.second = self.catalog.create_product(
            name="Alpha Two", article="LOCK-2", brand="Alpha",
            category="Часы", stock=2,
        )
        self.other = self.catalog.create_product(
            name="Beta One", article="FREE-1", brand="Beta",
            category="Часы", stock=1,
        )
        self.brand_id = self.first["brand_id"]

    def tearDown(self):
        self.temp.cleanup()

    def start(self):
        return self.inventory.start(self.brand_id, "Максим")[0]

    def item(self, session, product):
        return next(
            item for item in self.inventory.list_items(session["id"])
            if item["product_id"] == product["id"]
        )

    def listed_ids(self):
        return {
            item["id"] for item in self.catalog.list_products(
                brand_id=self.brand_id, per_page=100
            )["items"]
        }

    def shared_ids(self):
        return {
            item["id"] for item in self.shared.list_products(
                brand_id=self.brand_id, limit=100
            )
        }

    def stock(self, product):
        with self.database.connect() as connection:
            return float(connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id = ?",
                (product["id"],),
            ).fetchone()[0])

    def test_snapshot_stays_locked_until_document_completion(self):
        session = self.start()
        self.assertEqual(self.listed_ids(), set())
        self.assertEqual(self.shared_ids(), set())
        self.assertEqual(session["locked_positions"], 2)
        self.assertEqual(session["progress_percent"], 0)

        self.inventory.confirm(
            session["id"], self.item(session, self.first)["id"], 3,
            idempotency_key="unlock-first",
        )
        self.assertEqual(self.listed_ids(), set())
        self.assertEqual(self.shared_ids(), set())
        progress = self.inventory.active_for_brand(self.brand_id)
        self.assertEqual(progress["checked_positions"], 1)
        self.assertEqual(progress["locked_positions"], 2)
        self.assertEqual(progress["progress_percent"], 50)

    def test_confirmed_zero_stays_locked_and_stock_filter_hides_it(self):
        session = self.start()
        self.inventory.confirm(
            session["id"], self.item(session, self.first)["id"], 0,
            idempotency_key="zero", confirm_zero=True,
        )
        self.assertNotIn(self.first["id"], self.listed_ids())
        in_stock = self.catalog.list_products(
            brand_id=self.brand_id, hide_zero=True, per_page=100
        )
        self.assertNotIn(self.first["id"], {item["id"] for item in in_stock["items"]})

    def test_sales_and_receipts_are_blocked_until_completion(self):
        session = self.start()
        with self.assertRaisesRegex(SalesInventoryError, "находится на инвентаризации"):
            self.sales.create_sale(
                {"id": "blocked-sale", "source": "Tictactoy"},
                self.first["id"], 1, 100,
            )
        with self.assertRaisesRegex(ReceiptInventoryError, "находится на инвентаризации"):
            self.receipts.create_receipt(
                {"id": "blocked-receipt", "number": "B-1", "receipt_date": "2026-08-18"},
                [{"product_id": self.first["id"], "quantity": 1}],
            )
        self.assertEqual(self.stock(self.first), 3)
        self.inventory.confirm(
            session["id"], self.item(session, self.first)["id"], 3,
            idempotency_key="confirm-before-sale",
        )
        with self.assertRaisesRegex(SalesInventoryError, "находится на инвентаризации"):
            self.sales.create_sale(
                {"id": "still-blocked", "source": "Tictactoy"},
                self.first["id"], 1, 100,
            )
        self.inventory.confirm(
            session["id"], self.item(session, self.second)["id"], 2,
            idempotency_key="confirm-second",
        )
        self.inventory.complete(session["id"], confirmation=True)
        self.sales.create_sale(
            {"id": "allowed-sale", "source": "Tictactoy"},
            self.first["id"], 1, 100,
        )
        self.receipts.create_receipt(
            {"id": "allowed-receipt", "number": "A-1", "receipt_date": "2026-08-18"},
            [{"product_id": self.first["id"], "quantity": 1}],
        )
        self.assertEqual(self.stock(self.first), 3)

    def test_batch_sale_with_one_locked_item_is_atomic(self):
        session = self.start()
        self.inventory.confirm(
            session["id"], self.item(session, self.first)["id"], 3,
            idempotency_key="batch-unlock-one",
        )
        with self.assertRaisesRegex(SalesInventoryError, "находится на инвентаризации"):
            self.sales.create_sale_batch(
                {"id": "blocked-batch", "source": "tictactoy"},
                [
                    {"product_id": self.first["id"], "quantity": 1},
                    {"product_id": self.second["id"], "quantity": 1},
                ],
            )
        self.assertEqual((self.stock(self.first), self.stock(self.second)), (3, 2))

    def test_return_and_receipt_cancellation_are_also_guarded(self):
        self.sales.create_sale(
            {"id": "before-inventory", "source": "Tictactoy"},
            self.first["id"], 1, 100,
        )
        receipt = self.receipts.create_receipt(
            {"id": "before-inventory-receipt", "number": "PRE", "receipt_date": "2026-08-18"},
            [{"product_id": self.second["id"], "quantity": 1}],
        )
        session = self.start()
        with self.assertRaisesRegex(ReturnConflictError, "находится на инвентаризации"):
            self.sales.return_sale("before-inventory", 1)
        with self.assertRaisesRegex(ReceiptInventoryError, "находится на инвентаризации"):
            self.receipts.cancel_receipt(receipt["id"])

        self.inventory.confirm(
            session["id"], self.item(session, self.first)["id"], 2,
            idempotency_key="release-return",
        )
        self.inventory.confirm(
            session["id"], self.item(session, self.second)["id"], 3,
            idempotency_key="release-receipt",
        )
        self.inventory.complete(session["id"], confirmation=True)
        self.sales.return_sale("before-inventory", 1)
        self.receipts.cancel_receipt(receipt["id"])
        self.assertEqual((self.stock(self.first), self.stock(self.second)), (3, 2))

    def test_stock_and_bulk_mutations_are_blocked_but_snapshot_metadata_is_not(self):
        self.start()
        with self.assertRaisesRegex(ValueError, "находится на инвентаризации"):
            self.catalog.update_product(self.first["id"], stock=4)
        self.catalog.update_product(self.first["id"], name="Snapshot renamed")
        created = self.catalog.create_product(
            name="Independent new SKU", article="LOCK-NEW", brand_id=self.brand_id,
            category="Часы", stock=1,
        )
        self.assertNotIn(created["id"], {
            item["product_id"] for item in self.inventory.list_items(
                self.inventory.active_for_brand(self.brand_id)["id"]
            )
        })
        self.catalog.update_product(
            self.other["id"], brand="Alpha", brand_id=self.brand_id
        )
        self.shared.rename_brand(self.brand_id, "Alpha Renamed")
        with self.assertRaisesRegex(ValueError, "инвентаризац"):
            self.catalog.delete_brand_catalog(self.brand_id, force=True)

    def test_cancel_and_completed_history_do_not_lock_products(self):
        session = self.start()
        self.inventory.cancel(session["id"], "Перенос", "Максим")
        self.assertEqual(self.listed_ids(), {self.first["id"], self.second["id"]})
        self.sales.create_sale(
            {"id": "after-cancel", "source": "Tictactoy"},
            self.first["id"], 1, 100,
        )

        completed = self.inventory.start(self.brand_id, "Максим")[0]
        for item in self.inventory.list_items(completed["id"]):
            self.inventory.confirm(
                completed["id"], item["id"], item["snapshot_stock"],
                idempotency_key="complete-{}".format(item["id"]),
            )
        self.inventory.complete(completed["id"], "Максим", confirmation=True)
        self.assertIsNone(self.inventory.active_for_brand(self.brand_id))
        self.assertEqual(
            self.catalog.list_products(brand_id=self.brand_id, per_page=100)["total"],
            2,
        )


class InventoryLockModeWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        database = CatalogDatabase(self.path)
        database.initialize()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,"
                "total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','test.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-18T09:00:00+00:00", "2026-08-18T09:00:00+00:00"),
            )
        self.catalog = ExcelProductCatalog(database)
        self.product = self.catalog.create_product(
            name="UI Locked", article="UI-LOCK", brand="UI Brand",
            category="Часы", stock=2,
        )
        self.brand_id = self.product["brand_id"]
        self.inventory = BrandInventory(database)
        self.session = self.inventory.start(self.brand_id, "Максим")[0]
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}
        )
        self.environment.start()
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def test_warehouse_banner_catalog_visibility_and_live_progress(self):
        page = self.client.get("/warehouse?brand_id={}".format(self.brand_id))
        markup = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("UI Brand · весь бренд находится на инвентаризации", markup)
        self.assertIn("Проверено 0 из 1", markup)
        self.assertIn("На инвентаризации: 1 позиция", markup)
        self.assertNotIn("UI Locked</a>", markup)

        sale_catalog = self.client.get(
            "/api/v1/sales/catalog?brand_id={}".format(self.brand_id)
        ).get_json()
        self.assertEqual(sale_catalog["data"], [])
        sale_brands = self.client.get(
            "/api/v1/catalog/options?type=brand&available_for_sale=1"
        ).get_json()
        self.assertNotIn(
            self.brand_id,
            {item["id"] for item in sale_brands["data"]},
        )

        item = self.inventory.list_items(self.session["id"])[0]
        self.inventory.confirm(
            self.session["id"], item["id"], 2,
            idempotency_key="ui-unlock",
        )
        refreshed = self.client.get("/warehouse?brand_id={}".format(self.brand_id))
        markup = refreshed.get_data(as_text=True)
        self.assertIn("Проверено 1 из 1", markup)
        self.assertIn("На инвентаризации: 1 позиция", markup)
        self.assertNotIn("UI Locked</a>", markup)
        available_brands = self.client.get(
            "/api/v1/catalog/options?type=brand&available_for_sale=1"
        ).get_json()
        self.assertNotIn(
            self.brand_id,
            {item["id"] for item in available_brands["data"]},
        )

    def test_direct_sale_and_receipt_posts_are_guarded_server_side(self):
        sale = self.client.post(
            "/api/v1/sales",
            json={
                "created_at": "2026-08-18",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "quantity": 1,
                "unit_price": 100,
                "order_number": "LOCKED-ORDER",
            },
        )
        self.assertEqual(sale.status_code, 422)
        self.assertIn("находится на инвентаризации", sale.get_json()["message"])

        with mock.patch.object(web, "MoySkladClient") as client:
            receipt = self.client.post(
                "/api/v1/receipts",
                json={
                    "receipt_date": "2026-08-18",
                    "positions": [{
                        "product_id": str(self.product["id"]),
                        "quantity": 1,
                    }],
                },
            )
        self.assertEqual(receipt.status_code, 409)
        self.assertIn("находится на инвентаризации", receipt.get_json()["message"])
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
