import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.category_consolidation import (
    CategoryConsolidation,
    CategoryConsolidationError,
)
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)


class CategoryConsolidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        ExcelProductBatchService(self.database).apply(
            [{
                "excel_row": 2,
                "excel_name": "Seed",
                "excel_brand": "Seed",
                "excel_article": "SEED",
                "article_quality": "code_like",
                "category": "Seed",
                "stock": 0,
                "stock_valid": True,
                "cell": "",
                "product_id": None,
                "match_status": "not_found",
                "match_method": "test",
                "confidence": 0,
                "alternatives": [],
            }],
            "c" * 64,
            "seed.xlsx",
        )
        self.products = ExcelProductCatalog(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def _fixture(self):
        first = self.products.create_product(
            name="Watch A", article="WATCH-A", brand="Brand A",
            category="Наручные часы", stock=5,
        )
        negative = self.products.create_product(
            name="Watch B", article="WATCH-B", brand="Brand B",
            category="Temporary", stock=5,
        )
        uncategorized = self.products.create_product(
            name="No category", article="NONE", brand="Brand C",
            category="", stock=7,
        )
        with self.database.transaction() as connection:
            brand_b = negative["brand_id"]
            brand_c = uncategorized["brand_id"]
            cursor = connection.execute(
                "INSERT INTO erp_categories "
                "(brand_id, name, normalized_name, active, created_at, updated_at) "
                "VALUES (?, 'НАРУЧНЫЕ   ЧАСЫ', 'наручные часы', 1, 'z', 'z')",
                (brand_b,),
            )
            duplicate_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE catalog_excel_products SET category_id = ?, stock = -5 "
                "WHERE id = ?", (duplicate_id, negative["id"]),
            )
            connection.execute(
                "INSERT INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, ?, 'z')",
                (brand_b, duplicate_id),
            )
            connection.execute(
                "INSERT INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, ?, 'z')",
                (brand_c, duplicate_id),
            )
            # Give the older survivor the strongest structural usage.
            connection.execute(
                "INSERT OR IGNORE INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, ?, 'a')",
                (brand_b, first["category_id"]),
            )
            connection.execute(
                "INSERT OR IGNORE INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, ?, 'a')",
                (brand_c, first["category_id"]),
            )
            connection.execute(
                "INSERT INTO erp_sales "
                "(id, source, status, created_at, inserted_at, updated_at) "
                "VALUES ('sale-history', 'test', 'completed', 'a', 'a', 'a')"
            )
            connection.execute(
                "INSERT INTO erp_sale_items "
                "(sale_id, product_id, brand_id, category_id, quantity, status, created_at) "
                "VALUES ('sale-history', ?, ?, ?, 1, 'completed', 'a')",
                (negative["id"], brand_b, duplicate_id),
            )
            connection.execute(
                "INSERT INTO erp_receipts "
                "(id, number, status, receipt_date, created_at, updated_at) "
                "VALUES ('receipt-history', 'R-1', 'posted', '2026-01-01', 'a', 'a')"
            )
            connection.execute(
                "INSERT INTO erp_receipt_items "
                "(receipt_id, product_id, brand_id, category_id, quantity, active, created_at) "
                "VALUES ('receipt-history', ?, ?, ?, 1, 1, 'a')",
                (negative["id"], brand_b, duplicate_id),
            )
        return first, negative, uncategorized, duplicate_id

    def test_dry_run_and_apply_preserve_inventory_and_history(self):
        first, negative, uncategorized, duplicate_id = self._fixture()
        connection = self.database.connect()
        try:
            migration = CategoryConsolidation(connection)
            dry_run = migration.build_plan()
            self.assertEqual(dry_run["safe_groups"], 1)
            self.assertEqual(dry_run["manual_review_groups"], 0)
            group = dry_run["groups"][0]
            self.assertEqual(group["canonical_id"], first["category_id"])
            self.assertEqual(group["products_to_move"], 1)
            self.assertEqual(group["before"]["stock"], 0)
            self.assertEqual(group["before"]["in_stock"], 2)
            self.assertEqual(group["brand_categories_to_insert"], 0)

            report = migration.apply(dry_run["plan_sha256"])

            self.assertEqual(report["second_dry_run_changes"], 0)
            self.assertEqual(report["baseline"], report["after_baseline"])
            self.assertEqual(
                report["immutable_history"],
                report["after_immutable_history"],
            )
            product = connection.execute(
                "SELECT category_id, stock, brand_id FROM catalog_excel_products "
                "WHERE id = ?", (negative["id"],)
            ).fetchone()
            self.assertEqual(product["category_id"], first["category_id"])
            self.assertEqual(product["stock"], -5)
            self.assertEqual(product["brand_id"], negative["brand_id"])
            self.assertIsNone(connection.execute(
                "SELECT category_id FROM catalog_excel_products WHERE id = ?",
                (uncategorized["id"],),
            ).fetchone()[0])
            self.assertEqual(connection.execute(
                "SELECT category_id FROM erp_sale_items WHERE sale_id = 'sale-history'"
            ).fetchone()[0], duplicate_id)
            self.assertEqual(connection.execute(
                "SELECT category_id FROM erp_receipt_items "
                "WHERE receipt_id = 'receipt-history'"
            ).fetchone()[0], duplicate_id)
            self.assertEqual(connection.execute(
                "SELECT active FROM erp_categories WHERE id = ?",
                (duplicate_id,),
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories "
                "WHERE category_id = ?", (duplicate_id,)
            ).fetchone()[0], 0)
            self.assertEqual(migration.apply()["applied_groups"], [])
        finally:
            connection.close()

    def test_empty_relation_is_moved_when_canonical_does_not_have_it(self):
        first, _, uncategorized, duplicate_id = self._fixture()
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM erp_brand_categories WHERE brand_id = ? "
                "AND category_id = ?",
                (uncategorized["brand_id"], first["category_id"]),
            )
        connection = self.database.connect()
        try:
            migration = CategoryConsolidation(connection)
            plan = migration.build_plan()
            self.assertEqual(
                plan["groups"][0]["brand_categories_to_insert"], 1
            )
            migration.apply(plan["plan_sha256"])
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories "
                "WHERE brand_id = ? AND category_id = ?",
                (uncategorized["brand_id"], first["category_id"]),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories "
                "WHERE brand_id = ? AND category_id = ?",
                (uncategorized["brand_id"], duplicate_id),
            ).fetchone()[0], 0)
        finally:
            connection.close()

    def test_unknown_operational_reference_requires_manual_review(self):
        first, _, _, duplicate_id = self._fixture()
        with self.database.transaction() as connection:
            connection.execute(
                "CREATE TABLE category_future_reference ("
                "id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL "
                "REFERENCES erp_categories(id) ON DELETE RESTRICT)"
            )
            connection.execute(
                "INSERT INTO category_future_reference (category_id) VALUES (?)",
                (duplicate_id,),
            )
        connection = self.database.connect()
        try:
            migration = CategoryConsolidation(connection)
            plan = migration.build_plan()
            self.assertEqual(plan["safe_groups"], 0)
            self.assertEqual(plan["manual_review_groups"], 1)
            self.assertEqual(
                plan["reference_contract"]["unknown"],
                ["category_future_reference"],
            )
            result = migration.apply(plan["plan_sha256"])
            self.assertEqual(result["applied_groups"], [])
            self.assertEqual(connection.execute(
                "SELECT active FROM erp_categories WHERE id = ?",
                (first["category_id"],),
            ).fetchone()[0], 1)
        finally:
            connection.close()

    def test_apply_rejects_stale_dry_run(self):
        self._fixture()
        connection = self.database.connect()
        try:
            migration = CategoryConsolidation(connection)
            plan = migration.build_plan()
            with self.assertRaises(CategoryConsolidationError):
                migration.apply("0" * 64)
            self.assertEqual(migration.build_plan()["plan_sha256"], plan["plan_sha256"])
        finally:
            connection.close()

if __name__ == "__main__":
    unittest.main()
