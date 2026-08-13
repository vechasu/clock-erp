import unittest
from pathlib import Path

from app import web


class SalesStatusVisualSystemTest(unittest.TestCase):
    def test_business_status_mapping_uses_semantic_labels_and_tones(self):
        cases = (
            ({"order_status": "completed"}, "completed", "Завершён успешно", "success"),
            ({"order_status": "cancelled"}, "cancelled", "Отменён", "warning"),
            ({"order_status": "returned"}, "returned", "Возврат", "warning"),
            (
                {
                    "order_status": "cancelled",
                    "cancellation_reason": "Клиент отказался",
                },
                "refusal",
                "Отказ",
                "danger",
            ),
            (
                {"order_status": "cancelled", "deleted_at": "2026-08-11"},
                "deleted",
                "Удалён",
                "destructive",
            ),
        )

        for sale, value, label, tone in cases:
            with self.subTest(value=value):
                presentation = web.get_sale_status_presentation(sale)
                self.assertEqual(presentation["value"], value)
                self.assertEqual(presentation["label"], label)
                self.assertEqual(presentation["tone"], tone)

    def test_unknown_legacy_status_is_preserved_and_neutral(self):
        presentation = web.get_sale_status_presentation(
            {"order_status": "legacy-awaiting-review"}
        )
        self.assertEqual(presentation["value"], "legacy-awaiting-review")
        self.assertEqual(presentation["raw_value"], "legacy-awaiting-review")
        self.assertEqual(presentation["label"], "legacy-awaiting-review")
        self.assertEqual(presentation["tone"], "neutral")
        self.assertNotEqual(presentation["label"], "Завершён успешно")

    def test_refusal_filter_does_not_include_other_cancellations(self):
        refusal = {
            "id": "refusal",
            "order_status": "cancelled",
            "cancellation_reason": "Клиент отказался",
        }
        cancellation = {
            "id": "cancellation",
            "order_status": "cancelled",
            "cancellation_reason": "Дубль",
        }
        result = web.filter_sales_report_records(
            [refusal, cancellation], {"status": "refusal"}
        )
        self.assertEqual([sale["id"] for sale in result], ["refusal"])

    def test_api_and_ui_use_the_same_status_presentation(self):
        sale = {
            "id": "sale-1",
            "order_status": "completed",
            "source": "Tictactoy",
        }
        web.decorate_sale_status(sale)
        serialized = web.serialize_api_sale(sale)
        self.assertEqual(
            (
                serialized["order_status_label"],
                serialized["order_status_tone"],
                serialized["order_status_class"],
            ),
            (
                sale["order_status_label"],
                sale["order_status_tone"],
                sale["order_status_class"],
            ),
        )

    def test_table_mobile_card_report_and_editor_use_unified_badge(self):
        root = Path(web.app.root_path) / "templates"
        sales_template = (root / "sales.html").read_text(encoding="utf-8")
        report_template = (root / "sales_report.html").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(
            sales_template.count("sale-status-badge {{ sale.order_status_class }}"),
            2,
        )
        self.assertIn('id="saleModalStatusBadge"', sales_template)
        self.assertIn("sale.order_status_label", report_template)
        self.assertIn("sale-status-badge--success", sales_template)
        self.assertIn("white-space: nowrap", sales_template)


if __name__ == "__main__":
    unittest.main()
