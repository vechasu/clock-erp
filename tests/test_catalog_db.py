import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.services.catalog_reader import CatalogReader
from app.services.excel_product_catalog import ExcelProductCatalog


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
    "catalog_excel_batches",
    "catalog_excel_products",
    "catalog_excel_batch_rows",
    "catalog_excel_stock_operations",
    "catalog_excel_match_audit",
    "catalog_excel_import_drafts",
    "catalog_excel_import_draft_rows",
    "catalog_excel_receipts",
    "catalog_excel_receipt_rows",
    "catalog_excel_receipt_operations",
    "catalog_excel_manual_stock_operations",
    "catalog_stock_movements",
    "catalog_product_classification_audit",
}


class CatalogDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "catalog.db"
        # Migration tests intentionally mutate the schema between initialize
        # calls, so they opt out of the production process cache.
        self.database = CatalogDatabase(
            self.database_path,
            cache_initialization=False,
        )
        self.database.initialize()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_creates_all_catalog_tables(self):
        self.assertEqual(set(self.database.table_names()), EXPECTED_TABLES)

    def test_legacy_order_mapping_schema_migrates_without_losing_relation(self):
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches "
                "(id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('mapping-batch', 'mapping-sha', 'mapping.xlsx', "
                "0, 0, 0, 0, 'active', '2026-08-21', '2026-08-21')"
            )
        product = ExcelProductCatalog(self.database).create_product(
            name="Nato 84", article="NATO-84", brand="Diloy",
            category="Ремни", stock=1,
        )
        with self.database.connect() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript("""
                DROP TABLE erp_order_product_mappings;
                CREATE TABLE erp_order_product_mappings (
                    order_id TEXT NOT NULL,
                    order_line_id TEXT NOT NULL,
                    product_id INTEGER NOT NULL,
                    product_name TEXT,
                    brand_id TEXT,
                    category_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (order_id, order_line_id)
                );
            """)
            connection.execute(
                "INSERT INTO erp_order_product_mappings VALUES "
                "(?, ?, ?, 'Nato 84', 'legacy-brand', 'legacy-category', ?, ?)",
                ("21112", "199696", product["id"], "2026-08-21", "2026-08-21"),
            )
            connection.commit()

        CatalogDatabase(
            self.database_path,
            cache_initialization=False,
        ).initialize()

        with self.database.connect() as connection:
            columns = [
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(erp_order_product_mappings)"
                ).fetchall()
            ]
            row = connection.execute(
                "SELECT order_id, order_item_id, product_id "
                "FROM erp_order_product_mappings"
            ).fetchone()
        self.assertEqual(columns, [
            "order_id", "order_item_id", "product_id", "created_at", "updated_at"
        ])
        self.assertEqual(tuple(row), ("21112", "199696", product["id"]))

    def test_cached_initialization_runs_schema_checks_only_once(self):
        database = CatalogDatabase(
            self.database_path,
            cache_initialization=True,
        )
        with mock.patch.object(
            database,
            "_initialize_schema",
            wraps=database._initialize_schema,
        ) as initialize_schema:
            database.initialize()
            database.initialize()

        initialize_schema.assert_called_once_with()

    def test_cached_initialization_is_reused_by_service_instances(self):
        original_initialize_schema = CatalogDatabase._initialize_schema
        initialize_calls = []

        def tracked_initialize_schema(database):
            initialize_calls.append(database)
            return original_initialize_schema(database)

        with mock.patch.object(
            CatalogDatabase,
            "_initialize_schema",
            tracked_initialize_schema,
        ):
            CatalogDatabase(
                self.database_path,
                cache_initialization=True,
            ).initialize()
            CatalogDatabase(
                self.database_path,
                cache_initialization=True,
            ).initialize()

        self.assertEqual(len(initialize_calls), 1)

    def test_inventory_scope_migration_is_additive_idempotent_and_preserves_legacy_rows(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE erp_inventory_sessions (
                id TEXT PRIMARY KEY,
                brand_id INTEGER NOT NULL,
                active_brand_id INTEGER,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                start_positions INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_erp_inventory_one_active_brand
                ON erp_inventory_sessions(active_brand_id);
            CREATE TABLE erp_inventory_items (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                snapshot_stock INTEGER NOT NULL,
                status TEXT NOT NULL,
                appearance TEXT NOT NULL,
                snapshot_at TEXT NOT NULL,
                snapshot_movement_rowid INTEGER NOT NULL DEFAULT 0,
                reactivated INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO erp_inventory_sessions VALUES
                ('legacy', 7, 7, 'completed', '2026-08-01', 1, '2026-08-01');
            INSERT INTO erp_inventory_items VALUES
                ('legacy-item', 'legacy', 99, 4, 'confirmed', 'snapshot',
                 '2026-08-01', 0, 0);
        """)

        CatalogDatabase._ensure_inventory_constraints(connection)
        CatalogDatabase._ensure_inventory_constraints(connection)

        session_columns = {
            row["name"] for row in connection.execute(
                "PRAGMA table_info(erp_inventory_sessions)"
            )
        }
        item_columns = {
            row["name"] for row in connection.execute(
                "PRAGMA table_info(erp_inventory_items)"
            )
        }
        legacy = connection.execute(
            "SELECT id, brand_id, status, start_positions, scope_type "
            "FROM erp_inventory_sessions WHERE id='legacy'"
        ).fetchone()
        legacy_item = connection.execute(
            "SELECT id, product_id, snapshot_stock, status, snapshot_name "
            "FROM erp_inventory_items WHERE id='legacy-item'"
        ).fetchone()
        indexes = {
            row["name"] for row in connection.execute(
                "PRAGMA index_list(erp_inventory_sessions)"
            )
        }
        connection.close()

        self.assertTrue({
            "scope_type", "category_id", "model_id", "idempotency_key",
            "scope_brand_name", "scope_category_name", "scope_model_name",
        }.issubset(session_columns))
        self.assertTrue({
            "snapshot_name", "snapshot_article", "snapshot_brand_id",
            "snapshot_category_id", "snapshot_model_id",
            "snapshot_brand_name", "snapshot_category_name",
            "snapshot_model_name", "snapshot_photo_url",
        }.issubset(item_columns))
        self.assertEqual(tuple(legacy), ("legacy", 7, "completed", 1, None))
        self.assertEqual(tuple(legacy_item), ("legacy-item", 99, 4, "confirmed", None))
        self.assertNotIn("idx_erp_inventory_one_active_brand", indexes)
        self.assertIn("idx_erp_inventory_idempotency", indexes)
        self.assertIn("idx_erp_inventory_sessions_scope", indexes)

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

    def test_active_brand_model_values_are_unique_sorted_and_canonical(self):
        with self.database.transaction() as connection:
            property_id = connection.execute(
                "INSERT INTO catalog_properties "
                "(external_property_id, code, name, property_type, created_at, updated_at) "
                "VALUES ('86', 'BRAND_MODEL', 'Марка часов', 'list', 'now', 'now')"
            ).lastrowid
            for index, (brand, active) in enumerate((
                ("  Zeta  ", 1),
                ("zeta", 1),
                ("A & Co.", 1),
                ("Archived", 0),
                ("   ", 1),
                ("Бренд.ру", 1),
            ), 1):
                product_id = connection.execute(
                    "INSERT INTO catalog_products "
                    "(name, brand, active, external_source, external_product_id, "
                    "payload_hash, normalized_payload_json, created_at, updated_at, "
                    "first_synced_at, last_synced_at) "
                    "VALUES (?, ?, ?, 'bitrix', ?, ?, '{}', 'now', 'now', 'now', 'now')",
                    ("Watch {}".format(index), brand, active, str(index), "hash-{}".format(index)),
                ).lastrowid
                connection.execute(
                    "INSERT INTO catalog_product_property_values "
                    "(product_id, property_id, value_json, display_value_json) "
                    "VALUES (?, ?, ?, ?)",
                    (product_id, property_id, json.dumps(brand), json.dumps(brand)),
                )

        reader = CatalogReader(self.database)
        self.assertEqual(
            reader.list_active_brands(),
            ["A & Co.", "Zeta", "Бренд.ру"],
        )
        self.assertEqual(reader.canonical_active_brand(" a  & CO. "), "A & Co.")
        self.assertIsNone(reader.canonical_active_brand("Casi0"))


if __name__ == "__main__":
    unittest.main()
