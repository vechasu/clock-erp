import json
import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter, payload_hash


def product_fixture(external_id="1", name="Watch", xml_id="xml-1"):
    return {
        "external_source": "bitrix",
        "external_product_id": external_id,
        "external_xml_id": xml_id,
        "external_sku": "",
        "code": "watch",
        "url": "https://example.test/watch/",
        "name": name,
        "preview_text": "Preview",
        "detail_text": "<p>Detail</p>",
        "preview_text_type": "text",
        "detail_text_type": "html",
        "active": True,
        "created_at": "2025-01-01T00:00:00+03:00",
        "updated_at": "2026-07-20T00:00:00+03:00",
        "brand": "Brand",
        "primary_category_id": "10",
        "categories": [{
            "id": "10",
            "xml_id": "category-10",
            "code": "watches",
            "name": "Watches",
            "parent_id": "1",
            "sort": 100,
            "active": True,
            "path": ["Catalog", "Watches"],
            "path_items": [{"id": "1", "name": "Catalog"}, {"id": "10", "name": "Watches"}],
        }],
        "properties": [{
            "id": "45", "code": "MATERIAL", "name": "Material", "type": "list",
            "multiple": True, "value": ["STEEL"], "display_value": ["Steel"],
            "enum_id": ["12"], "sort": 500,
        }],
        "images": [{
            "id": "987", "kind": "gallery", "original_url": "https://example.test/watch.jpg",
            "filename": "watch.jpg", "mime_type": "image/jpeg", "width": 1200,
            "height": 1200, "file_size": 1000, "order": 10, "is_primary": True,
        }],
        "prices": [
            {"type_id": "1", "type_code": "BASE", "type_name": "Retail", "role": "base",
             "value": 15990.0, "currency": "RUB", "is_purchase": False},
            {"type_id": "2", "type_code": "COST", "type_name": "Закупочная", "role": "purchase",
             "value": 5000.0, "currency": "RUB", "is_purchase": True},
        ],
        "offers": [],
        "available_quantity": 99,
        "reserve": 10,
    }


class BitrixCatalogImporterTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "catalog.db"
        self.database = CatalogDatabase(self.database_path)
        self.importer = BitrixCatalogImporter(self.database)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def row(self, query, parameters=()):
        with self.database.connect() as connection:
            return connection.execute(query, parameters).fetchone()

    def test_preview_is_default_and_does_not_create_database(self):
        result = self.importer.import_products([product_fixture()])
        self.assertEqual(result["mode"], "preview")
        self.assertEqual(result["writes_performed"], 0)
        self.assertEqual(result["created"], 1)
        self.assertFalse(self.database_path.exists())

    def test_full_sync_saves_all_content_atomically_and_excludes_stock_and_purchase_price(self):
        result = self.importer.import_products([product_fixture()], "full_sync")
        self.assertEqual(result["created"], 1)
        product = self.row("SELECT * FROM catalog_products")
        self.assertEqual((product["name"], product["brand"], product["active"]), ("Watch", "Brand", 1))
        self.assertNotIn("available_quantity", product.keys())
        self.assertNotIn("reserve", product.keys())
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_categories")["count"], 2)
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_product_property_values")["count"], 1)
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_images")["count"], 1)
        price = self.row("SELECT * FROM catalog_prices")
        self.assertEqual((price["amount"], price["currency"], price["price_type"]), ("15990.0", "RUB", "BASE"))
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_prices")["count"], 1)

    def test_repeated_import_is_unchanged_and_creates_no_duplicates(self):
        self.importer.import_products([product_fixture()], "full_sync")
        result = self.importer.import_products([product_fixture()], "full_sync")
        self.assertEqual((result["created"], result["updated"], result["unchanged"]), (0, 0, 1))
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_products")["count"], 1)
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_images")["count"], 1)

    def test_full_sync_updates_card(self):
        self.importer.import_products([product_fixture()], "full_sync")
        changed = product_fixture()
        changed["name"] = "Updated Watch"
        changed["detail_text"] = "Updated detail"
        result = self.importer.import_products([changed], "full_sync")
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.row("SELECT name FROM catalog_products")["name"], "Updated Watch")

    def test_transaction_rolls_back_all_products_on_failure(self):
        def failure_hook(index, product):
            if index == 1:
                raise RuntimeError("injected failure")

        importer = BitrixCatalogImporter(self.database, failure_hook=failure_hook)
        with self.assertRaises(RuntimeError):
            importer.import_products([product_fixture("1"), product_fixture("2", "Second", "xml-2")], "full_sync")
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_products")["count"], 0)
        failed_run = self.row("SELECT * FROM catalog_sync_runs")
        self.assertEqual((failed_run["status"], failed_run["errors_count"]), ("failed", 1))
        self.assertEqual(failed_run["error_summary"], "RuntimeError")
        self.assertNotIn("injected failure", failed_run["error_summary"])

    def test_ambiguous_exact_name_requires_mapping(self):
        first = product_fixture("1", "Same", "xml-1")
        second = product_fixture("2", "Same", "xml-2")
        first["external_source"] = "legacy"
        second["external_source"] = "legacy"
        self.importer.import_products([first], "full_sync")
        with self.database.transaction() as connection:
            self.importer._insert_product(
                connection, second, payload_hash(second), "full_sync"
            )
        incoming = product_fixture("3", "  SAME  ", "")
        result = self.importer.import_products([incoming], "full_sync")
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(result["items"][0]["status"], "requires_mapping")
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_products")["count"], 2)

    def test_distinct_bitrix_ids_with_same_fallback_keys_remain_distinct(self):
        first = product_fixture("1", "Same", "shared-xml")
        second = product_fixture("2", "Same", "shared-xml")
        result = self.importer.import_products([first, second], "full_sync")
        self.assertEqual((result["created"], result["conflicts"]), (2, 0))
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_products")["count"], 2)

    def test_create_only_never_updates_existing_product(self):
        self.importer.import_products([product_fixture()], "full_sync")
        changed = product_fixture()
        changed["name"] = "Should not replace"
        result = self.importer.import_products([changed], "create_only")
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(self.row("SELECT name FROM catalog_products")["name"], "Watch")

    def test_fill_empty_only_fills_empty_scalar_fields(self):
        original = product_fixture()
        original["brand"] = ""
        self.importer.import_products([original], "full_sync")
        changed = product_fixture()
        changed["name"] = "Do not replace"
        changed["brand"] = "Filled Brand"
        self.importer.import_products([changed], "fill_empty")
        row = self.row("SELECT name, brand FROM catalog_products")
        self.assertEqual((row["name"], row["brand"]), ("Watch", "Filled Brand"))

    def test_update_content_does_not_change_activity_or_prices(self):
        self.importer.import_products([product_fixture()], "full_sync")
        changed = product_fixture()
        changed["detail_text"] = "New content"
        changed["active"] = False
        changed["prices"][0]["value"] = 999.0
        self.importer.import_products([changed], "update_content")
        product = self.row("SELECT detail_text, active FROM catalog_products")
        price = self.row("SELECT amount FROM catalog_prices")
        self.assertEqual((product["detail_text"], product["active"]), ("New content", 1))
        self.assertEqual(price["amount"], "15990.0")

    def test_normalized_payload_contains_no_inventory_side_effect(self):
        self.importer.import_products([product_fixture()], "full_sync")
        payload = json.loads(self.row("SELECT normalized_payload_json FROM catalog_products")[0])
        self.assertEqual(payload["available_quantity"], 99)
        table_names = set(self.database.table_names())
        self.assertFalse(any("stock" in name or "operation" in name for name in table_names))

    def test_offer_properties_images_and_prices_are_saved(self):
        product = product_fixture()
        offer = product_fixture("offer-1", "Blue offer", "offer-xml")
        offer["external_offer_id"] = "offer-1"
        offer["external_sku"] = "BLUE-1"
        product["offers"] = [offer]
        self.importer.import_products([product], "full_sync")
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_offers")["count"], 1)
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_offer_property_values")["count"], 1)
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_images WHERE offer_id IS NOT NULL")["count"], 1)
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM catalog_prices WHERE offer_id IS NOT NULL")["count"], 1)


if __name__ == "__main__":
    unittest.main()
