import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.excel_product_catalog import ExcelProductBatchService
from app.services.protected_catalog_brands import protected_product_brand
from scripts.sync_bitrix_products import import_missing_active_products


def product(identity, name="Brand Watch", brand="Brand", sku=None, stock=None):
    category = {
        "id": "category-watches", "name": "Watches", "path": ["Watches"],
        "path_items": [{"id": "category-watches", "name": "Watches"}],
        "active": True, "sort": 100, "xml_id": "", "code": "watches",
        "parent_id": "",
    }
    return {
        "external_source": "bitrix", "external_product_id": str(identity),
        "external_xml_id": "xml-" + str(identity),
        "external_sku": sku if sku is not None else "SKU-" + str(identity),
        "code": "product-" + str(identity), "url": "https://example.test/" + str(identity),
        "name": name, "brand": brand, "active": True, "stock": stock,
        "preview_text": "", "detail_text": "", "preview_text_type": "text",
        "detail_text_type": "text", "created_at": None, "updated_at": None,
        "category": category, "categories": [category], "properties": [],
        "images": [], "prices": [], "offers": [], "sale_price": None,
    }


def excel_result(row, name, brand, stock, article=""):
    return {
        "excel_row": row, "excel_name": name, "excel_name_raw": name,
        "excel_article": article, "excel_brand": brand, "category": "Watches",
        "stock": stock, "stock_valid": True, "cell": "A-1",
        "match_status": "not_found", "match_method": "none", "confidence": 0,
        "alternatives": [],
    }


class FakeClient:
    def __init__(self, products):
        self.products = list(products)

    def get_products_page(self, page, limit, include_inactive=False):
        start = (page - 1) * limit
        rows = self.products[start:start + limit]
        return {
            "products": rows, "total": len(self.products),
            "has_more": start + limit < len(self.products),
        }


class MissingActiveProductImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        ExcelProductBatchService(self.database).apply(
            [
                excel_result(2, "Existing", "Brand", 17, article="SKU-1"),
                excel_result(3, "Protected", "Ziiiro", 8, article="Z-1"),
            ],
            "a" * 64,
            "existing.xlsx",
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_import(self, products, apply=True):
        return import_missing_active_products(
            FakeClient(products),
            self.database,
            apply=apply,
            page_size=2,
            backup_root=Path(self.temp.name) / "backups",
        )

    def test_protected_aliases_ids_and_ambiguous_prefix_are_excluded(self):
        cases = [
            ("ZIIIRO", ""), ("Void", ""), ("OBLVLO", ""),
            ("Projects", ""), ("Aark", ""), ("A.B. Art", ""),
            ("Triwa", ""), ("", "Ziiiro Eclipse"),
        ]
        for index, (brand, name) in enumerate(cases, 1):
            item = product(str(index), name=name or "Watch", brand=brand, stock=1)
            self.assertIsNotNone(protected_product_brand(item))
        enum_item = product("20", brand="Wrong", stock=1)
        enum_item["properties"] = [{
            "id": "86", "enum_id": "465", "display_value": "Wrong"
        }]
        self.assertEqual(protected_product_brand(enum_item), "Oblivio")

    def test_import_is_create_only_idempotent_and_preserves_protected_state(self):
        source_protected = product(
            "9", name="Ziiiro Original", brand="Ziiiro", stock=4
        )
        source_protected["properties"] = [{
            "id": "86", "enum_id": "97", "display_value": "Ziiiro"
        }]
        BitrixCatalogImporter(self.database).import_products(
            [source_protected], "full_sync"
        )
        protected = product("9", name="Ziiiro New", brand="Ziiiro", stock=4)
        protected["properties"] = [{
            "id": "86", "enum_id": "97", "display_value": "Ziiiro"
        }]
        rows = [
            product("1", name="Incoming Existing", sku="SKU-1", stock=99),
            product("2", name="New Product", brand="New Brand", sku="NEW-2", stock=6),
            product("3", name="Unknown Stock", brand="New Brand", sku="NEW-3"),
            protected,
        ]
        first = self.run_import(rows)
        second = self.run_import(rows)
        self.assertEqual((first["created"], second["created"]), (1, 0))
        self.assertEqual(first["excluded_by_brand"]["ZIIIRO"]["count"], 1)
        self.assertEqual(first["excluded_by_brand"]["ZIIIRO"]["source_brand_ids"], ["97"])
        self.assertEqual(first["without_exact_stock"]["count"], 1)
        self.assertTrue(first["protected_unchanged"])
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT excel_name_raw, stock FROM catalog_excel_products "
                "WHERE excel_article = 'SKU-1'"
            ).fetchone()
            protected_row = connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE excel_brand = 'Ziiiro'"
            ).fetchone()
            imported = connection.execute(
                "SELECT stock, bitrix_external_product_id, brand_id, category_id "
                "FROM catalog_excel_products WHERE bitrix_external_product_id = '2'"
            ).fetchone()
            count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products "
                "WHERE bitrix_external_product_id = '2'"
            ).fetchone()[0]
            protected_source_name = connection.execute(
                "SELECT name FROM catalog_products WHERE external_product_id = '9'"
            ).fetchone()[0]
        self.assertEqual(tuple(existing), ("Existing", 17))
        self.assertEqual(protected_row["stock"], 8)
        self.assertEqual((imported["stock"], imported["bitrix_external_product_id"]), (6, "2"))
        self.assertIsNotNone(imported["brand_id"])
        self.assertIsNotNone(imported["category_id"])
        self.assertEqual(count, 1)
        self.assertEqual(protected_source_name, "Ziiiro Original")
        self.assertEqual(first["database_verification"]["quick_check"], "ok")
        self.assertEqual(first["database_verification"]["foreign_key_errors"], 0)


if __name__ == "__main__":
    unittest.main()
