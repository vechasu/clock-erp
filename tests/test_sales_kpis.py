import unittest

from app import web


def sale(
        sale_id, *, status="completed", quantity=1, net_quantity=None,
        amount=100, source="tictactoy", created_at="2026-08-10T12:00:00",
        brand_id="b1", category_id="c1", product_id="p1",
        order_number=None, sale_type="manual", deleted_at=""):
    return {
        "id": sale_id,
        "sale_type": sale_type,
        "source": web.get_sales_source_label(source),
        "source_key": source,
        "order_number": (
            "ORDER-{}".format(sale_id)
            if order_number is None
            else order_number
        ),
        "order_status": status,
        "deleted_at": deleted_at,
        "created_at": created_at,
        "brand_id": brand_id,
        "brand": "Brand {}".format(brand_id),
        "category_id": category_id,
        "category": "Category {}".format(category_id),
        "product_id": product_id,
        "product_name": "Product {}".format(product_id),
        "article": "ARTICLE-{}".format(product_id),
        "quantity_value": quantity,
        "net_quantity_value": (
            quantity if net_quantity is None else net_quantity
        ),
        "total_amount": amount,
        "is_cancelled": status in {"cancelled", "refusal"},
    }


class SalesKpiContractTest(unittest.TestCase):
    def test_every_status_uses_the_confirmed_business_contract(self):
        records = [
            sale("completed", status="completed", quantity=2, amount=200),
            sale("processing", status="processing", amount=500),
            sale("shipped", status="shipped", amount=600),
            sale("cancelled", status="cancelled", amount=700),
            sale("refusal", status="refusal", amount=800),
            sale(
                "partial", status="partially_returned",
                quantity=5, net_quantity=3, amount=300,
            ),
            sale(
                "returned", status="returned",
                quantity=4, net_quantity=0, amount=0,
            ),
            sale(
                "deleted", status="deleted", amount=900,
                deleted_at="2026-08-11T10:00:00",
            ),
        ]

        result = web.calculate_sales_kpis(records)

        self.assertEqual(result["sales_count"], 2)
        self.assertEqual(result["quantity"], 5)
        self.assertEqual(result["revenue"], 500)
        self.assertEqual(result["revenue_display"], "500 ₽")
        self.assertEqual(result["average_receipt"], 250)
        self.assertEqual(result["average_receipt_display"], "250 ₽")
        self.assertEqual(result["cancelled_count"], 2)
        self.assertEqual(result["processing_count"], 1)
        self.assertEqual(result["shipped_count"], 1)

    def test_multiple_lines_and_quantity_above_one_count_one_sale(self):
        records = [
            sale("multi", quantity=2, amount=200),
            sale(
                "multi", quantity=3, amount=450,
                product_id="p2",
            ),
        ]

        result = web.calculate_sales_kpis(records)

        self.assertEqual(result["sales_count"], 1)
        self.assertEqual(result["quantity"], 5)
        self.assertEqual(result["revenue"], 650)
        self.assertEqual(result["average_receipt"], 650)

    def test_automatic_lines_are_grouped_by_source_and_order(self):
        records = [
            sale(
                "operation-1", quantity=2, amount=200,
                sale_type="automatic", order_number="BITRIX-1",
            ),
            sale(
                "operation-2", quantity=1, amount=150,
                sale_type="automatic", order_number="BITRIX-1",
                product_id="p2",
            ),
        ]

        result = web.calculate_sales_kpis(records)

        self.assertEqual(result["sales_count"], 1)
        self.assertEqual(result["quantity"], 3)
        self.assertEqual(result["revenue"], 350)

    def test_partial_and_full_returns_are_distinct(self):
        records = [
            sale(
                "partial", status="partially_returned",
                quantity=4, net_quantity=2, amount=240,
            ),
            sale(
                "full", status="returned",
                quantity=5, net_quantity=0, amount=0,
            ),
        ]

        result = web.calculate_sales_kpis(records)

        self.assertEqual(result["sales_count"], 1)
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["revenue"], 240)
        self.assertEqual(result["average_receipt"], 240)

    def test_missing_price_preserves_quantitative_kpis(self):
        result = web.calculate_sales_kpis([
            sale("known", quantity=2, amount=200),
            sale("unknown", quantity=3, amount=None),
            sale("cancelled", status="cancelled", quantity=9, amount=None),
        ])

        self.assertEqual(result["sales_count"], 2)
        self.assertEqual(result["quantity"], 5)
        self.assertEqual(result["cancelled_count"], 1)
        self.assertIsNone(result["revenue"])
        self.assertIsNone(result["average_receipt"])
        self.assertEqual(result["revenue_display"], "Нет данных")
        self.assertEqual(result["average_receipt_display"], "Нет данных")

    def test_missing_price_outside_financial_status_does_not_hide_money(self):
        result = web.calculate_sales_kpis([
            sale("known", amount=120),
            sale("shipped", status="shipped", amount=None),
            sale("returned", status="returned", amount=None),
        ])

        self.assertEqual(result["revenue"], 120)
        self.assertEqual(result["average_receipt"], 120)

    def test_empty_selection_has_zero_money_and_counts(self):
        result = web.calculate_sales_kpis([])

        self.assertEqual(result["revenue_display"], "0 ₽")
        self.assertEqual(result["average_receipt_display"], "0 ₽")
        self.assertEqual(result["sales_count"], 0)
        self.assertEqual(result["quantity_display"], "0")
        self.assertEqual(result["cancelled_count"], 0)

    def test_catalog_filters_count_only_matching_item_lines(self):
        records = [
            sale(
                "multi", quantity=2, amount=200,
                brand_id="b1", category_id="c1", product_id="p1",
            ),
            sale(
                "multi", quantity=7, amount=700,
                brand_id="b2", category_id="c2", product_id="p2",
            ),
            sale(
                "other", quantity=11, amount=1100,
                brand_id="b1", category_id="c2", product_id="p3",
            ),
        ]

        filtered = web.filter_sales_report_records(records, {
            "source": "all",
            "brand_id": "b1",
            "category_id": "c1",
            "product_id": "p1",
        })
        result = web.calculate_sales_kpis(filtered)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(result["sales_count"], 1)
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["revenue"], 200)

    def test_period_source_and_reset_share_the_filtered_dataset(self):
        records = [
            sale(
                "before", created_at="2026-08-10T23:59:59",
                source="amazon",
            ),
            sale(
                "start", created_at="2026-08-11T00:00:00",
                source="amazon", quantity=2, amount=200,
            ),
            sale(
                "end", created_at="2026-08-13T23:59:59",
                source="amazon", quantity=3, amount=300,
            ),
            sale(
                "other-source", created_at="2026-08-12T12:00:00",
                source="wildberries", quantity=5, amount=500,
            ),
            sale(
                "after", created_at="2026-08-14T00:00:00",
                source="amazon",
            ),
        ]

        filtered = web.filter_sales_report_records(records, {
            "source": "amazon",
            "date_from": "2026-08-11",
            "date_to": "2026-08-13",
        })
        result = web.calculate_sales_kpis(filtered)
        reset = web.calculate_sales_kpis(
            web.filter_sales_report_records(records, {"source": "all"})
        )

        self.assertEqual({item["id"] for item in filtered}, {"start", "end"})
        self.assertEqual(result["sales_count"], 2)
        self.assertEqual(result["quantity"], 5)
        self.assertEqual(result["revenue"], 500)
        self.assertEqual(reset["sales_count"], 5)

    def test_single_day_and_status_keep_kpi_business_meaning(self):
        records = [
            sale(
                "completed", created_at="2026-08-13T18:45:00",
                amount=300,
            ),
            sale(
                "cancelled", status="cancelled",
                created_at="2026-08-13T20:00:00", amount=900,
            ),
        ]

        completed = web.filter_sales_report_records(records, {
            "source": "all", "date_from": "2026-08-13",
            "date_to": "2026-08-13", "status": "completed",
        })
        cancelled = web.filter_sales_report_records(records, {
            "source": "all", "date_from": "2026-08-13",
            "date_to": "2026-08-13", "status": "cancelled",
        })

        self.assertEqual(web.calculate_sales_kpis(completed)["revenue"], 300)
        cancelled_kpis = web.calculate_sales_kpis(cancelled)
        self.assertEqual(cancelled_kpis["sales_count"], 0)
        self.assertEqual(cancelled_kpis["revenue_display"], "0 ₽")
        self.assertEqual(cancelled_kpis["cancelled_count"], 1)


if __name__ == "__main__":
    unittest.main()
