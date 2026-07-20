import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from scripts.import_bitrix_catalog import belongs_to_category, import_catalog


def product(external_id, active=True, category_id="10"):
    return {
        "external_source": "bitrix", "external_product_id": str(external_id),
        "external_xml_id": "xml-{}".format(external_id), "external_sku": "",
        "code": "p-{}".format(external_id), "url": "", "name": "Product {}".format(external_id),
        "preview_text": "", "detail_text": "", "preview_text_type": "text",
        "detail_text_type": "text", "active": active, "brand": "", "created_at": None,
        "updated_at": None, "categories": [{"id": category_id, "name": "Category", "path_items": [
            {"id": "1", "name": "Root"}, {"id": category_id, "name": "Category"}
        ]}], "properties": [], "images": [], "prices": [], "offers": [],
    }


class FakeClient:
    def __init__(self, products, page_size=2):
        self.products = products
        self.page_size = page_size

    def get_products_page(self, page, limit, include_inactive=False):
        rows = self.products if include_inactive else [item for item in self.products if item["active"]]
        start = (page - 1) * self.page_size
        selected = rows[start:start + self.page_size]
        return {
            "products": selected, "total": len(rows),
            "has_more": start + self.page_size < len(rows),
        }


class CatalogImportCommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        self.products = [product("1"), product("2", category_id="20"), product("3", False)]

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_is_default_behavior_and_does_not_create_database(self):
        report = import_catalog(FakeClient(self.products), self.database, max_items=2)
        self.assertEqual((report["mode"], report["created"], report["writes_performed"]), ("preview", 2, 0))
        self.assertFalse(self.path.exists())

    def test_full_sync_is_paginated_idempotent_and_has_no_inventory_writes(self):
        first = import_catalog(FakeClient(self.products), self.database, mode="full_sync")
        second = import_catalog(FakeClient(self.products), self.database, mode="full_sync")
        self.assertEqual((first["created"], second["unchanged"]), (2, 2))
        self.assertEqual(first["database"]["duplicate_external_ids"], 0)
        self.assertEqual((first["moysklad_writes"], first["inventory_operations"]), (0, 0))

    def test_category_and_inactive_filters_are_applied(self):
        category_report = import_catalog(
            FakeClient(self.products), self.database, mode="full_sync", category_id="20"
        )
        inactive_report = import_catalog(
            FakeClient(self.products), self.database, mode="full_sync", inactive_only=True
        )
        self.assertEqual((category_report["selected_products"], inactive_report["selected_products"]), (1, 1))
        self.assertEqual(inactive_report["database"]["inactive"], 1)

    def test_parent_category_matches_path(self):
        self.assertTrue(belongs_to_category(self.products[0], "1"))
        self.assertFalse(belongs_to_category(self.products[0], "99"))


if __name__ == "__main__":
    unittest.main()
