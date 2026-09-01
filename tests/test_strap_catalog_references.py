import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.excel_product_catalog import ExcelProductCatalog


NOW = "2026-09-01T00:00:00+00:00"


def source_product(external_id, name, brand, path, active=True):
    category_id = "category-{}".format(external_id)
    return {
        "external_source": "bitrix",
        "external_product_id": str(external_id),
        "external_xml_id": "xml-{}".format(external_id),
        "external_sku": "SKU-{}".format(external_id),
        "code": "product-{}".format(external_id),
        "url": "https://example.test/{}/".format(external_id),
        "name": name,
        "preview_text": "",
        "detail_text": "",
        "preview_text_type": "text",
        "detail_text_type": "text",
        "active": active,
        "created_at": NOW,
        "updated_at": NOW,
        "brand": brand,
        "primary_category_id": category_id,
        "categories": [{
            "id": category_id,
            "xml_id": category_id,
            "code": category_id,
            "name": path[-1],
            "parent_id": "parent-{}".format(external_id),
            "sort": 100,
            "active": True,
            "path": path,
            "path_items": [
                {"id": "{}-{}".format(external_id, index), "name": value}
                for index, value in enumerate(path, start=1)
            ],
        }],
        "properties": [],
        "images": [],
        "prices": [],
        "offers": [],
    }


class StrapCatalogReferencesTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}
        )
        self.environment.start()
        self.database = CatalogDatabase(self.path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches "
                "(id,file_sha256,source_filename,row_count,total_stock,"
                "positive_rows,zero_rows,status,created_at,applied_at) VALUES "
                "('references','sha','references.xlsx',0,0,0,0,'active',?,?)",
                (NOW, NOW),
            )
        source_rows = [
            source_product("alpha-strap", "Alpha Strap", "Alpha", ["Каталог", "Товары", "Alpha"]),
            source_product("alpha-bracelet", "Alpha Bracelet", "Alpha", ["Каталог", "Товары", "Alpha"]),
            source_product("alpha-watch", "Alpha Watch", "Alpha", ["Каталог", "Часы", "Alpha"]),
            source_product("watch-only", "Watch Only", "TimeOnly", ["Каталог", "Часы", "TimeOnly"]),
            source_product("zero-strap", "Zero Strap", "Zero", ["Каталог", "Товары", "Zero"]),
            source_product("single-strap", "Single Strap", "Single", ["Каталог", "Товары", "Single"]),
            source_product("inactive-strap", "Inactive Strap", "Archived", ["Каталог", "Товары", "Archived"], active=False),
        ]
        BitrixCatalogImporter(self.database).import_products(source_rows, "full_sync")
        catalog = ExcelProductCatalog(self.database)
        products = [
            ("Alpha Strap", "Alpha", "Ремешки", 0),
            ("Alpha Bracelet", "Alpha", "Браслеты", 0),
            ("Alpha Watch", "Alpha", "Часы", 5),
            ("Watch Only", "TimeOnly", "Часы", 5),
            ("Zero Strap", "Zero", "Ремешки", 0),
            ("Single Strap", "Single", "Ремешки", 1),
        ]
        for name, brand, category, stock in products:
            catalog.create_product(name, brand=brand, category=category, stock=stock)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_catalog_product_id = "
                "(SELECT source.id FROM catalog_products source "
                "WHERE source.name = catalog_excel_products.excel_name_raw)"
            )
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        self.environment.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temporary.cleanup()

    def options(self, kind, **parameters):
        response = self.client.get(
            "/api/v1/catalog/options",
            query_string={
                "type": kind,
                "limit": 200,
                "catalog_scope": "straps",
                **parameters,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["data"]

    def test_only_brands_with_erp_strap_products_are_returned(self):
        brands = {item["name"]: item for item in self.options("brand")}
        self.assertEqual(set(brands), {"Alpha", "Single", "Zero"})
        self.assertEqual(brands["Zero"]["stock_total"], 0)
        available = self.options("brand", available_for_sale="1")
        self.assertEqual([item["name"] for item in available], ["Single"])

    def test_brand_category_product_cascade_uses_erp_classification(self):
        brands = {item["name"]: item for item in self.options("brand")}
        alpha = self.options("category", brand_id=brands["Alpha"]["id"])
        single = self.options("category", brand_id=brands["Single"]["id"])
        self.assertEqual([item["name"] for item in alpha], ["Ремешки"])
        self.assertEqual([item["name"] for item in single], ["Ремешки"])
        products = self.options(
            "product",
            brand_id=brands["Single"]["id"],
            category_id=single[0]["id"],
            product_kind="strap_component",
            in_stock="1",
        )
        self.assertEqual([item["name"] for item in products], ["Single Strap"])

    def test_removed_and_installed_picker_searches_find_real_erp_straps(self):
        removed = self.options(
            "product", q="Alpha Strap", product_kind="strap_component"
        )
        installed = self.options(
            "product", q="Single", product_kind="strap_component", in_stock="1"
        )
        watches = self.options(
            "product", q="Alpha Watch", product_kind="strap_component"
        )
        self.assertEqual([item["name"] for item in removed], ["Alpha Strap"])
        self.assertEqual([item["name"] for item in installed], ["Single Strap"])
        self.assertEqual(watches, [])


if __name__ == "__main__":
    unittest.main()
