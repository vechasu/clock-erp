import json
import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.receipt_inventory import ReceiptInventory
from app.services.sales_inventory import SalesInventory
from app.services.shared_catalog import (
    DuplicateCatalogValueError,
    SharedCatalog,
)
from scripts.migrate_unified_catalog import (
    audit_legacy_links,
    backup_database,
    migrate,
    migration_applied,
    persist_legacy_audit,
)


class UnifiedCatalogInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES ('unified-test', 'unified-sha', 'test.xlsx', "
                "0, 0, 0, 0, 'active', ?, ?)",
                ("2026-07-30T08:00:00+00:00", "2026-07-30T08:00:00+00:00"),
            )
        self.products = ExcelProductCatalog(self.database)
        self.catalog = SharedCatalog(self.database)
        self.receipts = ReceiptInventory(self.database)
        self.sales = SalesInventory(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def create_product(
        self,
        name="Casio A168",
        brand="Casio",
        category="Наручные часы",
        stock=0,
    ):
        return self.products.create_product(
            name=name,
            article=name.replace(" ", "-").upper(),
            brand=brand,
            category=category,
            stock=stock,
        )

    def stock(self, product_id):
        return self.catalog.get_product(
            product_id,
            include_archived=True,
        )["stock"]

    def test_shared_taxonomy_normalizes_case_and_cascades_by_ids(self):
        casio = self.create_product()
        casio_variant = self.create_product(
            name="Casio F-91W",
            brand="  CASIO  ",
            category="Наручные часы",
        )
        seiko = self.create_product(
            name="Seiko 5",
            brand="Seiko",
            category="Механические часы",
        )

        with self.assertRaises(DuplicateCatalogValueError) as duplicate:
            self.catalog.create_brand("  cASIo  ")
        self.assertEqual(
            duplicate.exception.existing["id"],
            casio["brand_id"],
        )
        self.assertEqual(casio_variant["brand_id"], casio["brand_id"])
        self.assertEqual(casio_variant["category_id"], casio["category_id"])
        with self.assertRaises(DuplicateCatalogValueError) as alias:
            self.catalog.create_brand("Касио")
        self.assertEqual(alias.exception.existing["id"], casio["brand_id"])

        casio_categories = self.catalog.list_categories(
            brand_id=casio["brand_id"],
        )
        self.assertEqual(
            [item["id"] for item in casio_categories],
            [casio["category_id"]],
        )
        self.assertNotIn(
            seiko["category_id"],
            [item["id"] for item in casio_categories],
        )
        products = self.catalog.list_products(
            brand_id=casio["brand_id"],
            category_id=casio["category_id"],
        )
        self.assertEqual(
            {item["id"] for item in products},
            {str(casio["id"]), str(casio_variant["id"])},
        )

    def test_rename_is_visible_through_shared_product_without_relinking_history(self):
        product = self.create_product(stock=1)
        self.sales.create_sale(
            {
                "id": "rename-sale",
                "source": "Tictactoy",
                "created_at": "2026-07-30",
                "order_number": "REN-1",
                "product_name": product["display_name"],
                "brand": product["display_brand"],
                "category": product["display_category"],
            },
            product["id"],
            1,
            1000,
        )

        self.catalog.rename_brand(product["brand_id"], "Casio Japan")

        current = self.catalog.get_product(
            product["id"],
            include_archived=True,
        )
        self.assertEqual(current["brand"], "Casio Japan")
        self.assertEqual(
            self.sales.get_sale("rename-sale")["product_id"],
            str(product["id"]),
        )

    def test_atomic_full_inventory_scenario_and_idempotency(self):
        product = self.create_product()
        receipt = {
            "id": "receipt-1",
            "number": "ПР-1",
            "receipt_date": "2026-07-30",
        }
        positions = [{
            "product_id": product["id"],
            "quantity": 10,
            "purchase_price": 500,
        }]
        self.receipts.create_receipt(
            receipt,
            positions,
            idempotency_key="receipt-create-1",
        )
        self.receipts.create_receipt(
            receipt,
            positions,
            idempotency_key="receipt-create-1",
        )
        self.assertEqual(self.stock(product["id"]), 10)

        sale_payload = {
            "id": "sale-1",
            "source": "Tictactoy",
            "created_at": "2026-07-30",
            "order_number": "ORDER-1",
            "product_name": product["display_name"],
            "brand": product["display_brand"],
            "category": product["display_category"],
        }
        self.sales.create_sale(
            sale_payload,
            product["id"],
            3,
            1000,
            idempotency_key="sale-create-1",
            enforce_external_unique=True,
        )
        self.assertEqual(self.stock(product["id"]), 7)

        self.sales.update_sale(
            "sale-1",
            sale_payload,
            2,
            1000,
            idempotency_key="sale-update-1",
        )
        self.assertEqual(self.stock(product["id"]), 8)

        cancelled = self.sales.cancel_sale(
            "sale-1",
            idempotency_key="sale-cancel-1",
        )
        self.sales.cancel_sale(
            "sale-1",
            idempotency_key="sale-cancel-1",
        )
        self.assertEqual(cancelled["status"], "returned")
        self.assertEqual(self.stock(product["id"]), 10)

        self.receipts.update_receipt(
            "receipt-1",
            {**receipt, "number": "ПР-1"},
            [{**positions[0], "quantity": 6}],
            idempotency_key="receipt-update-1",
        )
        self.assertEqual(self.stock(product["id"]), 6)

        movements = self.sales.list_movements(product["id"])
        self.assertEqual(len(movements), 5)
        self.assertEqual(
            {item["type"] for item in movements},
            {"receipt", "sale", "manual_adjustment", "cancellation"},
        )
        self.assertTrue(
            any(item["sale_id"] == "sale-1" for item in movements)
        )
        self.assertTrue(
            any(item["receipt_id"] == "receipt-1" for item in movements)
        )

    def test_return_and_failures_change_stock_only_once_and_rollback(self):
        product = self.create_product(stock=3)
        sale_payload = {
            "id": "return-sale",
            "source": "Amazon",
            "created_at": "2026-07-30",
            "order_number": "AMZ-1",
        }
        self.sales.create_sale(
            sale_payload,
            product["id"],
            2,
            1000,
        )
        self.sales.return_sale(
            "return-sale",
            1,
            idempotency_key="return-once",
        )
        self.sales.return_sale(
            "return-sale",
            1,
            idempotency_key="return-once",
        )
        self.assertEqual(self.stock(product["id"]), 2)

        def fail(_connection):
            raise RuntimeError("forced rollback")

        with self.assertRaises(RuntimeError):
            self.receipts.create_receipt(
                {
                    "id": "failed-receipt",
                    "number": "FAIL",
                    "receipt_date": "2026-07-30",
                },
                [{
                    "product_id": product["id"],
                    "quantity": 5,
                    "purchase_price": 1,
                }],
                idempotency_key="failed-receipt",
                failure_hook=fail,
            )
        self.assertEqual(self.stock(product["id"]), 2)
        self.assertFalse(self.receipts.exists("failed-receipt"))

    def test_archive_keeps_sale_receipt_and_movement_history(self):
        product = self.create_product()
        self.receipts.create_receipt(
            {
                "id": "archive-receipt",
                "number": "АРХ-1",
                "receipt_date": "2026-07-30",
            },
            [{
                "product_id": product["id"],
                "quantity": 1,
                "purchase_price": 1,
            }],
        )
        self.sales.create_sale(
            {
                "id": "archive-sale",
                "source": "Wildberries",
                "created_at": "2026-07-30",
            },
            product["id"],
            1,
            100,
        )
        self.products.archive_product(product["id"])

        self.assertIsNone(self.products.get_product(product["id"]))
        archived = self.catalog.get_product(
            product["id"],
            include_archived=True,
        )
        self.assertFalse(archived["active"])
        self.assertIsNotNone(self.sales.get_sale("archive-sale"))
        self.assertIsNotNone(self.receipts.get_receipt("archive-receipt"))
        self.assertEqual(len(self.sales.list_movements(product["id"])), 2)

    def test_external_order_uniqueness_is_scoped_by_source(self):
        product = self.create_product(stock=2)
        first = self.sales.create_sale(
            {
                "id": "source-one",
                "source": "Tictactoy",
                "order_number": "42",
            },
            product["id"],
            1,
            100,
            enforce_external_unique=True,
        )
        repeated = self.sales.create_sale(
            {
                "id": "source-duplicate",
                "source": "Tictactoy",
                "order_number": "42",
            },
            product["id"],
            1,
            100,
            enforce_external_unique=True,
        )
        other_source = self.sales.create_sale(
            {
                "id": "other-source",
                "source": "Amazon",
                "order_number": "42",
            },
            product["id"],
            1,
            100,
            enforce_external_unique=True,
        )

        self.assertEqual(repeated["id"], first["id"])
        self.assertEqual(other_source["id"], "other-source")
        self.assertEqual(self.stock(product["id"]), 0)

    def test_migration_is_backed_up_repeatable_and_stock_neutral(self):
        product = self.create_product(stock=7)
        database_path = Path(self.database.path)
        backup = backup_database(
            database_path,
            Path(self.temp.name) / "backups",
        )
        before, after, _audit = migrate(database_path)

        self.assertTrue(backup.exists())
        self.assertEqual(before["products"], after["products"])
        self.assertEqual(before["stock_total"], 7)
        self.assertEqual(after["stock_total"], 7)
        self.assertTrue(migration_applied(database_path))
        self.assertEqual(self.stock(product["id"]), 7)

    def test_old_movement_constraint_is_migrated_without_losing_rows(self):
        product = self.create_product(stock=1)
        with self.database.connect() as connection:
            original_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_stock_movements"
            ).fetchone()[0]
            table_sql = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'catalog_stock_movements'"
            ).fetchone()[0]
            old_sql = table_sql.replace(
                "CREATE TABLE catalog_stock_movements",
                "CREATE TABLE catalog_stock_movements_old",
            ).replace("'cancellation',", "")
            connection.commit()
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(old_sql)
            connection.execute(
                "INSERT INTO catalog_stock_movements_old "
                "SELECT * FROM catalog_stock_movements"
            )
            connection.execute("DROP TABLE catalog_stock_movements")
            connection.execute(
                "ALTER TABLE catalog_stock_movements_old "
                "RENAME TO catalog_stock_movements"
            )
            connection.commit()
            connection.execute("PRAGMA foreign_keys = ON")

        self.database.initialize()

        with self.database.connect() as connection:
            migrated_sql = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'catalog_stock_movements'"
            ).fetchone()[0]
            migrated_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_stock_movements"
            ).fetchone()[0]
        self.assertIn("'cancellation'", migrated_sql)
        self.assertEqual(migrated_count, original_count)
        self.assertEqual(self.stock(product["id"]), 1)

    def test_exact_legacy_links_are_persisted_without_merging_cards(self):
        product = self.create_product()
        instance_dir = Path(self.temp.name)
        (instance_dir / "receipts.json").write_text(
            json.dumps([{
                "id": "legacy-receipt",
                "positions": [{
                    "product_id": "moysklad-a168",
                    "product_name": "Casio A168",
                    "brand": "casio",
                    "category": "Наручные часы",
                }],
            }]),
            encoding="utf-8",
        )
        (instance_dir / "manual_sales.json").write_text(
            json.dumps([{
                "id": "legacy-sale",
                "product_id": "",
                "product_name": "Casio A168",
                "brand": "Casio",
                "category": "Наручные часы",
            }]),
            encoding="utf-8",
        )

        audit = audit_legacy_links(self.database.path, instance_dir)
        persisted = persist_legacy_audit(self.database.path, audit)

        self.assertEqual(persisted["linked"], 2)
        self.assertEqual(persisted["ambiguous"], 0)
        self.assertEqual(
            self.catalog.legacy_links(
                "receipt",
                ["legacy-receipt"],
            )[("legacy-receipt", 0)],
            str(product["id"]),
        )
        self.assertEqual(
            self.catalog.get_product(product["id"])["moysklad_product_id"],
            "moysklad-a168",
        )

    def test_unmatched_legacy_receipt_is_materialized_with_shared_ids(self):
        instance_dir = Path(self.temp.name)
        (instance_dir / "receipts.json").write_text(
            json.dumps([{
                "id": "orphan-receipt",
                "positions": [{
                    "product_id": "moysklad-orphan",
                    "product_name": "Legacy Test",
                    "article": "LEGACY-1",
                    "brand": "Legacy Brand",
                    "category": "Legacy Category",
                    "cell": "L-1",
                    "quantity": 1,
                }],
            }]),
            encoding="utf-8",
        )

        before, after, audit = migrate(
            Path(self.database.path),
            instance_dir,
        )
        reconciliation = audit["legacy_reconciliation"]
        created = reconciliation["materialized"]["created"]

        self.assertEqual(len(created), 1)
        self.assertEqual(after["products"], before["products"] + 1)
        self.assertEqual(after["stock_total"], before["stock_total"])
        self.assertEqual(reconciliation["persisted"]["linked"], 1)
        self.assertEqual(reconciliation["persisted"]["unmatched"], 0)

        product = self.catalog.get_product(created[0]["product_id"])
        self.assertEqual(product["name"], "Legacy Test")
        self.assertEqual(product["brand"], "Legacy Brand")
        self.assertEqual(product["category"], "Legacy Category")
        self.assertEqual(product["moysklad_product_id"], "moysklad-orphan")
        self.assertIsInstance(product["brand_id"], int)
        self.assertIsInstance(product["category_id"], int)
        self.assertEqual(
            self.catalog.legacy_links(
                "receipt",
                ["orphan-receipt"],
            )[("orphan-receipt", 0)],
            product["id"],
        )


if __name__ == "__main__":
    unittest.main()
