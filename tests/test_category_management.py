import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.shared_catalog import (
    CatalogReferenceError,
    DuplicateCatalogValueError,
    SharedCatalog,
)


class CategoryManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.database_path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('categories', 'category-sha', 'categories.xlsx', "
                "0, 0, 0, 0, 'active', '2026-08-12T00:00:00+00:00', "
                "'2026-08-12T00:00:00+00:00')"
            )
        self.products = ExcelProductCatalog(self.database)
        self.catalog = SharedCatalog(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def product(self, name, article, brand, category, stock):
        return self.products.create_product(
            name=name, article=article, brand=brand,
            category=category, stock=stock,
        )

    def by_name(self, result):
        return {item["name"]: item for item in result["items"]}

    def test_registry_includes_used_empty_and_system_categories(self):
        first = self.product("A", "A", "Casio", "Часы", 5)
        self.product("B", "B", "Seiko", "Часы", 0)
        empty = self.catalog.create_brand_category(
            first["brand_id"], "test"
        )
        unassigned = self.products.create_product(
            name="No category", article="NONE", brand="Casio",
            category="", category_id=0, stock=3,
        )

        items = self.by_name(self.catalog.list_category_overviews(limit=100))

        self.assertIn("Часы", items)
        self.assertIn("test", items)
        self.assertIn("Без категории", items)
        self.assertEqual(items["Часы"]["status"], "Используется")
        self.assertEqual(items["Часы"]["product_count"], 2)
        self.assertEqual(items["Часы"]["brand_count"], 2)
        self.assertEqual(items["Часы"]["nonzero_count"], 1)
        self.assertEqual(items["Часы"]["stock_total"], 5)
        self.assertEqual(
            [brand["name"] for brand in items["Часы"]["brands"]],
            ["Casio", "Seiko"],
        )
        self.assertEqual(items["test"]["id"], empty["id"])
        self.assertEqual(items["test"]["status"], "Не используется")
        self.assertEqual(items["test"]["brand_count"], 1)
        self.assertEqual(items["test"]["brands"][0]["name"], "Casio")
        self.assertEqual(items["test"]["brands"][0]["product_count"], 0)
        self.assertTrue(items["Без категории"]["system"])
        self.assertEqual(items["Без категории"]["status"], "Системная")
        self.assertEqual(items["Без категории"]["product_count"], 1)
        self.assertEqual(items["Без категории"]["stock_total"], 3)
        self.assertEqual(unassigned["category_id"], None)

    def test_live_search_is_normalized_and_keeps_empty_entities(self):
        brand = self.catalog.create_brand("Casio")
        self.catalog.create_brand_category(brand["id"], "  Ёлочные часы  ")
        self.catalog.create_brand_category(brand["id"], "Часы")
        self.catalog.create_brand_category(brand["id"], "test")

        found = self.catalog.list_category_overviews(query="  ел ", limit=100)
        contains = self.catalog.list_category_overviews(
            query="часы", limit=100
        )
        empty = self.catalog.list_category_overviews(query="test", limit=100)

        self.assertEqual([item["name"] for item in found["items"]], ["Ёлочные часы"])
        self.assertEqual(
            [item["name"] for item in contains["items"]],
            ["Ёлочные часы", "Часы"],
        )
        self.assertEqual(empty["items"][0]["product_count"], 0)

    def test_global_create_uses_one_registry_and_blocks_normalized_duplicate(self):
        self.catalog.create_brand("Owner")
        created = self.catalog.create_global_category("  Наручные   часы  ")

        options = self.catalog.list_category_options(limit=100)
        registry = self.catalog.list_category_overviews(limit=100)["items"]
        self.assertIn(created["id"], [item["id"] for item in options])
        self.assertIn(created["id"], [item["id"] for item in registry])
        with self.assertRaises(DuplicateCatalogValueError):
            self.catalog.create_global_category("наручные часы ")

    def test_global_create_requires_existing_schema_owner_brand(self):
        with self.assertRaises(CatalogReferenceError):
            self.catalog.create_global_category("Часы")

    def test_rename_updates_products_dropdown_and_blocks_conflict(self):
        product = self.product("A", "A", "Casio", "Часы", 2)
        other = self.catalog.create_brand_category(
            product["brand_id"], "Ремешки"
        )
        renamed = self.catalog.rename_category(
            product["category_id"], "Наручные часы"
        )

        self.assertEqual(renamed["name"], "Наручные часы")
        self.assertEqual(
            self.products.get_product(product["id"])["excel_category"],
            "Наручные часы",
        )
        self.assertIn(
            "Наручные часы",
            [item["name"] for item in self.catalog.list_category_options(limit=100)],
        )
        with self.assertRaises(DuplicateCatalogValueError):
            self.catalog.rename_category(other["id"], "наручные часы ")

    def test_empty_category_is_archived_after_backend_reference_plan(self):
        brand = self.catalog.create_brand("Casio")
        category = self.catalog.create_brand_category(brand["id"], "test")
        plan = self.catalog.category_delete_plan(category["id"])

        self.assertFalse(plan["requires_transfer"])
        self.assertEqual(plan["delete_mode"], "archive")
        self.assertEqual(
            set(plan["references"]),
            {"products", "receipt_rows", "sale_items", "receipt_items",
             "brand_relations", "audit_events", "normalization_mappings"},
        )
        self.catalog.move_products_and_archive_category(category["id"])
        self.assertNotIn(
            category["id"],
            [item["id"] for item in self.catalog.list_category_options(limit=100)],
        )

    def test_used_category_requires_target_and_move_preserves_product_data(self):
        product = self.product("A", "A", "Casio", "Часы", 7)
        target = self.catalog.create_brand_category(
            product["brand_id"], "Ремешки"
        )
        before = self.products.get_product(product["id"])
        plan = self.catalog.category_delete_plan(product["category_id"])
        self.assertTrue(plan["requires_transfer"])
        self.assertEqual(plan["active_product_count"], 1)
        with self.assertRaises(CatalogReferenceError):
            self.catalog.move_products_and_archive_category(
                product["category_id"]
            )

        result = self.catalog.move_products_and_archive_category(
            product["category_id"], target["id"]
        )
        after = self.products.get_product(product["id"])
        self.assertEqual(result["active_product_count"], 1)
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["brand_id"], before["brand_id"])
        self.assertEqual(after["stock"], before["stock"])
        self.assertEqual(after["category_id"], target["id"])
        self.assertEqual(after["excel_category"], "Ремешки")
        self.assertEqual(
            self.products.list_products(category_id=target["id"])["total"], 1
        )

    def test_move_to_without_category_uses_null_and_preserves_stock(self):
        product = self.product("A", "A", "Casio", "Часы", 9)
        result = self.catalog.move_products_and_archive_category(
            product["category_id"], 0
        )
        after = self.products.get_product(product["id"])
        self.assertEqual(result["target_category_name"], "Без категории")
        self.assertIsNone(after["category_id"])
        self.assertIsNone(after["excel_category"])
        self.assertEqual(after["stock"], 9)
        self.assertEqual(
            self.products.list_products(category_id=0)["total"], 1
        )

    def test_system_category_cannot_be_renamed_or_deleted(self):
        with self.assertRaises(CatalogReferenceError):
            self.catalog.rename_category(0, "Другое")
        with self.assertRaises(CatalogReferenceError):
            self.catalog.category_delete_plan(0)

    def test_detail_unions_explicit_empty_and_product_brands(self):
        product = self.product("A", "A", "Casio", "Часы", 1)
        stale = self.catalog.create_brand("Legacy")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, ?, 'x')",
                (stale["id"], product["category_id"]),
            )

        detail = self.catalog.get_category_overview(product["category_id"])
        self.assertEqual(detail["brand_count"], 2)
        self.assertEqual(detail["detail_brand_count"], 2)
        self.assertEqual(
            [item["name"] for item in detail["brands"]],
            ["Casio", "Legacy"],
        )
        legacy = next(item for item in detail["brands"] if item["name"] == "Legacy")
        self.assertTrue(legacy["explicit_relation"])
        self.assertEqual(legacy["product_count"], 0)
        self.assertEqual(legacy["nonzero_count"], 0)
        self.assertEqual(legacy["stock_total"], 0)

    def test_product_only_brand_is_visible_without_get_side_effects(self):
        product = self.product("A", "A", "Casio", "Часы", 3)
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM erp_brand_categories WHERE brand_id = ? "
                "AND category_id = ?",
                (product["brand_id"], product["category_id"]),
            )

        registry = self.catalog.list_category_overviews(limit=100)["items"]
        main_row = next(
            item for item in registry if item["id"] == product["category_id"]
        )
        detail = self.catalog.get_category_overview(product["category_id"])

        self.assertEqual(main_row["brand_count"], 1)
        self.assertEqual(detail["brand_count"], 1)
        self.assertEqual(detail["detail_brand_count"], 1)
        self.assertEqual(main_row["brand_count"], detail["brand_count"])
        self.assertEqual(detail["brand_count"], detail["detail_brand_count"])
        self.assertTrue(detail["brands"][0]["product_only"])
        self.assertEqual(detail["brands"][0]["product_count"], 1)
        with self.database.connect() as connection:
            relation_count = connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories "
                "WHERE brand_id = ? AND category_id = ?",
                (product["brand_id"], product["category_id"]),
            ).fetchone()[0]
        self.assertEqual(relation_count, 0)

    def test_detail_reconciles_negative_and_zero_sum_stock(self):
        first = self.product("Positive", "POS", "Casio", "Часы", 5)
        negative = self.product("Negative", "NEG", "Casio", "Часы", 0)
        self.product("Other", "OTH", "Seiko", "Часы", 0)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = -5 WHERE id = ?",
                (negative["id"],),
            )

        detail = self.catalog.get_category_overview(first["category_id"])

        self.assertEqual(detail["product_count"], 3)
        self.assertEqual(detail["nonzero_count"], 2)
        self.assertEqual(detail["stock_total"], 0)
        self.assertEqual(
            sum(item["product_count"] for item in detail["brands"]),
            detail["product_count"],
        )
        self.assertEqual(
            sum(item["nonzero_count"] for item in detail["brands"]),
            detail["nonzero_count"],
        )
        self.assertEqual(
            sum(item["stock_total"] for item in detail["brands"]),
            detail["stock_total"],
        )

    def test_real_duplicate_category_ids_remain_separate_rows(self):
        first_brand = self.catalog.create_brand("First")
        second_brand = self.catalog.create_brand("Second")
        with self.database.transaction() as connection:
            for category_id, brand_id in ((501, first_brand["id"]),
                                          (502, second_brand["id"])):
                connection.execute(
                    "INSERT INTO erp_categories "
                    "(id, brand_id, name, normalized_name, active, "
                    "created_at, updated_at) VALUES (?, ?, 'Часы', 'часы', "
                    "1, 'x', 'x')",
                    (category_id, brand_id),
                )

        result = self.catalog.list_category_overviews(query="час", limit=100)

        self.assertEqual([item["id"] for item in result["items"]], [501, 502])
        self.assertTrue(all(
            item["duplicate_category"] for item in result["items"]
        ))

    def test_global_empty_category_has_no_brand_relation(self):
        self.catalog.create_brand("Owner")
        category = self.catalog.create_global_category("Ремешки")
        detail = self.catalog.get_category_overview(category["id"])

        self.assertEqual(detail["brand_count"], 0)
        self.assertEqual(detail["detail_brand_count"], 0)
        self.assertEqual(detail["product_count"], 0)
        self.assertEqual(detail["brands"], [])
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories WHERE category_id = ?",
                (category["id"],),
            ).fetchone()[0], 0)

    def test_system_bucket_is_first_and_has_product_brand_count(self):
        self.product("Watch", "WATCH", "Casio", "Часы", 1)
        unassigned = self.products.create_product(
            name="No category", article="NONE", brand="Seiko",
            category="", category_id=0, stock=0,
        )
        self.products.create_product(
            name="No brand or category", article="NONE-2", brand="",
            category="", category_id=0, stock=4,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = -2 WHERE id = ?",
                (unassigned["id"],),
            )

        result = self.catalog.list_category_overviews(
            sort_by="products", sort_dir="desc", limit=100
        )

        self.assertEqual(result["items"][0]["id"], 0)
        self.assertEqual(result["items"][0]["brand_count"], 2)
        self.assertEqual(result["items"][0]["detail_brand_count"], 2)
        self.assertEqual(result["items"][0]["product_count"], 2)
        self.assertEqual(result["items"][0]["nonzero_count"], 2)
        self.assertEqual(result["items"][0]["stock_total"], 2)

    def test_pagination_and_numeric_sort_are_server_side(self):
        brand = self.catalog.create_brand("Casio")
        for index in range(6):
            self.catalog.create_brand_category(brand["id"], "C{}".format(index))
        first = self.catalog.list_category_overviews(limit=3, offset=0)
        second = self.catalog.list_category_overviews(limit=3, offset=3)
        self.assertGreaterEqual(first["total"], 7)
        self.assertEqual(len(first["items"]), 3)
        self.assertEqual(len(second["items"]), 3)
        self.assertNotEqual(
            [item["id"] for item in first["items"]],
            [item["id"] for item in second["items"]],
        )


class CategoryManagementWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.database_path)}
        )
        self.environment.start()
        self.database = CatalogDatabase(self.database_path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('category-web', 'category-web-sha', 'category-web.xlsx', "
                "0, 0, 0, 0, 'active', '2026-08-12T00:00:00+00:00', "
                "'2026-08-12T00:00:00+00:00')"
            )
        SharedCatalog(self.database).create_brand("Casio")
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def test_categories_page_api_and_stable_detail_url(self):
        created = self.client.post(
            "/warehouse/categories",
            data={"name": "test"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        self.assertIn("view=categories", created.headers["Location"])
        page = self.client.get("/app/products?view=categories")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Категории".encode(), page.data)
        self.assertIn("Поиск категорий".encode(), page.data)
        payload = self.client.get(
            "/api/v1/category-overviews?q=test"
        ).get_json()
        category_id = payload["data"][0]["id"]
        detail = self.client.get(
            "/app/products?view=categories&category_id={}".format(category_id)
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Открыть все товары".encode(), detail.data)
        self.assertIn("Бренды (0)".encode(), detail.data)

    def test_template_has_canonical_columns_actions_and_live_search(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app" / "templates" / "warehouse_categories.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Переименовать категорию во всей ERP", source)
        self.assertIn("Открыть товары", source)
        self.assertIn("Брендов", source)
        self.assertIn("Дубликат категории", source)
        self.assertNotIn(">Статус<", source)
        self.assertNotIn("Удалить категорию", source)
        self.assertNotIn("delete-plan", source)
        self.assertIn("setTimeout(loadCategories,200)", source)
        self.assertIn("AbortController", source)
        self.assertIn("history.replaceState", source)

    def test_category_get_search_and_detail_do_not_repair_relations(self):
        products = ExcelProductCatalog(self.database)
        product = products.create_product(
            name="Legacy", article="LEGACY", brand="Casio",
            category="Часы", stock=1,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM erp_brand_categories WHERE brand_id = ? "
                "AND category_id = ?",
                (product["brand_id"], product["category_id"]),
            )
            before = {
                "relations": connection.execute(
                    "SELECT COUNT(*) FROM erp_brand_categories"
                ).fetchone()[0],
                "products": connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_products"
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM erp_audit_events"
                ).fetchone()[0],
            }

        self.assertEqual(
            self.client.get("/app/products?view=categories").status_code, 200
        )
        self.assertEqual(
            self.client.get(
                "/app/products?view=categories&category_id={}".format(
                    product["category_id"]
                )
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/category-overviews?q=час").status_code,
            200,
        )

        with self.database.connect() as connection:
            after = {
                "relations": connection.execute(
                    "SELECT COUNT(*) FROM erp_brand_categories"
                ).fetchone()[0],
                "products": connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_products"
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM erp_audit_events"
                ).fetchone()[0],
            }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
