import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.product_model_backfill import (
    ProductModelBackfill,
    normalize_model_key,
)
from app.services.shared_catalog import SharedCatalog


class ProductModelBackfillTest(unittest.TestCase):
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
                "'active', '2026-08-21T10:00:00+00:00', '2026-08-21T10:00:00+00:00')"
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.service = ProductModelBackfill(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def product(self, name, article, brand="Braun", model="", properties=None, stock=3):
        product = self.catalog.create_product(
            name=name, article=article, brand=brand, category="Будильники",
            model=model, stock=stock,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_name = ?, "
                "bitrix_source_url = ?, bitrix_properties_json = ? WHERE id = ?",
                (name, "https://example.test/{}/".format(article),
                 json.dumps(properties or [], ensure_ascii=False), product["id"]),
            )
        return product

    def test_braun_variants_share_one_normalized_model_and_are_idempotent(self):
        black = self.product("BC03B Black", "BC03B")
        white = self.product("BC03W White", "BC03W")
        before = {}
        with self.database.connect() as connection:
            for row in connection.execute(
                    "SELECT id, stock, excel_article, excel_brand, excel_category, bitrix_source_url FROM catalog_excel_products"):
                before[row["id"]] = tuple(row)

        preview = self.service.dry_run()
        self.assertEqual(preview["summary"]["high_confidence"], 2)
        self.assertEqual({item["proposed_model"] for item in preview["items"]}, {"BC03"})

        result = self.service.apply()
        self.assertEqual(result["writes_performed"], 2)
        self.assertTrue(Path(result["backup_path"]).is_file())
        with self.database.connect() as connection:
            products = connection.execute(
                "SELECT id, model, model_id, stock, excel_article, excel_brand, excel_category, bitrix_source_url "
                "FROM catalog_excel_products ORDER BY id"
            ).fetchall()
            self.assertEqual({row["model"] for row in products}, {"BC03"})
            self.assertEqual(len({row["model_id"] for row in products}), 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM erp_models").fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_audit_events WHERE source_snapshot = 'model_backfill'"
            ).fetchone()[0], 2)
            for row in products:
                self.assertEqual(
                    (row["id"], row["stock"], row["excel_article"], row["excel_brand"],
                     row["excel_category"], row["bitrix_source_url"]),
                    before[row["id"]],
                )
        repeated = self.service.apply()
        self.assertEqual(repeated["writes_performed"], 0)
        self.assertEqual(
            self.catalog.list_products(model="bc 03", include_inventory_locked=True)["total"], 2
        )
        category = SharedCatalog(self.database).get_category_overview(
            black["category_id"]
        )
        self.assertEqual(category["model_count"], 1)
        self.assertEqual(category["models"][0]["name"], "BC03")

    def test_explicit_bitrix_model_has_priority_but_brand_model_is_ignored(self):
        product = self.product(
            "Unknown Black", "unknown-black", brand="Example",
            properties=[
                {"code": "BRAND_MODEL", "name": "Марка часов", "value": "Example"},
                {"code": "MODEL", "name": "Модель", "value": "  Alpha  01 "},
            ],
        )
        item = next(item for item in self.service.dry_run()["items"] if item["product_id"] == product["id"])
        self.assertEqual(item["proposed_model"], "Alpha 01")
        self.assertEqual(item["source"], "bitrix_property")
        self.assertEqual(item["confidence"], "high")

    def test_existing_model_is_preserved_and_conflict_is_reported(self):
        product = self.product("BC03B Black", "BC03B", model="Legacy Model")
        preview = self.service.dry_run()
        self.assertEqual(preview["summary"]["conflicts"], 1)
        self.service.apply()
        stored = self.catalog.get_product(product["id"])
        self.assertEqual(stored["model"], "Legacy Model")
        self.assertIsNotNone(stored["model_id"])

    def test_unknown_textual_guess_is_not_applied(self):
        product = self.product("Something Black", "something-black", brand="Unknown")
        item = self.service.dry_run()["items"][0]
        self.assertEqual(item["confidence"], "low")
        self.assertEqual(item["action"], "requires_review")
        self.assertEqual(self.service.apply()["writes_performed"], 0)
        self.assertFalse(self.catalog.get_product(product["id"])["model"])

    def test_active_inventory_blocks_before_backup_or_write(self):
        product = self.product("BC03B Black", "BC03B")
        with self.database.transaction() as connection:
            brand_id = connection.execute(
                "SELECT brand_id FROM catalog_excel_products WHERE id = ?", (product["id"],)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO erp_inventory_sessions (id, brand_id, active_brand_id, status, "
                "started_at, updated_at) VALUES ('inventory', ?, ?, 'active', 'now', 'now')",
                (brand_id, brand_id),
            )
        with self.assertRaises(ValueError):
            self.service.apply()
        self.assertFalse((Path(self.temp.name) / "backups").exists())

    def test_transaction_rolls_back_when_audit_fails(self):
        product = self.product("BC03B Black", "BC03B")
        with mock.patch("app.services.product_model_backfill.AuditJournal.record", side_effect=RuntimeError("audit")):
            with self.assertRaises(RuntimeError):
                self.service.apply()
        stored = self.catalog.get_product(product["id"])
        self.assertFalse(stored["model"])
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM erp_models").fetchone()[0], 0)

    def test_normalization_merges_case_and_spaces_but_not_hyphens(self):
        self.assertEqual(normalize_model_key("BC 03"), normalize_model_key("bc03"))
        self.assertNotEqual(normalize_model_key("BC-03"), normalize_model_key("BC03"))

    def test_manual_products_reuse_model_entity_and_edit_relinks_it(self):
        first = self.catalog.create_product(
            name="First", article="FIRST", brand="Braun",
            category="Будильники", model="BC 03", stock=0,
        )
        second = self.catalog.create_product(
            name="Second", article="SECOND", brand="Braun",
            category="Будильники", model="bc03", stock=0,
        )
        self.assertEqual(first["model_id"], second["model_id"])
        updated = self.catalog.update_product(second["id"], model="BC04")
        self.assertNotEqual(updated["model_id"], first["model_id"])
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_models WHERE brand_id = ?",
                (first["brand_id"],),
            ).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
