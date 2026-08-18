import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.out_of_stock import OutOfStockChecks


class OutOfStockChecksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES ('batch', 'sha', 'test.xlsx', 0, 0, 0, 0, "
                "'active', '2026-08-18T10:00:00+00:00', "
                "'2026-08-18T10:00:00+00:00')"
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.checks = OutOfStockChecks(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def product(self, stock=0, article="Z-1"):
        return self.catalog.create_product(
            name="Ziiiro Celeste",
            model="Celeste",
            article=article,
            brand="Ziiiro",
            category="Часы",
            stock=stock,
        )

    def test_zero_and_negative_stock_are_listed_but_positive_stock_is_not(self):
        zero = self.product(0, "Z-0")
        negative = self.product(1, "Z-N")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = -1 WHERE id = ?",
                (negative["id"],),
            )
        self.product(2, "Z-P")

        listing = self.catalog.list_products(
            stock_state="out", include_inventory_locked=True
        )

        self.assertEqual(
            {item["id"] for item in listing["items"]},
            {zero["id"], negative["id"]},
        )

    def test_checks_are_independent_filterable_audited_and_idempotent(self):
        product = self.product()
        first = self.checks.set_check(
            product["id"], "ziiiro", True, actor_name="Максим"
        )
        repeated = self.checks.set_check(
            product["id"], "ziiiro", True, actor_name="Максим"
        )

        self.assertTrue(first["checks"]["ziiiro"]["checked"])
        self.assertFalse(first["checks"]["wildberries"]["checked"])
        self.assertEqual(first["cycle_id"], repeated["cycle_id"])
        self.assertEqual(
            self.catalog.list_products(
                stock_state="out", check_state="partial",
                include_inventory_locked=True,
            )["total"],
            1,
        )
        with self.database.connect() as connection:
            events = connection.execute(
                "SELECT COUNT(*) FROM erp_audit_events "
                "WHERE entity_type = 'product' AND entity_id = ? "
                "AND action = 'updated'",
                (str(product["id"]),),
            ).fetchone()[0]
        self.assertEqual(events, 1)

    def test_replenishment_closes_cycle_and_next_outage_starts_clean_cycle(self):
        product = self.product()
        first = self.checks.set_check(
            product["id"], "tictactoy", True, actor_name="Максим"
        )
        self.catalog.update_product(product["id"], stock=2)
        self.checks.sync()
        self.assertEqual(self.checks.current_for_products([product["id"]]), {})

        self.catalog.update_product(product["id"], stock=0)
        second = self.checks.current_for_products([product["id"]])[product["id"]]

        self.assertNotEqual(first["cycle_id"], second["cycle_id"])
        self.assertEqual(second["state"], "unchecked")
        with self.database.connect() as connection:
            history = connection.execute(
                "SELECT id, ended_at FROM erp_out_of_stock_cycles "
                "WHERE product_id = ? ORDER BY id",
                (product["id"],),
            ).fetchall()
        self.assertEqual(len(history), 2)
        self.assertTrue(history[0]["ended_at"])
        self.assertIsNone(history[1]["ended_at"])

    def test_model_is_saved_and_searchable(self):
        product = self.product()
        found = self.catalog.list_products(
            query="Cel", include_inventory_locked=True
        )
        self.assertEqual(found["items"][0]["id"], product["id"])
        self.assertEqual(found["items"][0]["model"], "Celeste")


if __name__ == "__main__":
    unittest.main()
