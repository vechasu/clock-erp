import json
import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.catalog_data_quality import is_empty_property_row
from scripts.cleanup_empty_catalog_properties import cleanup
from tests.test_bitrix_catalog_importer import product_fixture


class CatalogDataQualityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_detection_preserves_zero_false_and_string_zero(self):
        self.assertTrue(is_empty_property_row("null", "[]"))
        self.assertTrue(is_empty_property_row(json.dumps(""), "null"))
        for value in (0, False, "0"):
            encoded = json.dumps(value)
            self.assertFalse(is_empty_property_row(encoded, encoded))

    def test_importer_skips_empty_properties_and_preserves_valid_falsey_values(self):
        product = product_fixture()
        product["properties"] = [
            {"id": "1", "code": "EMPTY", "name": "Empty", "value": None, "display_value": []},
            {"id": "2", "code": "ZERO", "name": "Zero", "value": 0, "display_value": 0},
            {"id": "3", "code": "FALSE", "name": "False", "value": False, "display_value": False},
            {"id": "4", "code": "STRING_ZERO", "name": "String zero", "value": "0", "display_value": "0"},
        ]
        BitrixCatalogImporter(self.database).import_products([product], "full_sync")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT value_json FROM catalog_product_property_values ORDER BY id"
            ).fetchall()
        self.assertEqual([json.loads(row[0]) for row in rows], [0, False, "0"])

    def test_cleanup_preview_does_not_write_and_apply_creates_verified_backup(self):
        product = product_fixture()
        BitrixCatalogImporter(self.database).import_products([product], "full_sync")
        with self.database.transaction() as connection:
            property_id = connection.execute("SELECT id FROM catalog_properties LIMIT 1").fetchone()[0]
            connection.execute(
                "UPDATE catalog_product_property_values SET value_json=?, display_value_json=? "
                "WHERE property_id=?",
                ("null", "[]", property_id),
            )
        before = self.path.read_bytes()
        preview = cleanup(self.database, apply=False)
        self.assertEqual((preview["rows_to_delete"], preview["rows_deleted"]), (1, 0))
        self.assertEqual(self.path.read_bytes(), before)
        backup_root = Path(self.temp.name) / "backups"
        applied = cleanup(self.database, apply=True, backup_root=backup_root)
        self.assertEqual((applied["rows_deleted"], applied["remaining_empty_rows"]), (1, 0))
        self.assertEqual((applied["quick_check"], applied["foreign_key_errors"]), ("ok", 0))
        self.assertTrue(Path(applied["backup_path"]).exists())


if __name__ == "__main__":
    unittest.main()
