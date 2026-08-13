import unittest
from unittest import mock

from app import web


def sale(
        sale_id, *, brand_id="b1", brand="Casio",
        category_id="c1", category="Часы", product_id="p1",
        product="G-Shock", article="GA-2100", quantity=1,
        amount=100, source="tictactoy"):
    return {
        "id": sale_id,
        "created_at": "2026-08-10",
        "sale_type": "manual",
        "source": web.get_sales_source_label(source),
        "source_key": source,
        "brand_id": brand_id,
        "brand": brand,
        "category_id": category_id,
        "category": category,
        "product_id": product_id,
        "product_name": product,
        "article": article,
        "order_number": "ORDER-{}".format(sale_id),
        "order_status": "shipped",
        "is_cancelled": False,
        "quantity_value": quantity,
        "net_quantity_value": quantity,
        "quantity_display": str(quantity),
        "gross_total_amount": amount,
        "returned_amount": 0,
        "returned_quantity": 0,
        "total_amount": amount,
        "unit_price": amount,
        "unit_price_display": str(amount),
        "total_amount_display": str(amount),
    }


def receipt(
        receipt_id, number, note, *, brand_id="b1", brand="Casio",
        category_id="c1", category="Часы", product_id="p1",
        product="G-Shock", article="GA-2100", quantity=1,
        receipt_date="2026-08-10", positions=None):
    position = {
        "brand_id": brand_id,
        "brand": brand,
        "category_id": category_id,
        "category": category,
        "product_id": product_id,
        "product_name": product,
        "article": article,
        "quantity": quantity,
    }
    positions = positions or [position]
    return {
        "id": receipt_id,
        "number": number,
        "note": note,
        "receipt_date": receipt_date,
        "created_at": "2026-08-10 12:00:00",
        "status": "posted",
        "status_label": "Проведён",
        "total_quantity": sum(item["quantity"] for item in positions),
        "total_amount": 0,
        "brand_id": brand_id,
        "brand": brand,
        "category_id": category_id,
        "category": category,
        "product_id": product_id,
        "product_name": product,
        "positions": positions,
    }


class SalesReportCatalogFiltersTest(unittest.TestCase):
    def test_report_rows_and_kpis_use_same_filtered_sales(self):
        sales = [
            sale("selected", quantity=2, amount=240),
            sale(
                "other", brand_id="b2", brand="Seiko",
                category_id="c2", category="Будильники",
                product_id="p2", product="Clock", amount=900,
                source="amazon",
            ),
        ]
        with web.app.test_request_context(
            "/sales/report?source=all&brand_id=b1&category_id=c1&product_id=p1"
        ), mock.patch.object(
            web, "build_sales_report_records", return_value=sales
        ), mock.patch.object(
            web.SharedCatalog,
            "category_compatibility_groups",
            return_value=[],
        ):
            context = web.build_sales_report_context()

        self.assertEqual([item["id"] for item in context["sales"]], ["selected"])
        self.assertEqual(context["total_records"], 1)
        self.assertEqual(context["total_sales"], 1)
        self.assertEqual(context["total_quantity"], "2")
        self.assertEqual(context["gross_revenue"], 240)
        self.assertEqual(context["total_revenue"], 240)

    def test_report_template_has_cascade_search_apply_and_reset(self):
        source = (web.PROJECT_ROOT / "app/templates/sales_report.html").read_text(
            encoding="utf-8"
        )
        for marker in (
            'id="reportBrandFilter"',
            'id="reportCategoryFilter"',
            'id="reportProductSearch"',
            "Поиск товара по названию или артикулу...",
            "Ничего не найдено",
            'id="reportStatusFilter"',
            'id="reportSourceFilter"',
            'id="reportFilterReset"',
            "cascade({...applied})",
        ):
            self.assertIn(marker, source)


