import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.bitrix_erp_product_sync import BitrixERPProductSync
from app.services.product_classification import (
    ProductClassificationRepair,
    classify_product,
)


def bitrix_product():
    properties = [
        {
            "id": "brand",
            "code": "BRAND_MODEL",
            "name": "Марка часов",
            "type": "string",
            "value": "Casio",
            "display_value": "Casio",
            "multiple": False,
            "sort": 10,
        },
        {
            "id": "glass",
            "code": "WATCH_GLASS",
            "name": "Стекло часов",
            "type": "string",
            "value": "Минеральное",
            "display_value": "Минеральное",
            "multiple": False,
            "sort": 20,
        },
    ]
    category = {
        "id": "brand-section",
        "xml_id": "",
        "code": "casio",
        "name": "Casio",
        "parent_id": "",
        "sort": 100,
        "active": True,
        "path": ["Casio"],
        "path_items": [{"id": "brand-section", "name": "Casio"}],
    }
    image = {
        "id": "image-1",
        "kind": "preview",
        "original_url": "https://example.test/thumb.jpg",
        "filename": "thumb.jpg",
        "mime_type": "image/jpeg",
        "width": 120,
        "height": 120,
        "file_size": 1000,
        "order": 1,
        "is_primary": True,
    }
    price = {
        "type_id": "1",
        "type_code": "BASE",
        "type_name": "Розничная",
        "role": "base",
        "value": 1000.0,
        "value_text": "1000",
        "currency": "RUB",
        "is_purchase": False,
    }
    return {
        "external_source": "bitrix",
        "external_product_id": "100",
        "external_xml_id": "xml-100",
        "external_sku": "CAS-100",
        "code": "cas-100",
        "url": "https://example.test/casio",
        "name": "Casio Test",
        "preview_text": "",
        "detail_text": "",
        "preview_text_type": "text",
        "detail_text_type": "text",
        "active": True,
        "created_at": None,
        "updated_at": None,
        "brand": "Casio",
        "category": category,
        "categories": [category],
        "properties": properties,
        "images": [image],
        "prices": [price],
        "offers": [],
        "sale_price": price,
    }


class ProductClassificationTest(unittest.TestCase):
    def test_explicit_brand_property_and_watch_properties_are_separate(self):
        decision = classify_product(bitrix_product())
        self.assertEqual(decision["brand"], "Casio")
        self.assertEqual(decision["category"], "Наручные часы")
        self.assertNotEqual(decision["brand"], decision["category"])

    def test_brand_section_is_used_only_from_exact_known_brand_dictionary(self):
        product = bitrix_product()
        product["brand"] = ""
        product["properties"] = []
        decision = classify_product(product, known_brands={"casio": "Casio"})
        self.assertEqual(decision["brand"], "Casio")
        self.assertEqual(decision["category"], "")
        self.assertTrue(decision["ambiguous"])

    def test_top_level_type_section_wins_over_brand_section(self):
        product = bitrix_product()
        product["categories"].append({
            "name": "БУДИЛЬНИКИ",
            "path": ["БУДИЛЬНИКИ"],
        })
        decision = classify_product(product)
        self.assertEqual(decision["category"], "Будильники")

    def test_ambiguous_product_is_not_guessed(self):
        product = bitrix_product()
        product["properties"] = []
        decision = classify_product(product)
        self.assertEqual(decision["category"], "")
        self.assertTrue(decision["ambiguous"])


class ProductClassificationRepairTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        product = bitrix_product()
        BitrixCatalogImporter(self.database).import_products([product], "full_sync")
        BitrixERPProductSync(self.database).apply_products([product])
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 7, "
                "excel_category = 'Casio', bitrix_category = 'Casio'"
            )

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT stock, bitrix_price_amount, "
                "bitrix_primary_image_url, excel_brand, excel_category "
                "FROM catalog_excel_products"
            ).fetchone()
        return tuple(row)

    def test_dry_run_apply_backup_and_repeat_are_safe(self):
        before = self.snapshot()
        dry_run = ProductClassificationRepair(self.database).run(apply=False)
        self.assertEqual(dry_run["checked"], 1)
        self.assertEqual(dry_run["categories_changed"], 1)
        self.assertEqual(self.snapshot(), before)

        applied = ProductClassificationRepair(self.database).run(
            apply=True,
            backup_root=Path(self.temp.name) / "backups",
        )
        after = self.snapshot()
        self.assertTrue(Path(applied["backup_path"]).is_file())
        self.assertEqual(applied["updated"], 1)
        self.assertEqual(after[:3], before[:3])
        self.assertEqual(after[3:], ("Casio", "Наручные часы"))
        self.assertEqual(applied["inventory_operations"], 0)

        repeated = ProductClassificationRepair(self.database).run(
            apply=True,
            backup_root=Path(self.temp.name) / "backups",
        )
        self.assertEqual(repeated["updated"], 0)
        self.assertEqual(self.snapshot(), after)


if __name__ == "__main__":
    unittest.main()
