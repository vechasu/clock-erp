import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.brand_inventory import BrandInventory
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.inventory_restoration import (
    InventoryRestorationError,
    InventorySnapshotRestoration,
)


class InventorySnapshotRestorationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,"
                "total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','test.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-18T09:00:00+00:00", "2026-08-18T09:00:00+00:00"),
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.inventory = BrandInventory(self.database)
        self.restoration = InventorySnapshotRestoration(self.database)
        self.one = self.catalog.create_product(
            name="ZIRO One", article="ZIRO-1", brand="ZIRO", category="Часы", stock=4
        )
        self.two = self.catalog.create_product(
            name="ZIRO Two", article="ZIRO-2", brand="ZIRO", category="Часы", stock=2
        )
        self.zero = self.catalog.create_product(
            name="ZIRO Zero Stock", article="ZIRO-0", brand="ZIRO",
            category="Часы", stock=0,
        )
        self.other = self.catalog.create_product(
            name="Zero Other", article="ZERO-1", brand="Zero", category="Часы", stock=7
        )

    def tearDown(self):
        self.temp.cleanup()

    def _stock(self, product_id):
        with self.database.connect() as connection:
            return float(connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id = ?", (product_id,)
            ).fetchone()[0])

    def _completed_inventory(self):
        session = self.inventory.start(self.one["brand_id"], "Максим")[0]
        for item in self.inventory.list_items(session["id"]):
            self.inventory.confirm(
                session["id"], item["id"], 0,
                idempotency_key="wrong-zero-{}".format(item["id"]),
                confirm_zero=True,
            )
        self.inventory.complete(session["id"], "Максим", confirmation=True)
        return session

    def test_exact_brand_is_required(self):
        self._completed_inventory()
        with self.assertRaisesRegex(InventoryRestorationError, "точным названием"):
            self.restoration.plan("zero")

    def test_restore_is_atomic_audited_and_idempotent(self):
        completed = self._completed_inventory()
        self.catalog.delete_product(self.one["id"])
        active = self.inventory.start(self.one["brand_id"], "Максим")[0]

        plan = self.restoration.plan("ZIRO", completed["id"])
        self.assertEqual(plan["snapshot_positions"], 2)
        self.assertEqual(plan["snapshot_stock"], 6)
        self.assertEqual(plan["positions_to_restore"], 2)
        self.assertEqual(plan["stock_delta"], 6)
        self.assertEqual(plan["active_session_ids"], [active["id"]])

        result = self.restoration.apply(
            "ZIRO", completed["id"],
            reason="Восстановление товаров ZIRO после ошибочной инвентаризации",
            user_name="Codex",
        )
        self.assertEqual(result["created_operation_count"], 2)
        self.assertEqual((self._stock(self.one["id"]), self._stock(self.two["id"])), (4, 2))
        self.assertEqual(self._stock(self.zero["id"]), 0)
        self.assertEqual(self._stock(self.other["id"]), 7)
        visible = self.catalog.list_products(
            brand_id=self.one["brand_id"], per_page=100
        )["items"]
        self.assertEqual({row["id"] for row in visible}, {
            self.one["id"], self.two["id"], self.zero["id"],
        })
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM erp_inventory_sessions WHERE id = ?", (active["id"],)
            ).fetchone()[0], "cancelled")
            movements = connection.execute(
                "SELECT COUNT(*), SUM(quantity_delta) FROM catalog_stock_movements "
                "WHERE source_type = 'inventory_restore' AND source_id = ?",
                (completed["id"],),
            ).fetchone()
            self.assertEqual(tuple(movements), (2, 6))
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_audit_events "
                "WHERE entity_type = 'inventory' AND entity_id = ? AND action = 'restored'",
                (completed["id"],),
            ).fetchone()[0], 1)

        repeated = self.restoration.apply(
            "ZIRO", completed["id"],
            reason="Восстановление товаров ZIRO после ошибочной инвентаризации",
            user_name="Codex",
        )
        self.assertEqual(repeated["created_operation_count"], 0)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM catalog_stock_movements "
                "WHERE source_type = 'inventory_restore' AND source_id = ?",
                (completed["id"],),
            ).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
