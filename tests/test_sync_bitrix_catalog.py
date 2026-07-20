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
        "prices": [], "offers": [],
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


if __name__ == "__main__":
    unittest.main()
