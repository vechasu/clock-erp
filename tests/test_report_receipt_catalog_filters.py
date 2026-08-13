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
        product="G-Shock", article="GA-2100"):
    position = {
        "brand_id": brand_id,
        "brand": brand,
        "category_id": category_id,
        "category": category,
        "product_id": product_id,
        "product_name": product,
        "article": article,
        "quantity": 1,
    }
    return {
        "id": receipt_id,
        "number": number,
        "note": note,
        "receipt_date": "2026-08-10",
        "created_at": "2026-08-10 12:00:00",
        "status_label": "Проведён",
        "total_quantity": 1,
        "brand_id": brand_id,
        "brand": brand,
        "category_id": category_id,
        "category": category,
        "product_id": product_id,
        "product_name": product,
        "positions": [position],
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


if __name__ == "__main__":
    unittest.main()
