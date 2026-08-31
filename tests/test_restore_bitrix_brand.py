import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.schema_migrations import apply_migrations
from app.services.brand_values import normalize_brand
from scripts.restore_bitrix_brand import exact_brand, restore_brand
from tests.test_bitrix_erp_product_sync import FakeClient, product


class RestoreBitrixBrandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = CatalogDatabase(self.root / "catalog.db")
        apply_migrations(self.database.path, app_commit="restore-brand-test")

    def tearDown(self):
        self.temp.cleanup()

    def test_luch_aliases_are_exact_and_canonical(self):
        for value in ("Луч", "ЛУЧ", "луч", "Luch", "LUCH"):
            self.assertEqual(normalize_brand(value), "Луч")
            self.assertTrue(exact_brand(value, "Луч"))
        self.assertFalse(exact_brand("Свет Луч", "Луч"))
        self.assertFalse(exact_brand("Luch Design", "Луч"))

    def test_restore_is_scoped_idempotent_and_reconciles_stock(self):
        luch = product(
            "10", name="Луч 10", brand="Luch", stock=7,
            sku="731959996", image=False,
        )
        other = product(
            "20", name="Other 20", brand="Other", stock=4, image=False
        )
        client = FakeClient([luch, other], page_size=1)

        first = restore_brand(
            client, self.database, apply=True,
            backup_root=self.root / "backups-first",
            image_root=self.root / "images",
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE erp_brands SET name = 'Luch', normalized_name = 'luch' "
                "WHERE name = 'Луч'"
            )
            connection.execute(
                "UPDATE catalog_excel_products SET excel_brand = 'Luch', "
                "excel_article = NULL"
            )
        second = restore_brand(
            client, self.database, apply=True,
            backup_root=self.root / "backups-second",
            image_root=self.root / "images",
        )

        self.assertEqual(first["imported"], 1)
        self.assertEqual(first["stock_mismatch"], 0)
        self.assertEqual(first["other_brands_changed"], 0)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["duplicates_skipped"], 1)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT excel_brand, stock, bitrix_external_product_id, "
                "excel_article "
                "FROM catalog_excel_products"
            ).fetchall()
            brand = connection.execute(
                "SELECT name FROM erp_brands WHERE active = 1"
            ).fetchone()[0]
        self.assertEqual(
            [tuple(row) for row in rows],
            [("Луч", 7, "10", "731959996")],
        )
        self.assertEqual(rows[0]["excel_article"], "731959996")
        self.assertEqual(brand, "Луч")


if __name__ == "__main__":
    unittest.main()
