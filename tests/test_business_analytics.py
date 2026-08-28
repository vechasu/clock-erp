import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.schema_migrations import apply_migrations
from app.services.business_analytics import BusinessAnalytics, parse_filters
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.purchases import PurchaseStore
from app.services.sales_inventory import SalesInventory
from app.purchases_migrations import migrate_database as migrate_purchases


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

    def test_revenue_contract_is_identical_across_summary_products_and_channels(self):
        second = self.catalog.create_product(
            name="Price missing", article="T-3", brand="Brand B",
            category="Watch", stock=5,
        )
        self.sales.create_sale(
            {"source": "manual", "created_at": "2026-08-16T12:00:00+03:00"},
            self.product["id"], 2, 100,
        )
        self.sales.create_sale(
            {"source": "wildberries", "created_at": "2026-08-17T12:00:00+03:00"},
            second["id"], 1, None,
        )
        service = BusinessAnalytics(self.database)
        summary = service.context("summary", self.filters())
        products = service.context("products", self.filters(sort="revenue"))
        channels = service.context("channels", self.filters())
        self.assertIsNone(summary["current"]["revenue"])
        self.assertTrue(all(row["revenue"] is None for row in products["rows"]))
        self.assertTrue(all(row["revenue"] is None and row["share"] is None for row in channels["rows"]))
        self.assertEqual(sum(row["sales"] for row in channels["rows"]), summary["current"]["sales"])
        self.assertEqual(sum(row["units"] for row in channels["rows"]), summary["current"]["units"])

    def test_products_are_searchable_sortable_and_server_paginated(self):
        for index in range(28):
            product = self.catalog.create_product(
                name="Analytics item {:02d}".format(index), article="A-{:02d}".format(index),
                brand="Brand A", category="Watch", stock=index + 1,
            )
            self.sales.create_sale(
                {"source": "manual", "created_at": "2026-08-20T12:00:00+03:00"},
                product["id"], 1, 10 + index,
            )
        result = BusinessAnalytics(self.database).context(
            "products", self.filters(q="Analytics item", sort="name", per_page="25", page="2")
        )
        self.assertEqual((result["pagination"]["total"], result["pagination"]["pages"], len(result["rows"])), (28, 2, 3))
        self.assertEqual(result["rows"][0]["name"], "Analytics item 25")

    def test_inventory_discrepancies_never_create_attention_signals(self):
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO erp_inventory_sessions(id,brand_id,status,started_at,updated_at,checked_positions,adjusted_positions,missing_positions,total_delta) "
                "VALUES('inventory-alert-test',1,'completed','2026-08-01','2026-08-02',10,8,2,999)"
            )
            connection.commit()
        result = BusinessAnalytics(self.database).context("summary", self.filters(signal_type="inventory"))
        self.assertNotEqual(result["filters"]["signal_type"], "inventory")
        self.assertFalse(any(event["type"] == "inventory" for event in result["attention"]))

    def test_frontend_lifecycle_has_one_owner_abort_and_stale_response_guard(self):
        script = (Path(__file__).resolve().parents[1] / "app/static/js/analytics.js").read_text(encoding="utf-8")
        self.assertIn("window.__vechasuAnalyticsOwner", script)
        self.assertIn("controller.abort()", script)
        self.assertIn("currentRequest !== requestId", script)
        self.assertEqual(script.count("addEventListener('popstate'"), 1)
        self.assertNotIn("document.write", script)

    def test_query_budget_stays_bounded_for_summary_and_products(self):
        statements = []
        original_connect = self.database.connect

        def traced_connect():
            connection = original_connect()
            connection.set_trace_callback(
                lambda statement: statements.append(statement)
                if statement.lstrip().upper().startswith("SELECT") else None
            )
            return connection

        self.database.connect = traced_connect
        service = BusinessAnalytics(self.database)
        service.context("summary", self.filters())
        summary_count = len(statements)
        statements[:] = []
        service.context("products", self.filters())
        self.assertLessEqual(summary_count, 20)
        self.assertLessEqual(len(statements), 10)

    def test_stock_signal_requires_repeat_demand_and_exposes_formula_inputs(self):
        self.sales.create_sale(
            {"source": "manual", "created_at": "2026-08-10T12:00:00+03:00"},
            self.product["id"], 1, 100,
        )
        with self.database.connect() as connection:
            connection.execute("UPDATE catalog_excel_products SET stock=0 WHERE id=?", (self.product["id"],))
            connection.commit()
        service = BusinessAnalytics(self.database)
        isolated = service.context("stock", self.filters(horizon="60"))["rows"][0]
        self.assertEqual(isolated["recommendation_code"], "insufficient")
        self.assertEqual((isolated["units_30"], isolated["units_60"], isolated["units_90"]), (1, 1, 1))
        self.assertTrue(isolated["preliminary"])

        with self.database.connect() as connection:
            connection.execute("UPDATE catalog_excel_products SET stock=1 WHERE id=?", (self.product["id"],))
            connection.commit()
        self.sales.create_sale({"source": "manual", "created_at": "2026-08-20T12:00:00+03:00"}, self.product["id"], 1, 100)
        repeated = service.context("stock", self.filters(horizon="60"))["rows"][0]
        self.assertEqual(repeated["recommendation_code"], "urgent")
        self.assertGreaterEqual(repeated["recommended_quantity"], 1)
        self.assertIn("2 ед. за 90 дней", repeated["evidence"])

    def test_open_supplier_order_prevents_duplicate_purchase_recommendation(self):
        purchases_path = Path(self.temporary.name) / "purchases.db"
        migrate_purchases(purchases_path)
        store = PurchaseStore(purchases_path)
        now = "2026-08-20T10:00:00+00:00"
        with store.connect() as connection:
            plan_id = connection.execute(
                "INSERT INTO purchase_plan_items(grouping_key,product_id,product_name,status,created_at,updated_at,updated_by) VALUES(?,?,?,'ordered',?,?,?)",
                ("product:{}".format(self.product["id"]), self.product["id"], "Test watch", now, now, 1),
            ).lastrowid
            order_id = connection.execute(
                "INSERT INTO supplier_orders(internal_number,supplier_name,created_date,ordered_date,currency,status,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,'ordered',?,?,?,?)",
                ("SUP-1", "Supplier", "2026-08-20", "2026-08-20", "RUB", now, now, 1, 1),
            ).lastrowid
            connection.execute(
                "INSERT INTO supplier_order_items(order_id,plan_item_id,quantity,received_quantity,purchase_price,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (order_id, plan_id, 5, 1, 10, now, now),
            )
            connection.commit()
        result = BusinessAnalytics(self.database, store).context("stock", self.filters())
        row = next(item for item in result["rows"] if item["id"] == self.product["id"])
        self.assertEqual((row["ordered_quantity"], row["recommendation_code"]), (4, "ordered"))

    def test_stock_filters_pagination_and_attention_exclude_zero_stock_without_demand(self):
        for index in range(55):
            self.catalog.create_product(
                name="Inactive demand {}".format(index), article="Z-{}".format(index),
                brand="Brand Z", category="Watch", stock=0,
            )
        service = BusinessAnalytics(self.database)
        stock = service.context("stock", self.filters(brand="Brand Z", per_page="25"))
        summary = service.context("summary", self.filters())
        self.assertEqual((stock["pagination"]["total"], stock["pagination"]["pages"], len(stock["rows"])), (55, 3, 25))
        self.assertFalse(any(event["type"] == "stockout" for event in summary["attention"]))
        self.assertIn("formula", summary["metric_registry"]["revenue"])

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
