import sqlite3
import tempfile
import unittest
from pathlib import Path
from app.catalog_db import CatalogDatabase
from app.schema_migrations import (
    MIGRATIONS,
    ORDER_REFUSAL_MIGRATION_ID,
    PRODUCT_COLLECTIONS_MIGRATION_ID,
    apply_migrations,
)
from app.services.product_collections import ProductCollections


NOW = "2026-08-31T12:00:00+00:00"


class ProductCollectionsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "catalog.db"
        apply_migrations(self.path, app_commit="collections-test")
        self.database = CatalogDatabase(self.path, cache_initialization=False)
        self.service = ProductCollections(self.database)
        self._seed_products()

    def tearDown(self):
        CatalogDatabase._schema_cache.clear()
        self.temporary.cleanup()

    def _seed_products(self):
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches "
                "(id,file_sha256,source_filename,sheet_name,source_type,operation_type,"
                "row_count,total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES('batch','hash','test.xlsx','Импорт','excel','initial_excel_balances',"
                "3,3,2,1,'active',?,?)", (NOW, NOW),
            )
            connection.execute(
                "INSERT INTO erp_brands(name,normalized_name,active,created_at,updated_at) "
                "VALUES('Casio','casio',1,?,?)", (NOW, NOW),
            )
            brand_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute(
                "INSERT INTO erp_categories(brand_id,name,normalized_name,active,created_at,updated_at) "
                "VALUES(?,'Часы','часы',1,?,?)", (brand_id, NOW, NOW),
            )
            category_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            for product_id, name, external_id, active, stock in (
                (1, "Alpha Watch", "101", 1, 2),
                (2, "Beta Watch", "102", 1, 1),
                (3, "Archived Watch", "103", 0, 0),
            ):
                connection.execute(
                    "INSERT INTO catalog_excel_products "
                    "(id,source_key,created_batch_id,current_batch_id,active,raw_excel_json,"
                    "excel_row,excel_name_raw,normalized_name,article_quality,excel_brand,"
                    "excel_category,brand_id,category_id,stock,stock_source,file_sha256,"
                    "match_status,match_method,match_confidence,match_decision,candidates_json,"
                    "bitrix_link_cardinality,shared_bitrix_row_count,bitrix_external_product_id,"
                    "bitrix_gallery_json,bitrix_properties_json,moysklad_sync_status,created_at,updated_at) "
                    "VALUES(?,?,'batch','batch',?,'{}',?,?,?,'missing','Casio','Часы',?,?,?,"
                    "'excel','hash','exact','external_id',1,'matched','[]','one_to_one',1,?,"
                    "'[]','[]','not_linked',?,?)",
                    (
                        product_id, "product:{}".format(product_id), active,
                        product_id, name, name.casefold(), brand_id, category_id,
                        stock, external_id, NOW, NOW,
                    ),
                )

    def system(self, key):
        return next(
            item for item in self.service.list_collections()
            if item["system_key"] == key
        )

    def test_migration_seeds_five_unique_system_collections(self):
        collections = self.service.list_collections()
        self.assertEqual(len(collections), 5)
        self.assertEqual(
            {item["system_key"] for item in collections},
            {"wow-price", "bestsellers", "new", "preorder", "sale"},
        )
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite3.connect(str(self.path)) as connection:
                connection.execute(
                    "INSERT INTO erp_collections(name,normalized_name,slug,system_key,"
                    "created_at,updated_at) VALUES('Дубль','дубль','duplicate','wow-price',?,?)",
                    (NOW, NOW),
                )

    def test_create_edit_and_duplicate_protection(self):
        created = self.service.create_collection("Подарки", False)
        self.assertFalse(created["on_site"])
        updated = self.service.update_collection(created["id"], "Подарки до 5000", True)
        self.assertEqual(updated["name"], "Подарки до 5000")
        self.assertTrue(updated["on_site"])
        with self.assertRaisesRegex(ValueError, "уже существует"):
            self.service.create_collection("Подарки до 5000")

    def test_add_multiple_membership_duplicate_and_remove(self):
        wow = self.system("wow-price")
        new = self.system("new")
        self.service.add_products(wow["id"], [1, 2])
        self.service.add_products(wow["id"], [1])
        self.service.add_products(new["id"], [1])
        self.assertEqual(set(self.service.product_collection_ids(1)), {wow["id"], new["id"]})
        with sqlite3.connect(str(self.path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM product_collections WHERE product_id=1 AND collection_id=?",
                (wow["id"],),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(self.service.remove_products(wow["id"], [1]), 1)
        self.assertEqual(self.service.product_collection_ids(1), [new["id"]])

    def test_archiving_collection_and_product_never_deletes_product(self):
        custom = self.service.create_collection("Витрина")
        self.service.add_products(custom["id"], [1])
        self.service.archive_collection(custom["id"])
        with sqlite3.connect(str(self.path)) as connection:
            product = connection.execute(
                "SELECT excel_name_raw FROM catalog_excel_products WHERE id=1"
            ).fetchone()
            link = connection.execute(
                "SELECT COUNT(*) FROM product_collections WHERE product_id=1"
            ).fetchone()[0]
            connection.execute("UPDATE catalog_excel_products SET active=0 WHERE id=1")
        self.assertEqual(product[0], "Alpha Watch")
        self.assertEqual(link, 1)
        self.assertEqual(self.service.list_products(collection_id=custom["id"]), [])
        self.assertEqual(
            len(self.service.list_products(collection_id=custom["id"], active="all")), 1
        )

    def test_search_brand_category_and_activity_filters(self):
        wow = self.system("wow-price")
        self.service.add_products(wow["id"], [1, 2])
        self.assertEqual(
            [item["id"] for item in self.service.list_products(
                collection_id=wow["id"], query="beta"
            )], [2]
        )
        first = self.service.list_products(collection_id=wow["id"])[0]
        self.assertEqual(len(self.service.list_products(
            collection_id=wow["id"], brand_id=first["brand_id"],
            category_id=first["category_id"], active="1",
        )), 2)

    def test_product_card_replaces_many_to_many_atomically(self):
        wow = self.system("wow-price")
        new = self.system("new")
        from app.services.excel_product_catalog import ExcelProductCatalog
        catalog = ExcelProductCatalog(self.database)
        catalog.update_product(1, name="Alpha Updated", collection_ids=[wow["id"], new["id"]])
        self.assertEqual(set(self.service.product_collection_ids(1)), {wow["id"], new["id"]})
        before = catalog.get_product(1)["excel_name_raw"]
        with self.assertRaisesRegex(ValueError, "не найдена"):
            catalog.update_product(1, name="Must Roll Back", collection_ids=[999999])
        self.assertEqual(catalog.get_product(1)["excel_name_raw"], before)

    def test_dry_run_import_intersections_missing_and_integrity(self):
        products = [
            {
                "external_product_id": "101", "name": "Alpha", "categories": [
                    {"id": "271"}, {"id": "260"}
                ], "properties": [],
            },
            {
                "external_product_id": "102", "name": "Beta", "categories": [],
                "properties": [{"code": "NEW", "value": ["да"], "display_value": ["да"]}],
            },
            {
                "external_product_id": "999", "name": "Missing", "categories": [{"id": "65"}],
                "properties": [],
            },
        ]
        before = self._product_snapshot()
        dry_run = self.service.dry_run(products)
        self.assertEqual(dry_run["expected_links"], 3)
        self.assertEqual(dry_run["missing_count"], 1)
        self.assertEqual(dry_run["overlaps"]["multiple_collection_products"], 1)
        applied = self.service.import_bitrix_memberships(products)
        self.assertEqual(applied["imported_links"], 3)
        self.assertEqual(self._product_snapshot(), before)

    def _product_snapshot(self):
        with sqlite3.connect(str(self.path)) as connection:
            return connection.execute(
                "SELECT id,active,excel_name_raw,excel_brand,excel_category,brand_id,"
                "category_id,stock,bitrix_price_amount,bitrix_gallery_json "
                "FROM catalog_excel_products ORDER BY id"
            ).fetchall()

    def test_existing_database_migrates_without_product_changes(self):
        before = self._product_snapshot()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("DROP TABLE product_collections")
            connection.execute("DROP TABLE erp_collections")
            connection.execute(
                "DELETE FROM erp_migration_ledger WHERE migration_id=?",
                (PRODUCT_COLLECTIONS_MIGRATION_ID,),
            )
        CatalogDatabase._schema_cache.clear()
        apply_migrations(self.path, app_commit="collections-upgrade")
        self.assertEqual(self._product_snapshot(), before)
        self.assertEqual(len(self.service.list_collections()), 5)
        self.assertEqual(MIGRATIONS[-1]["id"], ORDER_REFUSAL_MIGRATION_ID)

    def test_collection_mutation_requires_admin(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "web.py").read_text(
            encoding="utf-8"
        )
        helper = source.split("def require_collection_manage():", 1)[1].split(
            "\n\n", 1
        )[0]
        self.assertIn("current_auth_user", helper)
        self.assertIn('!= "admin"', helper)
        self.assertIn("abort(403", helper)
        self.assertIn("require_csrf_when_authenticated", helper)


if __name__ == "__main__":
    unittest.main()
