import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.schema_migrations import (
    PRODUCT_COLLECTIONS_SQL, apply_migrations,
)

NOW = "2026-09-09T12:00:00+00:00"


class RemoveProductCollectionsMigrationTest(unittest.TestCase):
    def test_upgrade_removes_membership_and_preserves_all_other_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            self.path = Path(directory) / "catalog.db"
            apply_migrations(self.path, app_commit="fresh")
            self._seed_products()
            with sqlite3.connect(str(self.path)) as connection:
                connection.executescript(PRODUCT_COLLECTIONS_SQL)
                connection.execute(
                    "INSERT INTO erp_collections(name,normalized_name,slug,created_at,updated_at) "
                    "VALUES('Retired','retired','retired',?,?)", (NOW, NOW))
                connection.execute(
                    "INSERT INTO product_collections(product_id,collection_id,created_at) "
                    "VALUES(1,1,?)", (NOW,))
                connection.execute(
                    "DELETE FROM erp_migration_ledger WHERE migration_id=?",
                    ("2026-09-09-remove-product-collections-v1",))
                tables = [row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
                    if row[0] not in ("erp_collections", "product_collections", "erp_migration_ledger", "sqlite_sequence")]
                before = {table: connection.execute('SELECT * FROM "' + table + '"').fetchall()
                          for table in tables}
            apply_migrations(self.path, app_commit="remove")
            apply_migrations(self.path, app_commit="repeat")
            with sqlite3.connect(str(self.path)) as connection:
                self.assertEqual(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name IN ('erp_collections','product_collections')"
                ).fetchall(), [])
                for table in tables:
                    self.assertEqual(before[table], connection.execute(
                        'SELECT * FROM "' + table + '"').fetchall(), table)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name='catalog_excel_products'"
                ).fetchone(), (3,))

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
