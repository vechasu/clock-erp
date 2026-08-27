import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.schema_migrations import apply_migrations
from app.services.business_analytics import BusinessAnalytics, parse_filters
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import SalesInventory


class BusinessAnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary.name) / "catalog.db"
        apply_migrations(database_path, app_commit="analytics-test")
        self.database = CatalogDatabase(
            database_path,
            cache_initialization=False,
        )
        self.database.initialize()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches(id,file_sha256,source_filename,row_count,total_stock,positive_rows,zero_rows,status,created_at,applied_at) VALUES('analytics-batch','analytics-sha','analytics.xlsx',0,0,0,0,'active','2026-08-01','2026-08-01')"
            )
            connection.commit()
        self.catalog = ExcelProductCatalog(self.database)
        self.product = self.catalog.create_product(
            name="Test watch", article="T-1", brand="Brand A",
            category="Watch", stock=20,
        )
        self.sales = SalesInventory(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def filters(self, **values):
        arguments = {"from": "2026-08-01", "to": "2026-08-30"}
        arguments.update(values)
        return parse_filters(arguments, today=date(2026, 8, 30))

    def test_period_defaults_to_last_thirty_days_and_normalizes_reversed_range(self):
        defaults = parse_filters({}, today=date(2026, 8, 30))
        reversed_range = parse_filters(
            {"from": "2026-08-20", "to": "2026-08-10"},
            today=date(2026, 8, 30),
        )
        self.assertEqual((defaults["from"], defaults["to"], defaults["days"]), ("2026-08-01", "2026-08-30", 30))
        self.assertEqual((reversed_range["from"], reversed_range["to"]), ("2026-08-10", "2026-08-20"))

    def test_multiline_sale_counts_document_once_and_quantities_not_lines(self):
        second = self.catalog.create_product(
            name="Second watch", article="T-2", brand="Brand A",
            category="Watch", stock=20,
        )
        sale = self.sales.create_sale(
            {"source": "manual", "created_at": "2026-08-10T12:00:00+03:00"},
            self.product["id"], 2, 100,
        )
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO erp_sale_items(sale_id,product_id,quantity,unit_price,created_at) VALUES(?,?,?,?,?)",
                (sale["id"], second["id"], 3, 50, "2026-08-10T12:00:00+03:00"),
            )
            connection.commit()
        result = BusinessAnalytics(self.database).context("summary", self.filters())
        self.assertEqual(result["current"]["sales"], 1)
        self.assertEqual(result["current"]["units"], 5)
        self.assertEqual(result["current"]["revenue"], 350)
        self.assertEqual(result["current"]["average_order"], 350)

    def test_cancelled_archived_and_full_returned_sales_do_not_inflate_metrics(self):
        completed = self.sales.create_sale(
            {"source": "manual", "created_at": "2026-08-11T12:00:00+03:00"},
            self.product["id"], 2, 100,
        )
        returned = self.sales.create_sale(
            {"source": "manual", "created_at": "2026-08-12T12:00:00+03:00"},
            self.product["id"], 1, 90,
        )
        self.sales.return_sale(returned["id"], 1, "return")
        with self.database.connect() as connection:
            connection.execute("UPDATE erp_sales SET archived_at='2026-08-13' WHERE id=?", (completed["id"],))
            connection.commit()
        result = BusinessAnalytics(self.database).context("summary", self.filters())
        self.assertEqual(result["current"]["sales"], 0)
        self.assertEqual(result["current"]["units"], 0)
        self.assertEqual(result["current"]["revenue"], 0)
        self.assertEqual(result["changes"]["revenue"]["percent_display"], "—")

    def test_unknown_channel_is_retained_and_profit_is_not_fabricated(self):
        self.sales.create_sale(
            {"source": "unknown-market", "created_at": "2026-08-15T12:00:00+03:00"},
            self.product["id"], 1, 100,
        )
        service = BusinessAnalytics(self.database)
        channels = service.context("channels", self.filters())
        profit = service.context("profit", self.filters())
        self.assertEqual(channels["rows"][0]["label"], "unknown-market")
        self.assertIn("Прибыль не рассчитывается", profit["unavailable_reason"])

    def test_missing_sale_price_hides_revenue_instead_of_using_zero(self):
        self.sales.create_sale(
            {"source": "manual", "created_at": "2026-08-16T12:00:00+03:00"},
            self.product["id"], 2, None,
        )
        result = BusinessAnalytics(self.database).context("summary", self.filters())
        self.assertEqual(result["current"]["sales"], 1)
        self.assertEqual(result["current"]["units"], 2)
        self.assertIsNone(result["current"]["revenue"])
        self.assertIsNone(result["current"]["average_order"])

    def test_routes_navigation_fragments_and_csv_are_read_only(self):
        from app import web

        web.app.config.update(TESTING=True)
        client = web.app.test_client()
        page = client.get("/app/analytics?section=summary&from=2026-08-01&to=2026-08-30")
        customers = client.get("/app/analytics?section=customers&from=2026-08-01&to=2026-08-30")
        fragment = client.get("/app/analytics/section?section=profit&from=2026-08-01&to=2026-08-30")
        export = client.get("/app/analytics/export.csv?section=summary&from=2026-08-01&to=2026-08-30")
        self.assertEqual((page.status_code, customers.status_code, fragment.status_code, export.status_code), (200, 200, 200, 200))
        self.assertIn("Аналитика", page.get_data(as_text=True))
        self.assertIn("Уникальные клиенты", customers.get_data(as_text=True))
        self.assertIn('data-navigation-key="analytics"', page.get_data(as_text=True))
        self.assertIn("Прибыль не рассчитывается", fragment.get_data(as_text=True))
        self.assertTrue(export.data.startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
