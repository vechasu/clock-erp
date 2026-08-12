import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.category_integrity import CategoryIntegrityRepair
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)


class CategoryIntegrityRepairTest(unittest.TestCase):
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
            "a" * 64,
            "seed.xlsx",
        )
        self.products = ExcelProductCatalog(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_scoped_repair_preserves_products_stock_and_other_brands(self):
        first = self.products.create_product(
            name="Barcelona One", article="B-1", brand="666 Barcelona",
            category="Наручные часы", stock=5,
        )
        second = self.products.create_product(
            name="Barcelona Two", article="B-2", brand="666 Barcelona",
            category="Наручные часы", stock=7,
        )
        other = self.products.create_product(
            name="Other", article="O-1", brand="Other",
            category="Наручные часы", stock=11,
        )
        uncategorized = self.products.create_product(
            name="No category", article="B-0", brand="666 Barcelona",
            category="", stock=13,
        )
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO erp_categories "
                "(brand_id, name, normalized_name, active, created_at, updated_at) "
                "VALUES (?, 'наручные   часы', 'наручные   часы', 1, 'x', 'x')",
                (first["brand_id"],),
            )
            duplicate_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, ?, 'x')",
                (first["brand_id"], duplicate_id),
            )
            connection.execute(
                "UPDATE catalog_excel_products SET category_id = ? "
                "WHERE id IN (?, ?)",
                (duplicate_id, second["id"], other["id"]),
            )

        connection = self.database.connect()
        try:
            repair = CategoryIntegrityRepair(
                connection, " 666 Barcelona ", "НАРУЧНЫЕ  ЧАСЫ"
            )
            dry_run = repair.diagnose(include_global_audit=True)
            self.assertEqual(
                dry_run["dry_run"]["catalog_excel_products_updated"], 1
            )
            self.assertEqual(dry_run["uncategorized"]["product_count"], 1)
            before_ids = dry_run["before"]["product_ids"]
            before_stock = dry_run["before"]["stock_total"]

            report = repair.apply()

            self.assertEqual(report["after"]["product_ids"], before_ids)
            self.assertEqual(report["after"]["stock_total"], before_stock)
            self.assertEqual(report["before"]["product_count"], 3)
            self.assertEqual(report["after"]["product_count"], 3)
            self.assertEqual(report["applied"]["catalog_excel_products_updated"], 1)
            self.assertEqual(
                connection.execute(
                    "SELECT category_id FROM catalog_excel_products WHERE id = ?",
                    (second["id"],),
                ).fetchone()[0],
                first["category_id"],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT category_id FROM catalog_excel_products WHERE id = ?",
                    (other["id"],),
                ).fetchone()[0],
                duplicate_id,
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT category_id FROM catalog_excel_products WHERE id = ?",
                    (uncategorized["id"],),
                ).fetchone()[0]
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
