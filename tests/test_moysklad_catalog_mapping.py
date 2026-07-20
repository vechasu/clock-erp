import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.moysklad_catalog_mapping import MoySkladCatalogMatcher
from tests.test_bitrix_catalog_importer import product_fixture


class MoySkladCatalogMappingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")

    def tearDown(self):
        self.temp.cleanup()

    def import_product(self, external_id="1", name="Watch", xml_id="xml-1"):
        product = product_fixture(external_id, name, xml_id)
        BitrixCatalogImporter(self.database).import_products([product], "full_sync")
        return product

    def test_external_code_exact_match_and_unique_name_probable(self):
        self.import_product()
        matcher = MoySkladCatalogMatcher(self.database, [
            {"id": "ms-1", "name": "Other", "externalCode": "xml-1"},
        ])
        item = matcher.preview()["items"][0]
        self.assertEqual((item["status"], item["method"]), ("matched", "external_code"))

        name_matcher = MoySkladCatalogMatcher(self.database, [
            {"id": "ms-2", "name": "Watch", "externalCode": "different"},
        ])
        item = name_matcher.preview()["items"][0]
        self.assertEqual((item["status"], item["method"]), ("probable", "unique_name"))

    def test_duplicate_source_name_is_not_matched_by_name(self):
        self.import_product("1", "Same", "xml-1")
        self.import_product("2", "Same", "xml-2")
        matcher = MoySkladCatalogMatcher(self.database, [{"id": "ms-1", "name": "Same"}])
        self.assertEqual({item["status"] for item in matcher.preview()["items"]}, {"not_found"})

    def test_multiple_candidates_and_manual_confirmation_are_local_only(self):
        self.import_product()
        rows = [
            {"id": "ms-1", "name": "One", "externalCode": "xml-1"},
            {"id": "ms-2", "name": "Two", "externalCode": "xml-1"},
        ]
        matcher = MoySkladCatalogMatcher(self.database, rows)
        item = matcher.preview()["items"][0]
        self.assertEqual((item["status"], item["candidate_count"]), ("multiple_candidates", 2))
        matcher.confirm(item["product"]["id"], "ms-1")
        confirmed = matcher.preview()["items"][0]
        self.assertEqual((confirmed["status"], confirmed["method"]), ("confirmed", "manual"))

    def test_xml_attribute_is_used_only_when_definition_exists(self):
        self.import_product()
        definitions = [{"id": "attr-1", "name": "XML_ID", "type": "string"}]
        rows = [{
            "id": "ms-1", "name": "Other", "externalCode": "different",
            "attributes": [{"id": "attr-1", "value": "xml-1"}],
        }]
        item = MoySkladCatalogMatcher(self.database, rows, definitions).preview()["items"][0]
        self.assertEqual((item["status"], item["method"]), ("matched", "xml_id"))


if __name__ == "__main__":
    unittest.main()
