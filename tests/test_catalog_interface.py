import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.catalog_reader import CatalogReader, sanitize_catalog_html


def product_fixture():
    return {
        "external_source": "bitrix", "external_product_id": "10", "external_xml_id": "xml-10",
        "external_sku": "A-10", "code": "watch", "url": "https://example.test/watch",
        "name": "Watch", "preview_text": "Preview", "detail_text": "<p>Safe</p>",
        "preview_text_type": "text", "detail_text_type": "html", "active": True,
        "brand": "Brand", "created_at": None, "updated_at": "2026-07-20T10:00:00Z",
        "categories": [{"id": "2", "name": "Watches", "path": ["Watches"], "path_items": [{"id": "2", "name": "Watches"}]}],
        "properties": [{"id": "1", "code": "COLOR", "name": "Color", "type": "string", "value": "black", "display_value": "Black"}],
        "images": [{"id": "1", "original_url": "https://example.test/a.jpg", "kind": "gallery"}],
        "prices": [{"type_id": "1", "type_code": "BASE", "type_name": "Retail", "role": "base", "value": 100, "currency": "RUB"}],
        "offers": [],
    }


class CatalogInterfaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        BitrixCatalogImporter(self.database).import_products([product_fixture()], "full_sync")

    def tearDown(self):
        self.temp.cleanup()

    def test_reader_returns_list_and_complete_card(self):
        reader = CatalogReader(self.database)
        listing = reader.list_products(query="Wat", activity="active")
        self.assertEqual((listing["total"], listing["items"][0]["price_amount"]), (1, "100"))
        card = reader.get_product(listing["items"][0]["id"])
        self.assertEqual((len(card["properties"]), len(card["images"]), len(card["prices"])), (1, 1, 1))
        self.assertEqual(card["sync_history"][0]["item_status"], "created")

    def test_description_sanitizer_removes_scripts_events_and_unsafe_links(self):
        cleaned = sanitize_catalog_html('<p onclick="x">ok<script>alert(1)</script><a href="javascript:x">bad</a></p>')
        self.assertNotIn("script", cleaned)
        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("javascript", cleaned)
        self.assertIn("<p>okalert(1)<a>bad</a></p>", cleaned)

    def test_catalog_routes_render_and_missing_card_is_404(self):
        from app import web
        web.app.config["TESTING"] = True
        with mock.patch.dict("os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}):
            client = web.app.test_client()
            response = client.get("/catalog")
            self.assertEqual(response.status_code, 200)
            rendered = response.get_data(as_text=True)
            self.assertIn("Watch", rendered)
            self.assertIn('href="/catalog"', rendered)
            self.assertEqual(client.get("/catalog/9999").status_code, 404)

    def test_preview_route_performs_no_database_writes(self):
        from app import web
        fake_page = {"products": [product_fixture()], "total": 1}
        web.app.config["TESTING"] = True
        before = self.path.read_bytes()
        with mock.patch.dict("os.environ", {"CATALOG_DATABASE_PATH": str(self.path), "BITRIX_CATALOG_URL": "https://example.test/api"}), mock.patch.object(web.BitrixCatalogReadOnlyClient, "get_products_page", return_value=fake_page):
            response = web.app.test_client().get("/catalog/import-preview?limit=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("записей выполнено: 0", response.get_data(as_text=True))
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
