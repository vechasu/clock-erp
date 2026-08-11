import tempfile
import unittest
from unittest import mock
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.bitrix_erp_product_sync import (
    BitrixERPProductSync,
    create_database_backup,
)
from app.services.excel_product_catalog import ExcelProductBatchService, ExcelProductCatalog
from scripts.sync_bitrix_products import sync_bitrix_products


def product(identity="1", name="Brand Watch", brand="Brand", xml_id=None,
            sku=None, price="15990.50", category="Watches", image=True):
    prices = [] if price is None else [{
        "type_id": "1",
        "type_code": "BASE",
        "type_name": "Розничная цена",
        "role": "base",
        "value": float(price),
        "value_text": str(price),
        "currency": "RUB",
        "is_purchase": False,
    }]
    images = [] if not image else [{
        "id": "image-" + identity,
        "kind": "gallery",
        "original_url": "https://www.tictactoy.ru/upload/{}.jpg".format(identity),
        "filename": identity + ".jpg",
        "mime_type": "image/jpeg",
        "width": 1000,
        "height": 1000,
        "file_size": 1000,
        "order": 10,
        "is_primary": True,
    }]
    categories = [] if category is None else [{
        "id": "category-" + category,
        "xml_id": "",
        "code": category.casefold(),
        "name": category,
        "parent_id": "",
        "sort": 100,
        "active": True,
        "path": [category],
        "path_items": [{"id": "category-" + category, "name": category}],
    }]
    return {
        "external_source": "bitrix",
        "external_product_id": str(identity),
        "external_xml_id": xml_id if xml_id is not None else "xml-" + str(identity),
        "external_sku": sku if sku is not None else "SKU-" + str(identity),
        "code": "product-" + str(identity),
        "url": "https://www.tictactoy.ru/catalog/{}/".format(identity),
        "name": name,
        "preview_text": "Preview",
        "detail_text": "Detail",
        "preview_text_type": "text",
        "detail_text_type": "text",
        "active": True,
        "created_at": None,
        "updated_at": None,
        "brand": brand,
        "category": categories[0] if categories else {},
        "categories": categories,
        "properties": [],
        "images": images,
        "prices": prices,
        "offers": [],
        "sale_price": prices[0] if prices else None,
    }


class FakeClient:
    def __init__(self, products, page_size=2):
        self.products = list(products)
        self.page_size = page_size

    def get_products_page(self, page, limit, include_inactive=False):
        start = (page - 1) * self.page_size
        rows = self.products[start:start + self.page_size]
        return {
            "products": rows,
            "total": len(self.products),
            "has_more": start + self.page_size < len(self.products),
        }


def excel_result(row, name, brand, stock, article="", category=""):
    return {
        "excel_row": row,
        "excel_name": name,
        "excel_name_raw": name,
        "excel_article": article,
        "excel_brand": brand,
        "category": category,
        "stock": stock,
        "stock_valid": True,
        "cell": "A-1",
        "match_status": "not_found",
        "match_method": "none",
        "confidence": 0,
        "alternatives": [],
    }


class BitrixERPProductSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def create_excel_cards(self, rows):
        return ExcelProductBatchService(self.database).apply(
            rows,
            "{:064x}".format(len(rows) + sum(row["excel_row"] for row in rows)),
            "existing.xlsx",
        )

    def run_sync(self, products, apply=True):
        return sync_bitrix_products(
            FakeClient(products),
            self.database,
            apply=apply,
            page_size=2,
            backup_root=Path(self.temp.name) / "backups",
        )

    def one_card(self):
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT * FROM catalog_excel_products ORDER BY id LIMIT 1"
            ).fetchone()

    def test_imports_new_product_with_zero_stock_and_is_idempotent(self):
        first = self.run_sync([product()])
        second = self.run_sync([product()])
        self.assertEqual((first["created"], second["created"], second["matched"]), (1, 0, 1))
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products"
            ).fetchone()[0]
            card = connection.execute(
                "SELECT stock, stock_source, bitrix_external_product_id "
                "FROM catalog_excel_products"
            ).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(tuple(card), (0, "bitrix_catalog", "1"))

    def test_deleted_product_is_not_recreated_by_bitrix_sync(self):
        self.run_sync([product()])
        card = self.one_card()
        ExcelProductCatalog(self.database).delete_product(card["id"])

        report = self.run_sync([product()])

        with self.database.connect() as connection:
            cards = connection.execute(
                "SELECT id, active, deleted_at, deleted_stock "
                "FROM catalog_excel_products ORDER BY id"
            ).fetchall()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["active"], 0)
        self.assertTrue(cards[0]["deleted_at"])
        self.assertEqual(cards[0]["deleted_stock"], 0)
        self.assertEqual(report["created"], 0)
        self.assertEqual(report["skipped"], 1)

    def test_matches_by_bitrix_id_then_xml_id(self):
        self.create_excel_cards([
            excel_result(2, "Existing", "Brand", 3),
            excel_result(3, "Second", "Brand", 4),
        ])
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_external_product_id = '1' "
                "WHERE excel_row = 2"
            )
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_xml_id = 'xml-2' "
                "WHERE excel_row = 3"
            )
        report = self.run_sync([
            product("1", name="By ID"),
            product("2", name="By XML"),
        ])
        self.assertEqual((report["created"], report["matched"], report["updated"]), (0, 2, 2))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT excel_row, bitrix_external_product_id, stock "
                "FROM catalog_excel_products ORDER BY excel_row"
            ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [(2, "1", 3), (3, "2", 4)])

    def test_matches_by_sku_and_normalized_brand_name(self):
        self.create_excel_cards([
            excel_result(2, "Other", "Brand", 1, article="SKU-1"),
            excel_result(3, "  Model — Two ", "BRAND", 2),
        ])
        report = self.run_sync([
            product("1", name="Changed", sku="sku-1"),
            product("2", name="Model - Two", brand="Brand", sku=""),
        ])
        self.assertEqual((report["created"], report["matched"], report["ambiguous"]), (0, 2, 0))

    def test_ambiguous_brand_name_is_not_merged(self):
        self.create_excel_cards([
            excel_result(2, "Same", "Brand", 1),
            excel_result(3, " SAME ", "brand", 2),
        ])
        report = self.run_sync([
            product("1", name="Same", brand="Brand", xml_id="", sku=""),
        ])
        self.assertEqual((report["created"], report["ambiguous"], report["matched"]), (0, 1, 0))
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_products"
                ).fetchone()[0],
                2,
            )

    def test_distinct_bitrix_ids_with_same_brand_name_stay_separate(self):
        report = self.run_sync([
            product("1", name="Same", brand="Brand", xml_id="xml-1", sku="SKU-1"),
            product("2", name="Same", brand="Brand", xml_id="xml-2", sku="SKU-2"),
        ])
        self.assertEqual((report["created"], report["ambiguous"]), (2, 0))
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_products"
                ).fetchone()[0],
                2,
            )

    def test_price_brand_category_and_photo_are_saved(self):
        report = self.run_sync([
            product(price="15990.50", brand=" Brand ", category=" Watches "),
        ])
        self.assertEqual(report["created"], 1)
        card = self.one_card()
        self.assertEqual(card["bitrix_price_amount"], "15990.50")
        self.assertEqual(card["excel_brand"], "Brand")
        self.assertEqual(card["excel_category"], "Watches")
        self.assertTrue(card["bitrix_primary_image_url"].endswith("/1.jpg"))

    def test_missing_values_do_not_erase_existing_price_brand_category_or_photo(self):
        self.run_sync([product()])
        report = self.run_sync([
            product(price=None, brand="", category=None, image=False),
        ])
        card = self.one_card()
        self.assertEqual((report["without_price"]["count"], report["without_image"]["count"]), (1, 1))
        self.assertEqual(
            (
                card["bitrix_price_amount"],
                card["excel_brand"],
                card["excel_category"],
                card["bitrix_primary_image_url"].endswith("/1.jpg"),
            ),
            ("15990.50", "Brand", "Watches", True),
        )

    def test_existing_stock_is_never_changed(self):
        self.create_excel_cards([
            excel_result(2, "Brand Watch", "Brand", 17, article="SKU-1"),
        ])
        report = self.run_sync([product()])
        self.assertEqual((report["inventory_operations"], report["stock_changes"]), (0, 0))
        self.assertEqual(self.one_card()["stock"], 17)

    def test_one_bad_product_does_not_stop_following_product(self):
        broken = product("1")
        broken["properties"] = [
            {"id": "same", "code": "A", "name": "A", "value": "1", "display_value": "1"},
            {"id": "same", "code": "A", "name": "A", "value": "2", "display_value": "2"},
        ]
        report = self.run_sync([broken, product("2")])
        self.assertEqual((report["error"], report["created"]), (1, 1))
        with self.database.connect() as connection:
            ids = [
                row[0] for row in connection.execute(
                    "SELECT bitrix_external_product_id FROM catalog_excel_products"
                ).fetchall()
            ]
        self.assertEqual(ids, ["2"])

    def test_dry_run_does_not_mutate_database(self):
        BitrixCatalogImporter(self.database).import_products([product("9")], "full_sync")
        before = self.path.read_bytes()
        report = self.run_sync([product("1")], apply=False)
        after = self.path.read_bytes()
        self.assertEqual((report["mode"], report["writes_performed"], report["created"]), ("dry_run", 0, 1))
        self.assertEqual(before, after)

    def test_backup_falls_back_to_sqlite_cli_without_connection_backup(self):
        BitrixCatalogImporter(self.database).import_products([product("9")], "full_sync")
        source_connection = self.database.connect()

        class ConnectionWithoutBackup:
            def close(self):
                source_connection.close()

        backup_root = Path(self.temp.name) / "python36-backups"

        def emulate_sqlite_backup(arguments, **_kwargs):
            destination_directory = next(backup_root.iterdir())
            destination = destination_directory / self.path.name
            sqlite3_module = __import__("sqlite3")
            source = sqlite3_module.connect(str(self.path))
            target = sqlite3_module.connect(str(destination))
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()

        with mock.patch.object(
            self.database,
            "connect",
            return_value=ConnectionWithoutBackup(),
        ), mock.patch(
            "app.services.bitrix_erp_product_sync.subprocess.run",
            side_effect=emulate_sqlite_backup,
        ) as run:
            backup = create_database_backup(self.database, backup_root)

        self.assertTrue(backup.exists())
        self.assertEqual(run.call_args.args[0][0], "sqlite3")

    def test_catalog_only_card_survives_later_excel_batch_and_is_reused(self):
        self.run_sync([product()])
        catalog_id = self.one_card()["bitrix_catalog_product_id"]
        result = excel_result(2, "Brand Watch", "Brand", 6, article="SKU-1")
        result.update({
            "match_status": "exact",
            "match_method": "xml_id",
            "confidence": 1,
            "product_id": catalog_id,
        })
        ExcelProductBatchService(self.database).apply(
            [result],
            "f" * 64,
            "later.xlsx",
        )
        with self.database.connect() as connection:
            cards = connection.execute(
                "SELECT source_key, stock, stock_source FROM catalog_excel_products"
            ).fetchall()
        self.assertEqual(len(cards), 1)
        self.assertEqual(tuple(cards[0]), ("excel-row:00000002", 6, "excel"))

    def test_missing_quality_fields_are_reported(self):
        report = self.run_sync([
            product(price=None, brand="", category=None, image=False),
        ], apply=False)
        self.assertEqual(
            (
                report["without_price"]["count"],
                report["without_brand"]["count"],
                report["without_category"]["count"],
                report["without_image"]["count"],
            ),
            (1, 1, 1, 1),
        )

    def test_numeric_brand_is_reported_and_not_created(self):
        report = self.run_sync([
            product(brand="100"),
        ], apply=False)
        self.assertEqual(report["invalid_brand"]["count"], 1)
        self.assertEqual(report["without_brand"]["count"], 1)
        self.assertEqual(
            report["invalid_brand"]["products"][0]["id"],
            "1",
        )

    def test_product_listing_includes_zero_stock_catalog_cards(self):
        self.run_sync([product()])
        listing = ExcelProductCatalog(self.database).list_products(per_page=10)
        self.assertEqual((listing["total"], listing["items"][0]["stock"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
