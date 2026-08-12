import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        event = AuditJournal(self.database).list_events(
            entity_type="category", entity_id=second["id"], limit=1,
        )["events"][0]
        self.assertEqual(event["metadata"]["relation_action"], "linked")
        self.assertEqual(event["metadata"]["brand_name_snapshot"], "Seiko")

    def test_category_event_keeps_historical_brand_name_snapshot(self):
        brand = self.catalog.create_brand("Casio")
        category = self.catalog.create_brand_category(brand["id"], "Ремешки")
        self.catalog.rename_brand(brand["id"], "CASIO Europe")

        event = AuditJournal(self.database).list_events(
            entity_type="category", entity_id=category["id"], limit=1,
        )["events"][0]
        self.assertEqual(event["metadata"]["brand_name_snapshot"], "Casio")

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
            len([
                event for event in events
                if event["entity_type"] == "product"
                and event["action"] == "deleted"
            ]),
            0,
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

    def test_without_category_never_creates_relation_and_brand_delete_includes_it(self):
        uncategorized = self.products.create_product(
            name="No category", article="NO-CATEGORY", brand="Casio",
            category="", category_id=0, stock=0,
        )
        normal = self.product("Normal", "NORMAL", "Casio", "Часы", 0)
        overview = self.catalog.get_brand_overview(uncategorized["brand_id"])
        self.assertEqual([item["name"] for item in overview["categories"]], ["Часы"])
        with self.database.connect() as connection:
            zero_links = connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories WHERE category_id = 0"
            ).fetchone()[0]
        self.assertEqual(zero_links, 0)

        self.products.delete_brand_catalog(
            normal["brand_id"], category_id=normal["category_id"]
        )
        self.assertIsNotNone(self.products.get_product(uncategorized["id"]))
        self.products.delete_brand_catalog(uncategorized["brand_id"])
        self.assertIsNone(self.products.get_product(uncategorized["id"]))

    def test_backfill_removes_and_never_recreates_zero_category_relation(self):
        brand = self.catalog.create_brand("Casio")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_categories "
                "(id, brand_id, name, normalized_name, active, created_at, updated_at) "
                "VALUES (0, ?, 'Без категории', 'без категории', 1, 'x', 'x')",
                (brand["id"],),
            )
            connection.execute(
                "INSERT INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, 0, 'x')",
                (brand["id"],),
            )
            connection.execute(
                "DELETE FROM erp_schema_migrations WHERE version = "
                "'2026-08-12-brand-category-relations-v2-no-zero'"
            )

        self.database.initialize()

        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories WHERE category_id = 0"
            ).fetchone()[0], 0)

    def test_product_assignment_ensures_normal_relation_but_not_zero_relation(self):
        product = self.products.create_product(
            name="Assignable", article="ASSIGN", brand="Casio", category="",
            stock=0,
        )
        category = self.catalog.create_brand_category(
            product["brand_id"], "Ремешки"
        )
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM erp_brand_categories WHERE brand_id = ? "
                "AND category_id = ?",
                (product["brand_id"], category["id"]),
            )
        self.products.update_product(
            product["id"], brand_id=product["brand_id"],
            category_id=category["id"],
        )
        with self.database.connect() as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM erp_brand_categories WHERE brand_id = ? "
                "AND category_id = ?",
                (product["brand_id"], category["id"]),
            ).fetchone())
        self.products.update_product(
            product["id"], brand_id=product["brand_id"], category_id=0,
        )
        refreshed = self.products.get_product(product["id"])
        self.assertIsNone(refreshed["category_id"])

    def test_category_global_rename_propagates_and_duplicate_is_blocked(self):
        casio = self.product("Casio Watch", "CW", "Casio", "Часы", 0)
        seiko = self.product("Seiko Watch", "SW", "Seiko", "Часы", 0)
        other = self.catalog.create_brand_category(casio["brand_id"], "Ремешки")

        renamed = self.catalog.rename_category(
            casio["category_id"], "Наручные часы"
        )
        self.assertEqual(renamed["name"], "Наручные часы")
        self.assertEqual(
            self.products.get_product(casio["id"])["excel_category"],
            "Наручные часы",
        )
        self.assertEqual(
            self.products.get_product(seiko["id"])["excel_category"],
            "Наручные часы",
        )
        with self.assertRaises(DuplicateCatalogValueError):
            self.catalog.rename_category(casio["category_id"], other["name"])

    def test_brand_search_matches_normalized_prefix_only(self):
        self.catalog.create_brand("Луч")
        self.catalog.create_brand("Луна")
        self.catalog.create_brand("Полёт")
        self.catalog.create_brand("Слава")
        self.catalog.create_brand("твлапт")

        lower = self.catalog.list_brand_overviews(query="л")
        upper = self.catalog.list_brand_overviews(query="Л")
        trimmed = self.catalog.list_brand_overviews(query="  лу  ")

        self.assertEqual([item["name"] for item in lower], ["Луна", "Луч"])
        self.assertEqual([item["name"] for item in upper], ["Луна", "Луч"])
        self.assertEqual([item["name"] for item in trimmed], ["Луна", "Луч"])

    def test_brand_search_backspace_expands_and_clear_returns_all(self):
        for name in ("Луч", "Луна", "Лонжин", "Полёт"):
            self.catalog.create_brand(name)

        exact = self.catalog.list_brand_overviews(query="луч")
        shorter = self.catalog.list_brand_overviews(query="лу")
        shortest = self.catalog.list_brand_overviews(query="л")
        cleared = self.catalog.list_brand_overviews(query="")

        self.assertEqual([item["name"] for item in exact], ["Луч"])
        self.assertEqual({item["name"] for item in shorter}, {"Луч", "Луна"})
        self.assertEqual(
            {item["name"] for item in shortest}, {"Луч", "Луна", "Лонжин"}
        )
        self.assertEqual(len(cleared), 4)

    def test_bulk_failure_rolls_back_every_product(self):
        first = self.product("First", "FIRST", "Casio", "Часы", 0)
        second = self.product("Second", "SECOND", "Casio", "Часы", 0)
        original = self.products._delete_product_in_transaction
        calls = {"count": 0}

        def fail_on_second(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("forced bulk failure")
            return original(*args, **kwargs)

        with mock.patch.object(
            self.products, "_delete_product_in_transaction",
            side_effect=fail_on_second,
        ):
            with self.assertRaises(RuntimeError):
                self.products.delete_brand_catalog(first["brand_id"])

        self.assertIsNotNone(self.products.get_product(first["id"]))
        self.assertIsNotNone(self.products.get_product(second["id"]))

    def test_delete_retry_is_safe_and_does_not_duplicate_batch_event(self):
        product = self.product("Watch", "WATCH", "Casio", "Часы", 0)
        self.products.delete_brand_catalog(
            product["brand_id"], category_id=product["category_id"]
        )
        with self.assertRaises(ValueError):
            self.products.delete_brand_catalog(
                product["brand_id"], category_id=product["category_id"]
            )
        events = AuditJournal(self.database).list_events(
            entity_type="category", action="deleted", limit=20,
        )["events"]
        self.assertEqual(len(events), 1)

    def test_brands_template_uses_live_search_without_search_button(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app" / "templates" / "warehouse_brands.html"
        ).read_text(encoding="utf-8")
        self.assertIn("setTimeout(loadBrands,200)", source)
        self.assertIn("AbortController", source)
        self.assertIn("requestSequence", source)
        self.assertIn("addEventListener('search'", source)
        self.assertIn("open-upward", source)
        self.assertIn("getBoundingClientRect", source)
        self.assertIn("mobile-erp-navigation", source)
        self.assertIn("Открыть товары", source)
        self.assertIn("data-row-href", source)
        self.assertIn(".metric strong { color:#172033", source)
        self.assertIn("box-shadow:0 0 0 2px", source)
        self.assertIn("outline:none!important", source)
        self.assertNotIn('type="submit">Найти', source)
        self.assertNotIn('class="chip"', source)


if __name__ == "__main__":
    unittest.main()