class ReceiptCatalogFiltersTest(unittest.TestCase):
    def setUp(self):
        self.receipts = [
            receipt("one", "RCPT-001", "Срочная поставка"),
            receipt(
                "two", "RCPT-002", "Обычная поставка",
                product_id="p2", product="G-Shock", article="GA-2200",
            ),
            receipt(
                "three", "RCPT-003", "Ремешки",
                brand_id="b2", brand="Vechasu",
                category_id="c2", category="Аксессуары",
                product_id="p3", product="Ремешок", article="STRAP-1",
            ),
        ]

    def route_context(self, query):
        with web.app.test_request_context("/receipts" + query), mock.patch.object(
            web, "api_receipt_records", return_value=self.receipts
        ), mock.patch.object(
            web, "attach_receipt_product_thumbnails", side_effect=lambda rows: rows
        ), mock.patch.object(
            web, "render_template", side_effect=lambda name, **context: context
        ):
            return web.receipts_page()

    def test_catalog_keeps_duplicate_names_by_product_id_and_sku(self):
        catalog = web.build_receipt_filter_catalog(self.receipts)
        duplicates = [item for item in catalog if item["product"] == "G-Shock"]
        self.assertEqual(
            {(item["product_id"], item["article"]) for item in duplicates},
            {("p1", "GA-2100"), ("p2", "GA-2200")},
        )

    def test_brand_category_and_product_filter_the_same_position(self):
        context = self.route_context(
            "?receipt_brand_id=b1&receipt_category_id=c1&receipt_product_id=p2"
        )
        self.assertEqual([item["id"] for item in context["receipts"]], ["two"])
        self.assertEqual(context["total_receipts"], 1)
        self.assertEqual(context["total_quantity"], "1")

    def test_multi_item_document_counts_once_and_sums_only_matching_items(self):
        mixed = receipt(
            "mixed", "RCPT-MIXED", "Два бренда",
            positions=[
                {
                    "brand_id": "b1", "brand": "Casio",
                    "category_id": "c1", "category": "Часы",
                    "product_id": "p1", "product_name": "G-Shock",
                    "article": "GA-2100", "quantity": 10,
                },
                {
                    "brand_id": "b2", "brand": "Vechasu",
                    "category_id": "c2", "category": "Аксессуары",
                    "product_id": "p3", "product_name": "Ремешок",
                    "article": "STRAP-1", "quantity": 20,
                },
                {
                    "brand_id": "b1", "brand": "Casio",
                    "category_id": "c1", "category": "Часы",
                    "product_id": "p4", "product_name": "Edifice",
                    "article": "EFV-100", "quantity": 5,
                },
            ],
        )
        self.receipts = [mixed]

        by_brand = self.route_context("?receipt_brand_id=b1")
        by_product = self.route_context("?receipt_product_id=p3")

        self.assertEqual(by_brand["total_receipts"], 1)
        self.assertEqual(by_brand["total_quantity"], "15")
        self.assertEqual(by_brand["receipts"][0]["total_quantity"], 15)
        self.assertEqual(by_product["total_receipts"], 1)
        self.assertEqual(by_product["total_quantity"], "20")
        self.assertEqual(by_product["receipts"][0]["product_id"], "p3")

    def test_catalog_filters_must_intersect_on_one_position(self):
        mixed = receipt(
            "mixed", "RCPT-MIXED", "Два товара",
            positions=[
                {
                    "brand_id": "b1", "brand": "Casio",
                    "category_id": "c1", "category": "Часы",
                    "product_id": "p1", "product_name": "G-Shock",
                    "article": "GA-2100", "quantity": 10,
                },
                {
                    "brand_id": "b2", "brand": "Vechasu",
                    "category_id": "c2", "category": "Аксессуары",
                    "product_id": "p3", "product_name": "Ремешок",
                    "article": "STRAP-1", "quantity": 20,
                },
            ],
        )
        self.receipts = [mixed]

        context = self.route_context(
            "?receipt_brand_id=b1&receipt_category_id=c2"
        )

        self.assertEqual(context["receipts"], [])
        self.assertEqual(context["total_receipts"], 0)
        self.assertEqual(context["total_quantity"], "0")

    def test_period_search_and_catalog_filters_are_intersection(self):
        self.receipts = [
            receipt(
                "selected", "PR-2026-0021", "поставка от сегодня",
                quantity=10, receipt_date="2026-08-10",
            ),
            receipt(
                "wrong-date", "PR-2026-0022", "поставка от сегодня",
                quantity=50, receipt_date="2026-07-01",
            ),
            receipt(
                "wrong-brand", "PR-2026-0023", "поставка от сегодня",
                brand_id="b2", brand="Vechasu", quantity=20,
            ),
            receipt(
                "wrong-search", "PR-2026-0024", "другая поставка",
                quantity=30,
            ),
        ]

        context = self.route_context(
            "?q=сегодня&date_from=2026-08-01&date_to=2026-08-31"
            "&receipt_brand_id=b1&receipt_category_id=c1"
            "&receipt_product_id=p1"
        )

        self.assertEqual(
            [item["id"] for item in context["receipts"]], ["selected"]
        )
        self.assertEqual(context["total_receipts"], 1)
        self.assertEqual(context["total_quantity"], "10")

    def test_period_is_inclusive_and_changes_rows_and_kpis(self):
        self.receipts = [
            receipt(
                "a", "RCPT-A", "before", quantity=1,
                receipt_date="2026-08-10 23:59:59",
            ),
            receipt(
                "b", "RCPT-B", "start", quantity=10,
                receipt_date="2026-08-11 00:00:00",
            ),
            receipt(
                "c", "RCPT-C", "middle", quantity=20,
                receipt_date="2026-08-12 12:00:00",
            ),
            receipt(
                "d", "RCPT-D", "end", quantity=5,
                receipt_date="2026-08-13 18:45:00",
            ),
            receipt(
                "e", "RCPT-E", "after", quantity=100,
                receipt_date="2026-08-14 00:00:00",
            ),
        ]

        context = self.route_context(
            "?date_from=2026-08-11&date_to=2026-08-13"
        )

        self.assertEqual(
            [item["id"] for item in context["receipts"]],
            ["d", "c", "b"],
        )
        self.assertEqual(context["total_receipts"], 3)
        self.assertEqual(context["total_quantity"], "35")

    def test_single_day_includes_receipt_with_time_inside_last_day(self):
        self.receipts = [
            receipt(
                "before", "RCPT-BEFORE", "", quantity=10,
                receipt_date="2026-08-12 23:59:59",
            ),
            receipt(
                "start", "RCPT-START", "", quantity=20,
                receipt_date="2026-08-13 00:00:00",
            ),
            receipt(
                "inside", "RCPT-INSIDE", "", quantity=5,
                receipt_date="2026-08-13 18:45:00",
            ),
            receipt(
                "after", "RCPT-AFTER", "", quantity=30,
                receipt_date="2026-08-14 00:00:00",
            ),
        ]

        context = self.route_context(
            "?date_from=2026-08-13&date_to=2026-08-13"
        )

        self.assertEqual(
            [item["id"] for item in context["receipts"]],
            ["inside", "start"],
        )
        self.assertEqual(context["total_receipts"], 2)
        self.assertEqual(context["total_quantity"], "25")

    def test_period_and_product_only_count_matching_items_inside_period(self):
        product_a = {
            "brand_id": "b1", "brand": "Casio",
            "category_id": "c1", "category": "Часы",
            "product_id": "p1", "product_name": "G-Shock",
            "article": "GA-2100", "quantity": 10,
        }
        product_b = {
            "brand_id": "b2", "brand": "Vechasu",
            "category_id": "c2", "category": "Аксессуары",
            "product_id": "p2", "product_name": "Ремешок",
            "article": "STRAP", "quantity": 20,
        }
        self.receipts = [
            receipt(
                "inside", "RCPT-IN", "target", receipt_date="2026-08-12",
                positions=[product_a, product_b],
            ),
            receipt(
                "outside", "RCPT-OUT", "target", receipt_date="2026-08-14",
                positions=[dict(product_a, quantity=100)],
            ),
        ]

        context = self.route_context(
            "?date_from=2026-08-11&date_to=2026-08-13"
            "&receipt_brand_id=b1&receipt_category_id=c1"
            "&receipt_product_id=p1"
        )

        self.assertEqual(
            [item["id"] for item in context["receipts"]], ["inside"]
        )
        self.assertEqual(context["total_receipts"], 1)
        self.assertEqual(context["total_quantity"], "10")

    def test_period_state_survives_reload_and_reset_restores_dataset(self):
        self.receipts = [
            receipt("inside", "RCPT-IN", "", quantity=5,
                    receipt_date="2026-08-13 18:45:00"),
            receipt("outside", "RCPT-OUT", "", quantity=50,
                    receipt_date="2026-08-10 12:00:00"),
        ]
        query = "?date_from=2026-08-13&date_to=2026-08-13"

        applied = self.route_context(query)
        reloaded = self.route_context(query)
        reset = self.route_context("")

        self.assertEqual(
            [item["id"] for item in applied["receipts"]], ["inside"]
        )
        self.assertEqual(
            [item["id"] for item in reloaded["receipts"]], ["inside"]
        )
        self.assertEqual(reloaded["receipt_date_from"], "2026-08-13")
        self.assertEqual(reloaded["receipt_date_to"], "2026-08-13")
        self.assertEqual(reloaded["total_quantity"], "5")
        self.assertEqual(
            [item["id"] for item in reset["receipts"]],
            ["inside", "outside"],
        )
        self.assertEqual(reset["total_receipts"], 2)
        self.assertEqual(reset["total_quantity"], "55")

    def test_sorting_still_applies_after_backend_filters(self):
        self.receipts = [
            receipt("lower", "RCPT-010", "", quantity=1),
            receipt("higher", "RCPT-020", "", quantity=5),
            receipt(
                "other", "RCPT-030", "", brand_id="b2",
                brand="Vechasu", quantity=100,
            ),
        ]

        context = self.route_context(
            "?receipt_brand_id=b1&sort=quantity&sort_dir=desc"
        )

        self.assertEqual(
            [item["id"] for item in context["receipts"]],
            ["higher", "lower"],
        )

    def test_receipts_api_intersects_item_filters_and_totals_matching_units(self):
        mixed = receipt(
            "mixed", "RCPT-MIXED", "Два товара",
            positions=[
                {
                    "brand_id": "b1", "brand": "Casio",
                    "category_id": "c1", "category": "Часы",
                    "product_id": "p1", "product_name": "G-Shock",
                    "article": "GA-2100", "quantity": 10,
                },
                {
                    "brand_id": "b2", "brand": "Vechasu",
                    "category_id": "c2", "category": "Аксессуары",
                    "product_id": "p3", "product_name": "Ремешок",
                    "article": "STRAP-1", "quantity": 20,
                },
            ],
        )
        with web.app.test_request_context(
            "/api/receipts?brand_id=b1&category_id=c1&product_id=p1"
        ), mock.patch.object(
            web, "api_receipt_records", return_value=[mixed]
        ):
            payload = web.app.make_response(
                web.api_receipts_collection()
            ).get_json()

        self.assertEqual(payload["meta"]["total"], 1)
        self.assertEqual(payload["meta"]["totals"]["quantity"], 10)

        with web.app.test_request_context(
            "/api/receipts?brand_id=b1&category_id=c2"
        ), mock.patch.object(
            web, "api_receipt_records", return_value=[mixed]
        ):
            incompatible = web.app.make_response(
                web.api_receipts_collection()
            ).get_json()

        self.assertEqual(incompatible["meta"]["total"], 0)
        self.assertEqual(incompatible["meta"]["totals"]["quantity"], 0)

    def test_main_search_finds_document_number_and_comment(self):
        by_number = self.route_context("?q=rcpt-002")
        by_comment = self.route_context("?q=срочная")
        self.assertEqual([item["id"] for item in by_number["receipts"]], ["two"])
        self.assertEqual([item["id"] for item in by_comment["receipts"]], ["one"])

    def test_receipts_template_uses_id_cascade_without_duplicate_search_fields(self):
        source = (web.PROJECT_ROOT / "app/templates/receipts.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('id="receiptDocumentFilter"', source)
        self.assertNotIn('id="receiptCommentFilter"', source)
        for marker in (
            'id="receiptBrandFilter"',
            'id="receiptCategoryFilter"',
            'id="receiptFilterProductSearch"',
            "Поиск товара по названию или артикулу...",
            "Ничего не найдено",
            "receipt_brand_id",
            "receipt_category_id",
            "receipt_product_id",
        ):
            self.assertIn(marker, source)

        self.assertIn("function filterReceipts(periodValues = null)", source)
        self.assertIn(
            "onChange: (periodValues) => filterReceipts(periodValues)", source
        )
        self.assertIn("periodValues?.dateFrom", source)
        self.assertIn("periodValues?.dateTo", source)
        self.assertIn("receipts-period-3", source)


if __name__ == "__main__":
    unittest.main()
