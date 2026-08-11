import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.excel_product_catalog import (
    ExcelProductCatalog,
    ProductDeleteBlockedError,
)
from app.services.shared_catalog import DuplicateCatalogValueError, SharedCatalog


class BrandManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES ('brands', 'brands-sha', 'brands.xlsx', 0, 0, 0, 0, "
                "'active', '2026-08-12T00:00:00+00:00', "
                "'2026-08-12T00:00:00+00:00')"
            )
        self.products = ExcelProductCatalog(self.database)
        self.catalog = SharedCatalog(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def product(self, name, article, brand, category, stock=0):
        return self.products.create_product(
            name=name,
            article=article,
            brand=brand,
            category=category,
            stock=stock,
        )

    def test_empty_brand_and_category_are_persisted_and_normalized(self):
        brand = self.catalog.create_brand("  Casio  ")
        category = self.catalog.create_brand_category(
            brand["id"], "  Аксессуары  "
        )
        overview = self.catalog.get_brand_overview(brand["id"])

        self.assertEqual(overview["name"], "Casio")
        self.assertEqual(overview["product_count"], 0)
        self.assertEqual(overview["nonzero_count"], 0)
        self.assertEqual(overview["categories"][0]["id"], category["id"])
        self.assertEqual(overview["categories"][0]["product_count"], 0)
        with self.assertRaises(DuplicateCatalogValueError):
            self.catalog.create_brand("casio")
        with self.assertRaises(DuplicateCatalogValueError):
            self.catalog.create_brand_category(brand["id"], "аксессуары")

    def test_existing_global_category_is_linked_without_duplicate(self):
        casio = self.catalog.create_brand("Casio")
        seiko = self.catalog.create_brand("Seiko")
        first = self.catalog.create_brand_category(casio["id"], "Часы")
        second = self.catalog.create_brand_category(seiko["id"], "часы")

        self.assertEqual(first["id"], second["id"])
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM erp_categories WHERE normalized_name = 'часы'"
            ).fetchone()[0]
            links = connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories WHERE category_id = ?",
                (first["id"],),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(links, 2)

    def test_aggregate_distinguishes_nonzero_products_from_zero_sum(self):
        first = self.product("A", "A", "Casio", "Часы", 0)
        second = self.product("B", "B", "Casio", "Часы", 0)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 5 WHERE id = ?",
                (first["id"],),
            )
            connection.execute(
                "UPDATE catalog_excel_products SET stock = -5 WHERE id = ?",
                (second["id"],),
            )
        overview = self.catalog.get_brand_overview(first["brand_id"])

        self.assertEqual(overview["stock_total"], 0)
        self.assertEqual(overview["nonzero_count"], 2)
        self.assertEqual(overview["categories"][0]["nonzero_count"], 2)

    def test_bulk_prevalidation_is_atomic_and_force_preserves_history(self):
        zero = self.product("Zero", "ZERO", "Casio", "Часы", 0)
        nonzero = self.product("Stock", "STOCK", "Casio", "Часы", 0)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 2 WHERE id = ?",
                (nonzero["id"],),
            )
        with self.assertRaises(ProductDeleteBlockedError):
            self.products.delete_brand_catalog(zero["brand_id"])
        self.assertIsNotNone(self.products.get_product(zero["id"]))
        self.assertIsNotNone(self.products.get_product(nonzero["id"]))

        result = self.products.delete_brand_catalog(
            zero["brand_id"], force=True, actor_id="admin"
        )
        self.assertEqual(result["products_deleted"], 2)
        self.assertIsNone(self.products.get_product(zero["id"]))
        self.assertIsNone(self.products.get_product(nonzero["id"]))
        events = AuditJournal(self.database).list_events(limit=20)["events"]
        self.assertTrue(any(event["entity_type"] == "brand" for event in events))
        self.assertEqual(
            len([event for event in events if event["entity_type"] == "product"]),
            4,
        )

    def test_category_delete_is_scoped_to_brand_and_keeps_global_category(self):
        casio = self.product("Casio A", "CA", "Casio", "Аксессуары", 0)
        seiko = self.product("Seiko A", "SA", "Seiko", "Аксессуары", 0)

        self.products.delete_brand_catalog(
            casio["brand_id"], category_id=casio["category_id"]
        )

        self.assertIsNone(self.products.get_product(casio["id"]))
        self.assertIsNotNone(self.products.get_product(seiko["id"]))
        with self.database.connect() as connection:
            category = connection.execute(
                "SELECT active FROM erp_categories WHERE id = ?",
                (casio["category_id"],),
            ).fetchone()
            casio_link = connection.execute(
                "SELECT 1 FROM erp_brand_categories WHERE brand_id = ? "
                "AND category_id = ?",
                (casio["brand_id"], casio["category_id"]),
            ).fetchone()
            seiko_link = connection.execute(
                "SELECT 1 FROM erp_brand_categories WHERE brand_id = ? "
                "AND category_id = ?",
                (seiko["brand_id"], seiko["category_id"]),
            ).fetchone()
        self.assertEqual(category["active"], 1)
        self.assertIsNone(casio_link)
        self.assertIsNotNone(seiko_link)


if __name__ == "__main__":
    unittest.main()
