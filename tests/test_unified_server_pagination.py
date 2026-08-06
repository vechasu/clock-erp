import unittest
from unittest import mock

from app import web


def sale(index, source="tictactoy"):
    return {
        "id": "sale-{:03d}".format(index),
        "created_at": "2026-08-{:02d}T12:00:00".format((index % 28) + 1),
        "source": web.SALES_SOURCE_LABELS[source],
        "source_key": source,
        "product_name": "Товар {:03d}".format(index),
        "article": "ART-{:03d}".format(index),
        "brand": "Бренд",
        "category": "Категория",
        "quantity_value": 1,
        "quantity_display": "1",
        "unit_price": 100,
        "unit_price_display": "100 ₽",
        "total_amount": 100,
        "total_amount_display": "100 ₽",
        "delivery_cost": 0,
        "delivery_cost_display": "0 ₽",
        "commission": "sbp",
        "order_status": "completed",
        "order_status_label": "Выполнен",
        "order_number": "ORDER-{:03d}".format(index),
        "is_cancelled": False,
        "is_manual": True,
        "sale_type": "manual",
        "net_quantity_value": 1,
        "return_available_quantity": 1,
    }


def receipt(index):
    return {
        "id": "receipt-{:03d}".format(index),
        "number": "REC-{:03d}".format(index),
        "created_at": "2026-08-{:02d}T10:00:00".format((index % 28) + 1),
        "receipt_date": "2026-08-{:02d}".format((index % 28) + 1),
        "brand": "Бренд",
        "category": "Категория",
        "product_name": "Товар {:03d}".format(index),
        "note": "Комментарий {:03d}".format(index),
        "status": "posted",
        "status_label": "Проведён",
        "total_quantity": index,
        "positions": [],
    }


class UnifiedServerPaginationTest(unittest.TestCase):
    def test_parser_defaults_and_rejects_unsupported_values(self):
        cases = [
            ("/app/sales", (1, 50)),
            ("/app/sales?page=-2&per_page=25", (1, 25)),
            ("/app/sales?page=no&per_page=100", (1, 100)),
            ("/app/sales?page=2&per_page=200", (2, 50)),
        ]
        for url, expected in cases:
            with self.subTest(url=url), web.app.test_request_context(url):
                self.assertEqual(web.parse_erp_pagination(), expected)

    def test_common_pagination_has_first_middle_last_and_preserves_state(self):
        with web.app.test_request_context(
            "/app/sales?source=amazon&q=watch&page=5&per_page=25"
        ):
            pagination = web.build_erp_pagination(
                "sales_page", 260, 5, 25
            )
        self.assertEqual(pagination["start"], 101)
        self.assertEqual(pagination["end"], 125)
        self.assertEqual(pagination["pages"], 11)
        self.assertIn(None, pagination["items"])
        self.assertIn("source=amazon", pagination["next_url"])
        self.assertIn("q=watch", pagination["next_url"])
        self.assertIn("per_page=25", pagination["next_url"])

    def test_sales_paginates_each_source_and_keeps_full_kpi(self):
        records = [sale(index, ("tictactoy", "wildberries", "amazon")[index % 3])
                   for index in range(1, 181)]
        for source in ("all", "tictactoy", "wildberries", "amazon"):
            with self.subTest(source=source), web.app.test_request_context(
                "/app/sales?source={}&page=2&per_page=25&sort=article&sort_dir=asc".format(source)
            ), mock.patch.object(web, "get_warehouse_items", return_value=[]), \
                    mock.patch.object(web, "build_sales_report_records", return_value=records), \
                    mock.patch.object(web, "render_template", side_effect=lambda name, **ctx: ctx):
                context = web.sales_page()
            expected_total = 180 if source == "all" else 60
            self.assertEqual(len(context["sales"]), 25)
            self.assertEqual(context["pagination"]["total"], expected_total)
            self.assertEqual(context["total_sales"], expected_total)
            articles = [item["article"] for item in context["sales"]]
            self.assertEqual(articles, sorted(articles))

    def test_sales_empty_and_oversized_pages_are_safe(self):
        for records, expected_page in (([], 1), ([sale(i) for i in range(3)], 1)):
            with web.app.test_request_context("/app/sales?page=999"), \
                    mock.patch.object(web, "get_warehouse_items", return_value=[]), \
                    mock.patch.object(web, "build_sales_report_records", return_value=records), \
                    mock.patch.object(web, "render_template", side_effect=lambda name, **ctx: ctx):
                context = web.sales_page()
            self.assertEqual(context["pagination"]["page"], expected_page)

    def test_receipts_filter_sort_and_paginate_on_server(self):
        records = [receipt(index) for index in range(1, 121)]
        with web.app.test_request_context(
            "/app/receipts?q=товар&receipt_brand=Бренд&page=2&per_page=25&sort=document&sort_dir=asc"
        ), mock.patch.object(web, "api_receipt_records", return_value=records), \
                mock.patch.object(web, "render_template", side_effect=lambda name, **ctx: ctx):
            context = web.receipts_page()
        self.assertEqual(len(context["receipts"]), 25)
        self.assertEqual(context["pagination"]["total"], 120)
        self.assertEqual(context["receipts"][0]["number"], "REC-026")
        self.assertEqual(context["total_receipts"], 120)
        self.assertEqual(context["total_quantity"], "7260")
        self.assertIn("q=", context["pagination"]["next_url"])

    def test_shared_template_exposes_only_supported_sizes_and_mobile_controls(self):
        source = (web.PROJECT_ROOT / "app" / "templates" / "_pagination.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("pagination.per_page_options", source)
        self.assertIn("aria-current=\"page\"", source)
        self.assertIn("Назад", source)
        self.assertIn("Вперёд", source)
        self.assertIn("aria-disabled=\"true\"", source)


if __name__ == "__main__":
    unittest.main()
