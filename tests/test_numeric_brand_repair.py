import json
import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.bitrix_erp_product_sync import BitrixERPProductSync
from app.services.numeric_brand_repair import (
    NumericBrandRepair,
    resolve_catalog_brand,
)


def product(identity, name, brand, section_name, section_code):
    category = {
        "id": "section-" + identity,
        "xml_id": "",
        "code": section_code,
        "name": section_name,
        "parent_id": "172",
        "sort": 100,
        "active": True,
        "path": ["Наручные часы", section_name],
        "path_items": [
            {"id": "172", "name": "Наручные часы"},
            {"id": "section-" + identity, "name": section_name},
        ],
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
        "external_product_id": identity,
        "external_xml_id": "xml-" + identity,
        "external_sku": "SKU-" + identity,
        "code": "product-" + identity,
        "url": "https://example.test/" + identity,
        "name": name,
        "preview_text": "",
        "detail_text": "",
        "preview_text_type": "text",
        "detail_text_type": "text",
        "active": True,
        "created_at": "2026-07-20T10:00:00+00:00",
        "updated_at": "2026-07-29T10:00:00+00:00",
        "brand": brand,
        "category": category,
        "categories": [category],
        "properties": [{
            "id": "10",
            "code": "BRAND",
            "name": "Отображать в бренде",
            "type": "string",
            "value": "1",
            "display_value": "1",
            "multiple": False,
            "sort": 10,
        }],
        "images": [{
            "id": "image-" + identity,
            "kind": "preview",
            "original_url": "https://example.test/" + identity + ".jpg",
            "filename": identity + ".jpg",
            "mime_type": "image/jpeg",
            "width": 120,
            "height": 120,
            "file_size": 1000,
            "order": 1,
            "is_primary": True,
        }],
        "prices": [price],
        "offers": [],
        "sale_price": price,
    }


class NumericBrandRepairTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        self.fullspot = product(
            "379",
            "Fullspot Bianco",
            "Fullspot",
            "Fullspot",
            "fullspot",
        )
        self.unresolved = product(
            "236247",
            "Nautis Deacon",
            "Nautis",
            "WOW-Цена",
            "wow-price",
        )
        importer = BitrixCatalogImporter(self.database)
        importer.import_products(
            [self.fullspot, self.unresolved],
            "full_sync",
        )
        BitrixERPProductSync(self.database).apply_products(
            [self.fullspot, self.unresolved]
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_products SET brand = CASE "
                "WHEN external_product_id = '379' THEN '1' ELSE '100' END"
            )
            connection.execute(
                "UPDATE catalog_excel_products SET "
                "excel_brand = CASE WHEN bitrix_external_product_id = '379' "
                "THEN '1' ELSE '100' END, "
                "bitrix_brand = CASE WHEN bitrix_external_product_id = '379' "
                "THEN '1' ELSE '100' END, "
                "stock = CASE WHEN bitrix_external_product_id = '379' "
                "THEN 8 ELSE 0 END"
            )

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        with self.database.connect() as connection:
            return {
                "products": connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_products"
                ).fetchone()[0],
                "stock": connection.execute(
                    "SELECT SUM(stock) FROM catalog_excel_products"
                ).fetchone()[0],
                "images": connection.execute(
                    "SELECT COUNT(*) FROM catalog_images"
                ).fetchone()[0],
                "prices": connection.execute(
                    "SELECT COUNT(*) FROM catalog_prices"
                ).fetchone()[0],
            }

    def test_dry_run_apply_and_repeat_keep_all_product_data(self):
        before = self.snapshot()
        repair = NumericBrandRepair(self.database)
        dry_run = repair.run(apply=False)
        self.assertEqual(dry_run["numeric_values"], ["1", "100"])
        self.assertEqual(dry_run["affected_catalog_products"], 2)
        self.assertEqual(dry_run["affected_erp_products"], 2)
        self.assertEqual(dry_run["numeric_value_counts"], {
            "1": {"products": 1, "stock": 8},
            "100": {"products": 1, "stock": 0},
        })
        self.assertEqual(dry_run["resolved_products"], 1)
        self.assertEqual(
            dry_run["resolved_by_brand"],
            {"Fullspot": 1},
        )
        self.assertEqual(
            [item["name"] for item in dry_run["unresolved_products"]],
            ["Nautis Deacon"],
        )
        self.assertEqual(self.snapshot(), before)

        applied = repair.run(
            apply=True,
            backup_root=Path(self.temp.name) / "backups",
        )
        self.assertTrue(Path(applied["backup_path"]).is_file())
        self.assertEqual(applied["catalog_rows_changed"], 2)
        self.assertEqual(applied["erp_rows_changed"], 2)
        self.assertEqual(applied["audit_rows_created"], 2)
        self.assertEqual(applied["remaining_numeric_catalog"], 0)
        self.assertEqual(applied["remaining_numeric_erp"], 0)
        self.assertEqual(applied["inventory_operations"], 0)
        self.assertEqual(self.snapshot(), before)

        with self.database.connect() as connection:
            catalog = {
                row["external_product_id"]: row["brand"]
                for row in connection.execute(
                    "SELECT external_product_id, brand "
                    "FROM catalog_products"
                )
            }
            erp = {
                row["bitrix_external_product_id"]: row["excel_brand"]
                for row in connection.execute(
                    "SELECT bitrix_external_product_id, excel_brand "
                    "FROM catalog_excel_products"
                )
            }
            payloads = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT normalized_payload_json "
                    "FROM catalog_products"
                )
            ]
        self.assertEqual(
            catalog,
            {"379": "Fullspot", "236247": ""},
        )
        self.assertEqual(erp, catalog)
        self.assertTrue(all(payload["brand"] == "" for payload in payloads))

        repeated = repair.run(
            apply=True,
            backup_root=Path(self.temp.name) / "backups-repeat",
        )
        self.assertEqual(repeated["affected_catalog_products"], 0)
        self.assertEqual(repeated["affected_erp_products"], 0)

    def test_reimport_cannot_restore_numeric_brand(self):
        repair = NumericBrandRepair(self.database)
        repair.run(
            apply=True,
            backup_root=Path(self.temp.name) / "backups",
        )
        incoming_fullspot = dict(self.fullspot, brand="1")
        incoming_unresolved = dict(self.unresolved, brand="100")
        importer = BitrixCatalogImporter(self.database)
        importer.import_products(
            [incoming_fullspot, incoming_unresolved],
            "full_sync",
        )
        BitrixERPProductSync(self.database).apply_products(
            [incoming_fullspot, incoming_unresolved]
        )
        with self.database.connect() as connection:
            catalog_brands = [
                row[0]
                for row in connection.execute(
                    "SELECT brand FROM catalog_products ORDER BY id"
                )
            ]
            erp_brands = [
                row[0]
                for row in connection.execute(
                    "SELECT excel_brand FROM catalog_excel_products "
                    "ORDER BY id"
                )
            ]
        self.assertEqual(catalog_brands, ["Fullspot", ""])
        self.assertEqual(erp_brands, ["Fullspot", ""])
        self.assertFalse(
            NumericBrandRepair(self.database).run(apply=False)[
                "numeric_values"
            ]
        )

    def test_existing_bitrix_section_brand_is_used_as_canonical_name(self):
        brand, reason = resolve_catalog_brand(
            [{
                "id": "49",
                "code": "d-wellington",
                "name": "D. Wellington",
                "path": [
                    {"id": "172", "name": "Наручные часы"},
                    {"id": "49", "name": "D. Wellington"},
                ],
            }],
            [],
            {"49": {"Daniel Wellington"}},
        )
        self.assertEqual(brand, "Daniel Wellington")
        self.assertEqual(reason, "bitrix_section_confirmed_brand")


if __name__ == "__main__":
    unittest.main()
