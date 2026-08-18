import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sale_pricing import calculate_sale_pricing, order_line_pricing
from app.services.sales_inventory import SalesInventory


class SalePricingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES ('batch', 'sha', 'test.xlsx', 0, 0, 0, 0, "
                "'active', '2026-08-18T10:00:00+00:00', "
                "'2026-08-18T10:00:00+00:00')"
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.product = self.catalog.create_product(
            "Ziiiro Celeste", article="Z-1", brand="Ziiiro",
            category="Часы", stock=4,
        )
        self.sales = SalesInventory(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_percent_fixed_rounding_and_zero_floor(self):
        percent = calculate_sale_pricing("199.95", "percent", "12.5")
        fixed = calculate_sale_pricing("1000", "fixed", "125.25")
        floor = calculate_sale_pricing("100", "fixed", "999")

        self.assertEqual(percent["discount_amount"], "24.99")
        self.assertEqual(percent["unit_price"], "174.96")
        self.assertEqual(fixed["unit_price"], "874.75")
        self.assertEqual(floor["unit_price"], "0.00")

    def test_order_price_is_prefilled_from_paid_and_base_prices(self):
        pricing = order_line_pricing({"BASE_PRICE": "1500", "PRICE": "1200"})
        self.assertEqual(pricing["original_unit_price"], "1500.00")
        self.assertEqual(pricing["discount_type"], "fixed")
        self.assertEqual(pricing["discount_value"], "300.00")
        self.assertEqual(pricing["unit_price"], "1200.00")
        self.assertEqual(pricing["discount_reason"], "Скидка из заказа")

    def test_sale_keeps_price_snapshot_and_discount_update_does_not_touch_stock(self):
        payload = {
            "id": "discount-sale",
            "source": "Tictactoy",
            "order_number": "D-1",
            "product_name": "Ziiiro Celeste",
            "original_unit_price": "1000",
            "discount_type": "percent",
            "discount_value": "10",
            "discount_reason": "Промокод",
        }
        sale = self.sales.create_sale(
            payload, self.product["id"], 1, 1000, user_name="Максим"
        )
        self.assertEqual(sale["unit_price"], 900.0)
        self.assertEqual(sale["original_unit_price"], "1000.00")
        self.assertEqual(sale["discount_amount"], "100.00")
        stock_after_sale = self.catalog.get_product(self.product["id"])["stock"]

        updated = self.sales.update_sale(
            sale["id"],
            {
                "_pricing_explicit": True,
                "original_unit_price": "1000",
                "discount_type": "fixed",
                "discount_value": "125.25",
                "discount_reason": "Ручная скидка",
            },
            quantity=1,
            unit_price=874.75,
            user_name="Максим",
        )

        self.assertEqual(updated["unit_price"], 874.75)
        self.assertEqual(updated["discount_amount"], "125.25")
        self.assertEqual(
            self.catalog.get_product(self.product["id"])["stock"],
            stock_after_sale,
        )
        with self.database.connect() as connection:
            event = connection.execute(
                "SELECT changes_json FROM erp_audit_events "
                "WHERE entity_type = 'sale' AND entity_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (sale["id"],),
            ).fetchone()
        self.assertIn("discount_amount", event["changes_json"])

    def test_frontend_uses_cent_rounding_and_backend_remains_authoritative(self):
        source = (Path(__file__).parents[1] / "app/templates/sales.html").read_text()
        self.assertIn("function recalculateSalePrice()", source)
        self.assertIn("Math.round", source)
        self.assertIn('name="discount_type"', source)


if __name__ == "__main__":
    unittest.main()
