import ast
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"


class ErpDatetimeCellsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment = Environment(loader=FileSystemLoader(str(TEMPLATES)))
        cls.render_datetime = environment.get_template(
            "_datetime_cell.html"
        ).module.render_erp_datetime
        cls.sales = (TEMPLATES / "sales.html").read_text(encoding="utf-8")
        cls.warehouse = (TEMPLATES / "warehouse.html").read_text(
            encoding="utf-8"
        )
        cls.receipts = (TEMPLATES / "receipts.html").read_text(
            encoding="utf-8"
        )
        cls.styles = (
            ROOT / "app" / "static" / "css" / "erp-components.css"
        ).read_text(encoding="utf-8")

    def test_shared_macro_formats_date_and_time_without_seconds(self):
        rendered = self.render_datetime("2026-08-04T14:14:59+03:00")
        self.assertIn('class="erp-datetime-cell"', rendered)
        self.assertIn('class="erp-datetime-date">04.08.2026</span>', rendered)
        self.assertIn('class="erp-datetime-time">14:14</span>', rendered)
        self.assertNotIn("14:14:59", rendered)

    def test_missing_time_does_not_render_an_empty_second_line(self):
        rendered = self.render_datetime("2026-08-04")
        self.assertIn("04.08.2026", rendered)
        self.assertNotIn("erp-datetime-time", rendered)
        self.assertNotIn("00:00", rendered)

    def test_separate_receipt_timestamp_supplies_time(self):
        rendered = self.render_datetime(
            "2026-08-04", "2026-08-04 09:07:31"
        )
        self.assertIn("04.08.2026", rendered)
        self.assertIn('class="erp-datetime-time">09:07</span>', rendered)
        self.assertNotIn("09:07:31", rendered)

    def test_missing_datetime_preserves_placeholder(self):
        rendered = self.render_datetime(None)
        self.assertIn('class="erp-muted-value">—</span>', rendered)
        self.assertNotIn("erp-datetime-cell", rendered)

    def test_all_main_tables_reuse_the_same_macro(self):
        import_line = (
            '{% from "_datetime_cell.html" import render_erp_datetime %}'
        )
        for template in (self.warehouse, self.sales, self.receipts):
            with self.subTest(template=template[:20]):
                self.assertEqual(template.count(import_line), 1)
                self.assertIn("render_erp_datetime(", template)

        self.assertNotIn("sale-date-cell", self.sales)
        self.assertNotIn("receipt-date-stack", self.receipts)
        self.assertNotIn("receipt-date-time", self.receipts)

    def test_sales_all_channels_share_the_server_rendered_datetime(self):
        self.assertIn(
            '{% elif column.key == "created_at" %}', self.sales
        )
        self.assertIn("{{ render_erp_datetime(value) }}", self.sales)
        for source in ("all", "tictactoy", "wildberries", "amazon"):
            self.assertIn(source, self.sales)

    def test_product_and_receipt_tables_use_shared_datetime(self):
        self.assertIn(
            "{{ render_erp_datetime(item.created_at_display) }}",
            self.warehouse,
        )
        self.assertIn(
            "render_erp_datetime(receipt_date_value, receipt_created_value)",
            self.receipts,
        )

    def test_sorting_keeps_the_complete_source_timestamp(self):
        self.assertIn('data-sort-value="{{ value }}"', self.sales)
        self.assertIn("Date.parse(rawValue)", self.sales)
        self.assertIn(
            'data-date="{{ receipt.receipt_date or receipt.created_at or \'\' }}"',
            self.receipts,
        )
        self.assertIn("row.dataset[key]", self.receipts)
        self.assertIn('value="created_at"', self.warehouse)

    def test_dynamic_filtering_and_sorting_reuse_existing_rows(self):
        self.assertIn("salesTableBody.appendChild(row)", self.sales)
        self.assertIn("body.appendChild(row)", self.receipts)
        self.assertIn("filterReceipts();", self.receipts)
        self.assertIn("navigateSales({q:", self.sales)
        self.assertIn('url.searchParams.delete("page")', self.sales)

    def test_shared_css_prevents_clipping_at_all_breakpoints(self):
        datetime_rule = self.styles.split(".erp-datetime-cell {", 1)[1]
        datetime_rule = datetime_rule.split("}", 1)[0]
        self.assertIn("flex-direction: column", datetime_rule)
        self.assertIn("align-items: flex-start", datetime_rule)
        self.assertIn("min-width: 10ch", datetime_rule)
        self.assertIn("white-space: nowrap", datetime_rule)
        self.assertIn("text-overflow: clip", datetime_rule)
        self.assertNotIn("ellipsis", datetime_rule)
        self.assertEqual(self.styles.count(".erp-datetime-cell {"), 1)

    def test_server_code_remains_python_36_compatible(self):
        for path in sorted((ROOT / "app").rglob("*.py")):
            with self.subTest(path=path):
                ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                    feature_version=(3, 6),
                )


if __name__ == "__main__":
    unittest.main()
