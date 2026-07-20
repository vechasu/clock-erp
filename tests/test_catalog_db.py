import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase


EXPECTED_TABLES = {
    "catalog_products",
    "catalog_categories",
    "catalog_product_categories",
    "catalog_properties",
    "catalog_product_property_values",
    "catalog_images",
    "catalog_offers",
    "catalog_offer_property_values",
    "catalog_prices",
    "catalog_moysklad_mappings",
    "catalog_sync_runs",
}


class CatalogDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "catalog.db"
        self.database = CatalogDatabase(self.database_path)
        self.database.initialize()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_creates_all_catalog_tables(self):
        self.assertEqual(set(self.database.table_names()), EXPECTED_TABLES)

    def test_external_product_identity_is_unique_but_name_and_article_are_not(self):
        product_values = (
            "Watch", "watch", "SKU", "Brand", "bitrix", "same-name",
            "hash", "{}", "2026-07-20T00:00:00Z",
        )
        insert = """
            INSERT INTO catalog_products (
                name, slug, article, brand, external_source, external_product_id,
                payload_hash, normalized_payload_json, created_at, updated_at,
                first_synced_at, last_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self.database.transaction() as connection:
            for external_id in ("1", "2"):
                connection.execute(insert, product_values[:5] + (external_id,) + product_values[6:] + (product_values[-1],) * 3)
        with self.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(insert, product_values[:5] + ("1",) + product_values[6:] + (product_values[-1],) * 3)

    def test_transaction_rolls_back_every_table_change(self):
        with self.assertRaises(RuntimeError):
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO catalog_categories "
                    "(external_category_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    ("10", "Watches", "now", "now"),
                )
                raise RuntimeError("stop")
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM catalog_categories").fetchone()[0]
        self.assertEqual(count, 0)

    def test_image_requires_exactly_one_owner(self):
        with self.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO catalog_images "
                    "(image_type, original_url, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    ("detail", "https://example.test/a.jpg", "now", "now"),
                )


if __name__ == "__main__":
    unittest.main()
