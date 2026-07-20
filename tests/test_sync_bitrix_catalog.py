import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from scripts.sync_bitrix_catalog import last_successful_cursor, sync_catalog


def product(external_id, updated_at="2026-07-20T10:00:00+03:00"):
    return {
        "external_source": "bitrix", "external_product_id": str(external_id),
        "external_xml_id": "xml-{}".format(external_id), "external_sku": "",
        "code": "p-{}".format(external_id), "url": "", "name": "Product {}".format(external_id),
        "preview_text": "", "detail_text": "", "preview_text_type": "text",
        "detail_text_type": "text", "active": True, "brand": "", "created_at": None,
        "updated_at": updated_at, "categories": [], "properties": [], "images": [],
        "prices": [{"type_id": "1", "type_code": "BASE", "type_name": "Retail", "role": "base", "value": 100, "currency": "RUB"}],
        "offers": [],
    }


class FakeClient:
    def __init__(self, pages, fail_page=None):
        self.pages = pages
        self.fail_page = fail_page
        self.calls = []

    def get_products_page(self, page, limit, updated_from, include_inactive):
        self.calls.append((page, updated_from, include_inactive))
        if page == self.fail_page:
            raise RuntimeError("private failure detail")
        products = self.pages[page - 1] if page <= len(self.pages) else []
        return {
            "products": products, "has_more": page < len(self.pages),
            "generated_at": "2026-07-20T12:00:00+03:00",
        }


class SyncBitrixCatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        BitrixCatalogImporter(self.database).import_products([product("1")], "full_sync")

    def tearDown(self):
        self.temp.cleanup()

    def test_success_processes_pages_and_advances_cursor(self):
        changed = product("1", "2026-07-20T11:00:00+03:00")
        changed["name"] = "Changed"
        report = sync_catalog(FakeClient([[changed], [product("2")]]), self.database)
        self.assertEqual((report["updated"], report["created"], report["pages_processed"]), (1, 1, 2))
        self.assertEqual(last_successful_cursor(self.database), report["cursor_to"])
        self.assertEqual((report["inventory_operations"], report["moysklad_writes"]), (0, 0))

    def test_failed_partial_run_does_not_advance_cursor_or_log_error_detail(self):
        original_cursor = last_successful_cursor(self.database)
        with self.assertRaises(RuntimeError):
            sync_catalog(
                FakeClient([[product("2", "2026-07-21T11:00:00+03:00")], []], fail_page=2),
                self.database,
            )
        self.assertEqual(last_successful_cursor(self.database), original_cursor)
        with self.database.connect() as connection:
            failed = connection.execute(
                "SELECT * FROM catalog_sync_runs WHERE mode=? ORDER BY id DESC LIMIT 1",
                ("incremental_sync",),
            ).fetchone()
        self.assertEqual((failed["status"], failed["cursor_to"]), ("failed", None))
        self.assertEqual(failed["error_summary"], "RuntimeError")
        self.assertNotIn("private failure detail", failed["error_summary"])

    def test_retry_after_partial_failure_creates_no_duplicates(self):
        with self.assertRaises(RuntimeError):
            sync_catalog(FakeClient([[product("2")], []], fail_page=2), self.database)
        report = sync_catalog(FakeClient([[product("2")]]), self.database)
        self.assertEqual(report["unchanged"], 1)
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM catalog_products").fetchone()[0]
        self.assertEqual(count, 2)

    def test_price_property_and_image_changes_are_detected(self):
        changed_price = product("1", "2026-07-20T11:00:00+03:00")
        changed_price["prices"][0]["value"] = 120
        price_report = sync_catalog(FakeClient([[changed_price]]), self.database)
        self.assertEqual(price_report["updated"], 1)

        changed_property = product("1", "2026-07-20T11:05:00+03:00")
        changed_property["prices"][0]["value"] = 120
        changed_property["properties"] = [{
            "id": "10", "code": "COLOR", "name": "Color",
            "value": "black", "display_value": "Black",
        }]
        property_report = sync_catalog(FakeClient([[changed_property]]), self.database)
        self.assertEqual(property_report["updated"], 1)

        changed_image = dict(changed_property)
        changed_image["updated_at"] = "2026-07-20T11:10:00+03:00"
        changed_image["images"] = [{
            "id": "20", "original_url": "https://example.test/new.jpg", "kind": "gallery",
        }]
        image_report = sync_catalog(FakeClient([[changed_image]]), self.database)
        self.assertEqual(image_report["updated"], 1)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT amount FROM catalog_prices").fetchone()[0], "120")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM catalog_product_property_values").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM catalog_images").fetchone()[0], 1)

    def test_verification_scan_does_not_advance_incremental_cursor(self):
        cursor = last_successful_cursor(self.database)
        client = FakeClient([[product("1")]])
        report = sync_catalog(client, self.database, verify_all=True)
        self.assertFalse(report["cursor_advanced"])
        self.assertTrue(report["verification_full_scan"])
        self.assertEqual(report["cursor_to"], cursor)
        self.assertIsNone(client.calls[0][1])
        self.assertEqual(last_successful_cursor(self.database), cursor)


if __name__ == "__main__":
    unittest.main()
