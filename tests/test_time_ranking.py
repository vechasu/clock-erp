import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)
from app.time_ranking import erp_timestamp, receipt_business_timestamp


def product_result(row, name):
    return {
        "excel_row": row,
        "excel_name": name,
        "excel_brand": "Brand",
        "excel_article": "ART-{}".format(row),
        "article_quality": "code_like",
        "category": "Category",
        "stock": 1.0,
        "stock_valid": True,
        "cell": "A-{}".format(row),
        "product_id": None,
        "match_status": "not_found",
        "match_method": "test",
        "confidence": 0,
        "alternatives": [],
    }


def sale(identity, timestamp, source="tictactoy", article=None):
    return {
        "id": identity,
        "created_at": timestamp,
        "source": web.SALES_SOURCE_LABELS[source],
        "source_key": source,
        "product_name": "Product {}".format(identity),
        "article": article or "ART-{}".format(identity),
        "brand": "Brand",
        "category": "Category",
        "quantity_value": 1,
        "quantity_display": "1",
        "unit_price": 100,
        "unit_price_display": "100 ₽",
        "total_amount": 100,
        "total_amount_display": "100 ₽",
        "delivery_cost": 0,
        "delivery_cost_display": "0 ₽",
        "order_status": "completed",
        "order_status_label": "Выполнен",
        "order_number": "ORDER-{}".format(identity),
        "is_cancelled": False,
        "is_manual": True,
        "sale_type": "manual",
        "net_quantity_value": 1,
        "return_available_quantity": 1,
        "_canonical_timestamp": erp_timestamp(timestamp),
    }


def receipt(identity, receipt_date, created_at, brand="Brand"):
    return {
        "id": identity,
        "number": "REC-{}".format(identity),
        "created_at": created_at,
        "receipt_date": receipt_date,
        "brand": brand,
        "category": "Category",
        "product_name": "Product {}".format(identity),
        "note": "Note",
        "status": "posted",
        "status_label": "Проведён",
        "total_quantity": 1,
        "positions": [],
    }


class ProductTimeRankingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        ExcelProductBatchService(self.database).apply(
            [
                product_result(2, "Morning"),
                product_result(3, "Afternoon"),
                product_result(4, "Evening"),
                product_result(5, "Legacy"),
                product_result(6, "Missing"),
            ],
            "a" * 64,
            "products.xlsx",
        )
        with self.database.transaction() as connection:
            timestamps = {
                "Morning": "2026-08-11T09:00:00+00:00",
                "Afternoon": "2026-08-11T15:00:00+00:00",
                "Evening": "2026-08-11T18:00:00+00:00",
                "Legacy": "2026-08-10",
                "Missing": "bad",
            }
            for name, timestamp in timestamps.items():
                connection.execute(
                    "UPDATE catalog_excel_products SET created_at = ? "
                    "WHERE excel_name_raw = ?",
                    (timestamp, name),
                )
            connection.execute(
                "UPDATE catalog_excel_products SET updated_at = ? "
                "WHERE excel_name_raw = ?",
                ("2026-08-12T23:59:59+00:00", "Morning"),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_products_sort_full_creation_time_before_pagination(self):
        catalog = ExcelProductCatalog(self.database)
        first_page = catalog.list_products(
            sort_by="created_at", sort_dir="desc", page=1, per_page=2,
        )
        all_items = catalog.list_products(
            sort_by="created_at", sort_dir="desc", page=1, per_page=25,
        )
        self.assertEqual(
            [item["excel_name_raw"] for item in first_page["items"]],
            ["Evening", "Afternoon"],
        )
        self.assertEqual(
            [item["excel_name_raw"] for item in all_items["items"]],
            ["Evening", "Afternoon", "Morning", "Legacy", "Missing"],
        )

    def test_warehouse_default_and_explicit_sort_contract(self):
        catalog_result = {
            "items": [], "total": 0, "page": 1,
            "stats": {"total_stock": 0}, "cell_groups": [],
        }
        catalog_service = mock.Mock()
        catalog_service.list_products.return_value = catalog_result
        shared_catalog = mock.Mock()
        shared_catalog.list_brands.return_value = []
        shared_catalog.list_category_options.return_value = []
        taxonomy = {"brands": [], "categories": []}
        for url, expected in (
            ("/warehouse", ("created_at", "desc")),
            ("/warehouse?sort_by=name", ("name", "asc")),
            ("/warehouse?sort_by=bad", ("created_at", "desc")),
        ):
            catalog_service.list_products.reset_mock()
            with self.subTest(url=url), web.app.test_request_context(url), \
                    mock.patch.object(web, "ExcelProductCatalog", return_value=catalog_service), \
                    mock.patch.object(web, "SharedCatalog", return_value=shared_catalog), \
                    mock.patch.object(web, "load_catalog_taxonomy", return_value=taxonomy), \
                    mock.patch.object(web, "get_catalog_stock_history") as stock_history, \
                    mock.patch.object(web, "render_template", return_value="ok") as render:
                web.warehouse_page()
            call = catalog_service.list_products.call_args.kwargs
            self.assertEqual((call["sort_by"], call["sort_dir"]), expected)
            stock_history.assert_not_called()
            self.assertNotIn("stock_operations", render.call_args.kwargs)


class SalesTimeRankingTest(unittest.TestCase):
    def sales_context(self, records, query=""):
        with web.app.test_request_context("/sales" + query), \
                mock.patch.object(web, "get_warehouse_items", return_value=[]), \
                mock.patch.object(web, "build_sales_report_records", return_value=records), \
                mock.patch.object(web, "render_template", side_effect=lambda name, **ctx: ctx):
            return web.sales_page()

    def test_all_sources_use_one_global_full_timestamp_chronology(self):
        records = [
            sale("tt", "2026-08-11T18:45:30", "tictactoy"),
            sale("wb", "2026-08-11T18:40:59", "wildberries"),
            sale("am", "2026-08-11T18:50:01", "amazon"),
        ]
        context = self.sales_context(records, "?source=all")
        self.assertEqual(
            [item["id"] for item in context["sales"]],
            ["am", "tt", "wb"],
        )

    def test_each_source_defaults_newest_first_and_missing_is_last(self):
        for source in ("tictactoy", "wildberries", "amazon"):
            records = [
                sale(source + "-09", "2026-08-11T09:00:01", source),
                sale(source + "-18", "2026-08-11T18:42:59", source),
                sale(source + "-15", "2026-08-11T15:00:30", source),
                sale(source + "-missing", "", source),
            ]
            with self.subTest(source=source):
                context = self.sales_context(
                    records, "?source={}".format(source),
                )
                self.assertEqual(
                    [item["id"] for item in context["sales"]],
                    [source + "-18", source + "-15", source + "-09", source + "-missing"],
                )

    def test_sales_sort_precedes_pagination_and_manual_sort_wins(self):
        records = [
            sale(
                "{:02d}".format(index),
                "2026-08-11T18:{:02d}:{:02d}".format(
                    index // 60, index % 60,
                ),
                ("tictactoy", "wildberries", "amazon")[index % 3],
                article="{:03d}".format(105 - index),
            )
            for index in range(105)
        ]
        for per_page in (25, 50, 100):
            newest = self.sales_context(
                records, "?source=all&per_page={}".format(per_page),
            )
            self.assertEqual(newest["sales"][0]["id"], "104")
            self.assertEqual(len(newest["sales"]), per_page)
        manual = self.sales_context(
            records, "?source=all&sort=article&sort_dir=asc&per_page=25",
        )
        self.assertEqual(
            [item["article"] for item in manual["sales"]],
            sorted(item["article"] for item in records)[:25],
        )

    def test_tictactoy_automatic_sale_prefers_order_business_time(self):
        records = web.build_sales_report_records(
            warehouse_items=[],
            operations=[{
                "id": "automatic",
                "source": "Заказ Битрикс",
                "type": "writeoff",
                "quantity": 1,
                "created_at": "2026-08-11T18:20:00",
                "order_created_at": "2026-08-10T14:35:00",
            }],
            stored_manual_sales=[],
            automatic_overrides={"automatic": {"order_status": "completed"}},
        )
        self.assertEqual(records[0]["created_at"], "2026-08-10T14:35:00")


class ReceiptTimeRankingTest(unittest.TestCase):
    def receipts_context(self, records, query=""):
        with web.app.test_request_context("/receipts" + query), \
                mock.patch.object(web, "api_receipt_records", return_value=records), \
                mock.patch.object(web, "render_template", side_effect=lambda name, **ctx: ctx):
            return web.receipts_page()

    def test_receipts_use_operation_date_and_persisted_time(self):
        records = [
            receipt("09", "2026-08-11", "2026-08-11 09:00"),
            receipt("18", "2026-08-11", "2026-08-11 18:00"),
            receipt("15", "2026-08-11", "2026-08-11 15:00"),
            receipt("old", "2026-08-10", "2026-08-11 23:59"),
            receipt("missing", "bad", "bad"),
        ]
        context = self.receipts_context(records)
        self.assertEqual(
            [item["id"] for item in context["receipts"]],
            ["18", "15", "09", "old", "missing"],
        )

    def test_receipt_filter_keeps_ranking_and_manual_sort_wins(self):
        records = [
            receipt("1", "2026-08-11", "2026-08-11 09:00", "A"),
            receipt("2", "2026-08-11", "2026-08-11 18:00", "A"),
            receipt("3", "2026-08-11", "2026-08-11 15:00", "B"),
        ]
        filtered = self.receipts_context(records, "?receipt_brand=A")
        self.assertEqual([item["id"] for item in filtered["receipts"]], ["2", "1"])
        manual = self.receipts_context(
            records, "?sort=document&sort_dir=asc",
        )
        self.assertEqual([item["id"] for item in manual["receipts"]], ["1", "2", "3"])


class TimePrecisionFallbackTest(unittest.TestCase):
    def test_timezone_equivalent_instants_compare_equal(self):
        self.assertEqual(
            erp_timestamp("2026-08-11T18:42:15+03:00"),
            erp_timestamp("2026-08-11T15:42:15Z"),
        )

    def test_receipt_date_only_uses_known_time_without_changing_date(self):
        ranked = receipt_business_timestamp({
            "receipt_date": "2026-08-10",
            "created_at": "2026-08-11 18:42:59",
        })
        self.assertEqual(ranked, erp_timestamp("2026-08-10 18:42:59"))

    def test_invalid_and_missing_values_are_safe(self):
        self.assertIsNone(erp_timestamp("bad"))
        self.assertIsNone(receipt_business_timestamp({}))

    def test_same_timestamp_uses_descending_stable_id(self):
        records = [
            {"id": "2", "timestamp": 1},
            {"id": "10", "timestamp": 1},
            {"id": "1", "timestamp": 1},
        ]
        ordered = web.sort_erp_records(
            records, "timestamp", "desc", numeric_fields={"timestamp"},
        )
        self.assertEqual([item["id"] for item in ordered], ["10", "2", "1"])


if __name__ == "__main__":
    unittest.main()
