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
           alternatives=None, article="", brand="Brand",
           category="Excel category", cell=None):
    return {
        "excel_row": row,
        "excel_name": name,
        "excel_brand": brand,
        "excel_article": article,
        "article_quality": "code_like" if article else "empty",
        "category": category,
        "stock": float(stock),
        "stock_valid": True,
        "cell": "A-{}".format(row) if cell is None else cell,
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
        with self.database.connect() as connection:
            registry_row = connection.execute(
                "SELECT bitrix_xml_id, operation_result FROM catalog_excel_batch_rows "
                "WHERE product_id = ?",
                (item["id"],),
            ).fetchone()
        self.assertEqual(
            (registry_row["bitrix_xml_id"], registry_row["operation_result"]),
            ("xml-10", "adjusted"),
        )

    def test_safe_high_confidence_result_is_enriched_but_keeps_excel_stock(self):
        self.service.apply(
            [result(2, "Brand Watch X1", 7, "high_confidence", self.bitrix_id)],
            "f" * 64,
            "high-confidence.xlsx",
        )
        item = self.catalog.list_products(per_page=10)["items"][0]
        self.assertEqual(
            (item["match_status"], item["bitrix_catalog_product_id"], item["stock"]),
            ("high_confidence", self.bitrix_id, 7),
        )

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
        self.assertEqual(repeated["operation_rows"], 2)
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

    def test_zero_adjustment_is_logged_as_row_without_useless_movement(self):
        batch = self.service.apply(
            [result(2, "Zero Stock", 0)], "e" * 64, "zero.xlsx"
        )
        self.assertEqual(batch["operation_rows"], 0)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT stock_before, stock_after, stock_difference, operation_result "
                "FROM catalog_excel_batch_rows WHERE batch_id = ?",
                (batch["id"],),
            ).fetchone()
        self.assertEqual(
            (row["stock_before"], row["stock_after"], row["stock_difference"], row["operation_result"]),
            (0, 0, 0, "already_at_target"),
        )

    def test_rollback_retains_a_new_card_that_has_manual_audit_history(self):
        batch = self.apply_initial()
        ambiguous = self.catalog.list_products(match_status="requires_mapping")["items"][0]
        self.catalog.confirm_match(ambiguous["id"], self.bitrix_id)
        self.service.rollback(batch["id"])
        with self.database.connect() as connection:
            retained = connection.execute(
                "SELECT active, stock, match_status FROM catalog_excel_products WHERE id = ?",
                (ambiguous["id"],),
            ).fetchone()
            active_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products WHERE active = 1"
            ).fetchone()[0]
        self.assertEqual((retained["active"], retained["stock"], retained["match_status"]), (0, 0, "manual_match"))
        self.assertEqual(active_count, 0)

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

    def test_same_excel_name_with_different_articles_creates_separate_cards(self):
        rows = [
            result(2380, "X7000 GLADIATOR YELLOW", 3, "exact", self.bitrix_id, article="X7000GA-Y"),
            result(2383, "X7000 GLADIATOR YELLOW", 0, "exact", self.bitrix_id, article="X7000GA-Y2"),
        ]
        batch = self.service.apply(rows, "c" * 64, "separate-rows.xlsx")
        items = self.catalog.list_products(per_page=100)["items"]
        self.assertEqual((batch["active_cards"], batch["active_stock"]), (2, 3))
        self.assertEqual([item["excel_row"] for item in items], [2380, 2383])
        self.assertEqual([item["excel_article"] for item in items], ["X7000GA-Y", "X7000GA-Y2"])
        self.assertEqual([item["stock"] for item in items], [3, 0])
        self.assertEqual(len({item["id"] for item in items}), 2)
        self.assertEqual(
            {item["bitrix_link_cardinality"] for item in items}, {"many_to_one"}
        )
        self.assertEqual({item["shared_bitrix_row_count"] for item in items}, {2})
        with self.database.connect() as connection:
            source_keys = [row[0] for row in connection.execute(
                "SELECT source_key FROM catalog_excel_products ORDER BY excel_row"
            ).fetchall()]
        self.assertEqual(source_keys, ["excel-row:00002380", "excel-row:00002383"])

    def test_invalid_excel_row_blocks_the_whole_batch(self):
        rows = [result(2, "Valid", 1), result(3, "Invalid", 2, status="invalid")]
        with self.assertRaises(BatchBlockedError):
            self.service.apply(rows, "9" * 64, "invalid.xlsx")
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM catalog_excel_batches").fetchone()[0], 0)

    def test_manual_links_recalculate_many_to_one_without_combining_stock(self):
        self.service.apply(
            [result(2, "First", 4), result(3, "Second", 7)],
            "8" * 64,
            "manual-many-to-one.xlsx",
        )
        items = self.catalog.list_products(per_page=100)["items"]
        first = self.catalog.confirm_match(items[0]["id"], self.bitrix_id)
        self.assertEqual(first["bitrix_link_cardinality"], "one_to_one")
        second = self.catalog.confirm_match(items[1]["id"], self.bitrix_id)
        first = self.catalog.get_product(items[0]["id"])
        self.assertEqual(
            (first["bitrix_link_cardinality"], second["bitrix_link_cardinality"]),
            ("many_to_one", "many_to_one"),
        )
        self.assertEqual((first["stock"], second["stock"]), (4, 7))
        self.catalog.unlink(second["id"])
        first = self.catalog.get_product(first["id"])
        self.assertEqual(
            (first["bitrix_link_cardinality"], first["shared_bitrix_row_count"]),
            ("one_to_one", 1),
        )

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

    def test_search_covers_excel_bitrix_brand_article_cell_and_xml_id(self):
        self.service.apply(
            [result(
                2, "Excel Alias", 5, "exact", self.bitrix_id,
                article="SKU-10", brand="Excel Brand", cell="CELL-77",
            )],
            "7" * 64,
            "search-fields.xlsx",
        )
        for query in (
            "Excel Alias", "Watch X1", "Excel Brand", "Brand",
            "SKU-10", "CELL-77", "xml-10",
        ):
            with self.subTest(query=query):
                listing = self.catalog.list_products(query=query)
                self.assertEqual(listing["total"], 1)

    def test_filters_hide_zero_category_tree_and_sorting_are_compatible(self):
        self.service.apply(
            [
                result(2, "Zulu", 0, brand="Alpha", category="Watches/Sport", cell="A-1"),
                result(3, "Alpha", 7, brand="Alpha", category="Watches/Classic", cell="A-2"),
                result(4, "Beta", 2, brand="Beta", category="Accessories", cell=""),
            ],
            "6" * 64,
            "filters.xlsx",
        )
        category = self.catalog.list_products(category="Watches", per_page=100)
        self.assertEqual(category["total"], 2)
        filtered = self.catalog.list_products(
            brand="Alpha", category="Watches", hide_zero=True,
            sort_by="stock", sort_dir="desc", per_page=100,
        )
        self.assertEqual([item["excel_name_raw"] for item in filtered["items"]], ["Alpha"])
        self.assertEqual((filtered["stats"]["positions"], filtered["stats"]["total_stock"]), (1, 7))
        no_cell = self.catalog.list_products(cell="Без ячейки", per_page=100)
        self.assertEqual([item["excel_name_raw"] for item in no_cell["items"]], ["Beta"])
        default_order = self.catalog.list_products(per_page=100)["items"]
        self.assertEqual([item["excel_name_raw"] for item in default_order], ["Alpha", "Beta", "Zulu"])

    def test_products_page_is_simple_daily_workflow_without_external_reads(self):
        self.apply_initial()
        from app import web
        web.app.config["TESTING"] = True
        with mock.patch.dict("os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}), \
                mock.patch.object(web, "MoySkladClient") as moysklad, \
                mock.patch.object(web, "BitrixCatalogReadOnlyClient") as bitrix:
            response = web.app.test_client().get(
                "/products?q=Watch&brand=Brand&category=Watches&cell=A-2&hide_zero=1"
            )
        rendered = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        for marker in (
            'id="productsSearchInput"', 'id="productsSearchClear"',
            "searchDebounceMs = 350", "history.pushState", "popstate",
            "vechasuProductsScrollPositionV2", "vechasuProductsColumnWidthsV2",
            'name="brand"', 'name="category"', 'name="cell"', "data-sort-field",
            "Скрыть нулевые", "Оформить приход", "Добавить товар", "Фото", "Цена",
        ):
            self.assertIn(marker, rendered)
        for removed in (
            "Batch registry", "Источник остатков", "Массовое редактирование",
            "Карта склада", "Сопоставление", "XML_ID", "Excel строка",
        ):
            self.assertNotIn(removed, rendered)
        moysklad.assert_not_called()
        bitrix.assert_not_called()

    def test_partial_products_response_keeps_daily_catalog_fields_only(self):
        self.apply_initial()
        from app import web
        web.app.config["TESTING"] = True
        with mock.patch.dict("os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}):
            response = web.app.test_client().get(
                "/products?_partial=1&q=Watch&brand=Brand&sort_by=stock&sort_dir=desc"
            )
        rendered = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<!doctype html>", rendered.lower())
        self.assertIn('data-products-table', rendered)
        self.assertIn("https://example.test/preview.jpg", rendered)
        self.assertIn("Watch X1", rendered)
        self.assertNotIn("Excel строка", rendered)
        self.assertNotIn("xml-10", rendered)
        self.assertNotIn("Сопоставление", rendered)

    def test_product_detail_preserves_safe_return_url(self):
        self.apply_initial()
        product_id = self.catalog.list_products(query="Watch X1")["items"][0]["id"]
        from app import web
        web.app.config["TESTING"] = True
        with mock.patch.dict("os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}):
            client = web.app.test_client()
            response = client.get(
                "/products/{}?return_to=%2Fproducts%3Fbrand%3DBrand%26hide_zero%3D1".format(product_id)
            )
            unsafe = client.get(
                "/products/{}?return_to=https%3A%2F%2Fevil.test".format(product_id)
            )
        self.assertIn('href="/products?brand=Brand&amp;hide_zero=1"', response.get_data(as_text=True))
        self.assertIn('href="/products"', unsafe.get_data(as_text=True))

    def test_operational_add_link_opens_preserved_warehouse_form(self):
        from app import web
        web.app.config["TESTING"] = True
        with mock.patch.object(web, "get_warehouse_items", return_value=[]), \
                mock.patch.object(web, "load_stock_operations", return_value=[]):
            response = web.app.test_client().get("/warehouse?open_add=1")
        rendered = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="add-card" id="warehouseAddCard"', rendered)
        self.assertIn('id="warehouseBulkForm"', rendered)


if __name__ == "__main__":
    unittest.main()
