import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.brand_inventory import BrandInventory, InventoryConflict, InventoryError
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.inventory_control import InventoryControl, inventory_accuracy


class InventoryControlTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,"
                "total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','test.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-18T09:00:00+00:00", "2026-08-18T09:00:00+00:00"),
            )
        catalog = ExcelProductCatalog(self.database)
        self.first = catalog.create_product(
            name="Braun BC02BL", article="BC02BL", brand="Braun",
            category="Часы", model="BC02", stock=4,
        )
        self.second = catalog.create_product(
            name="Braun BC03", article="BC03", brand="Braun",
            category="Часы", model="BC03", stock=2,
        )
        self.zero = catalog.create_product(
            name="Braun Zero", article="ZERO", brand="Braun",
            category="Часы", stock=0,
        )
        self.inventory = BrandInventory(self.database)
        self.control = InventoryControl(self.database)
        with self.database.connect() as connection:
            self.brand_id = connection.execute(
                "SELECT brand_id FROM catalog_excel_products WHERE id=?",
                (self.first["id"],),
            ).fetchone()[0]

    def tearDown(self):
        self.temporary.cleanup()

    def complete_with_difference(self):
        session = self.inventory.start(self.brand_id, "Максим")[0]
        items = self.inventory.list_items(session["id"])
        self.assertEqual({item["product_id"] for item in items}, {self.first["id"], self.second["id"]})
        for item in items:
            actual = 2 if item["product_id"] == self.first["id"] else item["snapshot_stock"]
            self.inventory.confirm(
                session["id"], item["id"], actual, "Максим",
                "control-{}".format(item["id"]),
            )
        self.inventory.complete(session["id"], "Максим", confirmation=True)
        return session, next(item for item in items if item["product_id"] == self.first["id"])

    def test_stable_number_accuracy_filters_and_snapshot_analytics(self):
        session, item = self.complete_with_difference()
        first = self.control.document(session["id"])
        second = InventoryControl(self.database).document(session["id"])
        self.assertEqual(first["document_number"], "ИНВ-0001")
        self.assertEqual(second["document_number"], first["document_number"])
        self.assertEqual(first["accuracy"], 50.0)
        self.assertEqual(first["shortage"], 2)
        self.assertEqual(inventory_accuracy(0, 0), 100.0)
        listing = self.control.history({"q": "ИНВ-0001", "discrepancies": "1"}, 1, 25)
        self.assertEqual(listing["total"], 1)
        queue = self.control.discrepancies({"direction": "shortage"}, 1, 25)
        self.assertEqual(queue["rows"][0]["item_id"], item["id"])
        analytics = self.control.analytics("2000-01-01", "2999-12-31")
        self.assertEqual((analytics["documents"], analytics["positions"]), (1, 2))
        self.assertEqual((analytics["shortage"], analytics["surplus"]), (2, 0))

    def test_review_validation_audit_and_applied_adjustment_guard(self):
        session, item = self.complete_with_difference()
        with self.assertRaisesRegex(InventoryError, "обязателен комментарий"):
            self.control.update_review(item["id"], {"reason_code": "other"})
        review = self.control.update_review(item["id"], {
            "reason_code": "count_error", "reason_comment": "Пересчитано",
            "decision_code": "resolved", "review_status": "resolved",
        }, "1", "Максим")
        self.assertEqual(review["review_status"], "resolved")
        self.assertEqual(len(self.control.events(session["id"])), 1)
        self.assertEqual(self.control.document(session["id"])["control_status"], "resolved")
        with self.assertRaisesRegex(InventoryConflict, "уже применена"):
            self.control.adjust_once(item["id"], 1, "Максим")

    def test_brand_control_is_additive_and_prioritized(self):
        self.control.update_brand_control(
            self.brand_id, {"interval_days": 30, "enabled": True}, "Администратор"
        )
        brand = self.control.brand_summary()[0]
        self.assertEqual(brand["interval_days"], 30)
        self.assertEqual(brand["control_status"], "never")


if __name__ == "__main__":
    unittest.main()
