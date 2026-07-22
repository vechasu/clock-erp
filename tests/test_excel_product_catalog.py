import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.services.bitrix_catalog_importer import BitrixCatalogImporter
from app.services.excel_product_catalog import (
    BatchBlockedError,
    ExcelProductBatchService,
    ExcelProductCatalog,
)


def bitrix_product(identity=10, name="Watch X1", brand="Brand"):
    return {
        "external_source": "bitrix",
        "external_product_id": str(identity),
        "external_xml_id": "xml-{}".format(identity),
        "external_sku": "SKU-{}".format(identity),
        "code": "watch-{}".format(identity),
        "url": "https://example.test/bitrix/{}".format(identity),
        "name": name,
        "preview_text": "Short description",
        "detail_text": "Full description",
        "preview_text_type": "text",
        "detail_text_type": "text",
        "active": True,
        "brand": brand,
        "created_at": None,
        "updated_at": "2026-07-20T10:00:00Z",
        "categories": [{
            "id": "2", "name": "Watches", "path": ["Watches"],
            "path_items": [{"id": "2", "name": "Watches"}],
        }],
        "properties": [{
            "id": "1", "code": "COLOR", "name": "Color", "type": "string",
            "value": "black", "display_value": "Black",
        }],
        "images": [
            {
                "id": "large", "original_url": "https://example.test/large.jpg",
                "kind": "detail", "file_size": 20000000, "width": 5000,
                "height": 5000, "is_primary": True,
            },
            {
                "id": "preview", "original_url": "https://example.test/preview.jpg",
                "kind": "preview", "file_size": 20000, "width": 240, "height": 240,
            },
        ],
        "prices": [{
            "type_id": "1", "type_code": "BASE", "type_name": "Retail",
            "role": "base", "value": 100, "currency": "RUB",
        }],
        "offers": [],
    }


def result(row, name, stock, status="not_found", product_id=None,
           alternatives=None, article="", brand="Brand"):
    return {
        "excel_row": row,
        "excel_name": name,
        "excel_brand": brand,
        "excel_article": article,
        "article_quality": "code_like" if article else "empty",
        "category": "Excel category",
        "stock": float(stock),
        "stock_valid": True,
        "cell": "A-{}".format(row),
        "product_id": product_id,
        "match_status": status,
        "match_method": "test",
        "confidence": 0.99 if product_id else 0,
        "alternatives": alternatives or [],
    }


class ExcelProductCatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        BitrixCatalogImporter(self.database).import_products(
            [bitrix_product(), bitrix_product(99, "Bitrix Only", "Other")],
            "full_sync",
        )
        with self.database.connect() as connection:
            self.bitrix_id = connection.execute(
                "SELECT id FROM catalog_products WHERE external_product_id = '10'"
            ).fetchone()[0]
        self.service = ExcelProductBatchService(self.database)
        self.catalog = ExcelProductCatalog(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def initial_results(self):
        candidates = [{
            "product_id": self.bitrix_id, "name": "Watch X1", "brand": "Brand",
            "score": 0.8,
        }]
        return [
            result(2, "Watch X1", 5, "exact", self.bitrix_id, article="SKU-10"),
            result(3, "Unmatched", 2),
            result(4, "Needs Choice", 0, "ambiguous", alternatives=candidates),
        ]

    def apply_initial(self):
        return self.service.apply(
            self.initial_results(), "a" * 64, "source.xlsx", "Импорт",
        )

    def test_excel_defines_composition_and_bitrix_only_is_hidden(self):
        batch = self.apply_initial()
        listing = self.catalog.list_products(per_page=100)
        self.assertEqual((batch["active_cards"], listing["total"]), (3, 3))
        self.assertNotIn("Bitrix Only", {item["display_name"] for item in listing["items"]})
        self.assertEqual(sum(item["stock"] for item in listing["items"]), 7)
        self.assertTrue(all(item["stock_source"] == "excel" for item in listing["items"]))
        self.assertTrue(all(item["moysklad_sync_status"] == "not_linked" for item in listing["items"]))

    def test_exact_is_enriched_with_preview_photo_price_and_properties(self):
        self.apply_initial()
        item = self.catalog.list_products(query="Watch X1")["items"][0]
        self.assertEqual(item["bitrix_thumbnail_url"], "https://example.test/preview.jpg")
        self.assertEqual(item["bitrix_primary_image_url"], "https://example.test/large.jpg")
        self.assertEqual((item["bitrix_price_amount"], item["display_category"]), ("100", "Watches"))
        self.assertEqual(item["properties"][0]["value"], "Black")

    def test_ambiguous_and_not_found_cards_exist_without_bitrix_content(self):
        self.apply_initial()
        ambiguous = self.catalog.list_products(match_status="requires_mapping")["items"]
        not_found = self.catalog.list_products(match_status="not_found")["items"]
        self.assertEqual((len(ambiguous), len(not_found)), (1, 1))
        self.assertIsNone(ambiguous[0]["bitrix_catalog_product_id"])
        self.assertEqual(ambiguous[0]["candidates"][0]["product_id"], self.bitrix_id)
        self.assertIsNone(not_found[0]["bitrix_thumbnail_url"])

    def test_stock_is_adjusted_to_excel_zero_is_valid_and_rollback_is_exact(self):
        first = self.apply_initial()
        second_rows = [
            result(2, "Watch X1", 2, "exact", self.bitrix_id, article="SKU-10"),
            result(3, "Unmatched", 0),
            result(4, "Needs Choice", 0, "ambiguous", alternatives=[{
                "product_id": self.bitrix_id, "name": "Watch X1", "brand": "Brand", "score": 0.8,
            }]),
        ]
        second = self.service.apply(second_rows, "b" * 64, "second.xlsx")
        listing = self.catalog.list_products(per_page=100)
        stocks = {item["excel_name_raw"]: item["stock"] for item in listing["items"]}
        self.assertEqual(stocks, {"Watch X1": 2, "Unmatched": 0, "Needs Choice": 0})
        with self.database.connect() as connection:
            difference = connection.execute(
                "SELECT stock_difference FROM catalog_excel_stock_operations o "
                "JOIN catalog_excel_products p ON p.id = o.product_id "
                "WHERE o.batch_id = ? AND p.excel_name_raw = 'Watch X1'",
                (second["id"],),
            ).fetchone()[0]
        self.assertEqual(difference, -3)
        repeated = self.service.apply(second_rows, "b" * 64, "second.xlsx")
        self.assertTrue(repeated["already_applied"])
        self.assertEqual(repeated["operation_rows"], 3)
        self.service.rollback(second["id"])
        restored = {
            item["excel_name_raw"]: item["stock"]
            for item in self.catalog.list_products(per_page=100)["items"]
        }
        self.assertEqual(restored, {"Watch X1": 5, "Unmatched": 2, "Needs Choice": 0})
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM catalog_excel_batches WHERE id = ?", (first["id"],)
                ).fetchone()[0],
                "active",
            )

    def test_manual_match_not_in_bitrix_unlink_and_undo(self):
        self.apply_initial()
        ambiguous = self.catalog.list_products(match_status="requires_mapping")["items"][0]
        matched = self.catalog.confirm_match(ambiguous["id"], self.bitrix_id)
        self.assertEqual((matched["match_status"], matched["bitrix_price_amount"]), ("manual_match", "100"))
        unlinked = self.catalog.unlink(ambiguous["id"])
        self.assertEqual(unlinked["match_status"], "ambiguous")
        restored = self.catalog.undo_last_match_change(ambiguous["id"])
        self.assertEqual(restored["match_status"], "manual_match")
        no_bitrix = self.catalog.mark_not_in_bitrix(ambiguous["id"])
        self.assertEqual((no_bitrix["match_status"], no_bitrix["bitrix_catalog_product_id"]), ("not_in_bitrix", None))

    def test_duplicate_excel_blocks_the_whole_batch(self):
        rows = [
            result(2, "Same", 1),
            result(3, "Same", 2, status="duplicate_excel"),
        ]
        rows[0]["match_status"] = "duplicate_excel"
        with self.assertRaises(BatchBlockedError):
            self.service.apply(rows, "c" * 64, "duplicate.xlsx")
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM catalog_excel_batches").fetchone()[0], 0)

    def test_all_3313_excel_rows_are_preserved_without_loss(self):
        rows = [
            result(index + 2, "Unique {}".format(index), 1 if index < 836 else 0, brand="Brand {}".format(index))
            for index in range(3313)
        ]
        batch = self.service.apply(rows, "d" * 64, "3313.xlsx")
        self.assertEqual((batch["active_cards"], batch["row_count"]), (3313, 3313))
        self.assertEqual(self.catalog.list_products(per_page=100)["total"], 3313)

    def test_apply_does_not_call_bitrix_or_moysklad_clients(self):
        with mock.patch("app.clients.bitrix_catalog.BitrixCatalogReadOnlyClient") as bitrix, mock.patch("app.clients.moysklad.MoySkladClient") as moysklad:
            self.apply_initial()
        bitrix.assert_not_called()
        moysklad.assert_not_called()

    def test_products_page_has_photo_placeholder_lazy_and_broken_image_fallback(self):
        self.apply_initial()
        from app import web
        web.app.config["TESTING"] = True
        with mock.patch.dict("os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}):
            response = web.app.test_client().get("/products?per_page=100")
        rendered = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("https://example.test/preview.jpg", rendered)
        self.assertIn('loading="lazy"', rendered)
        self.assertIn("handleProductImageError", rendered)
        self.assertIn("Фото отсутствует", rendered)
        self.assertNotIn("Bitrix Only", rendered)


if __name__ == "__main__":
    unittest.main()
