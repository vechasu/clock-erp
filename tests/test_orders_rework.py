import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import SalesInventory
from app.services.shared_catalog import SharedCatalog


class OrdersReworkTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def test_status_transition_matrix_accepts_refusal_as_terminal(self):
        for current in ("N", "A", "D"):
            for target in ("N", "A", "D", "C"):
                self.assertTrue(
                    web.validate_order_status_transition(current, target)
                )
        self.assertFalse(web.validate_order_status_transition("C", "N"))
        self.assertTrue(web.validate_order_status_transition("C", "C"))

    def test_route_rejects_unknown_status_before_bitrix_write(self):
        with (
            mock.patch.object(web, "order_status_service") as service,
            mock.patch.object(web, "update_order_status") as update,
        ):
            response = self.client.post(
                "/order/7/status", data={"csrf_token": "test-token", "status": "X"}
            )
        self.assertIn("допустимый статус", parse_qs(urlsplit(response.location).query)["message"][0])
        service.assert_not_called()
        update.assert_not_called()

    def test_tracking_is_validated_saved_and_audited(self):
        saved = {}
        journal = mock.Mock()
        with (
            mock.patch.object(web, "get_order", return_value={"id": "7"}),
            mock.patch.object(web, "load_order_overrides", return_value={}),
            mock.patch.object(web, "save_order_overrides", side_effect=lambda rows: saved.update(rows)),
            mock.patch.object(web, "AuditJournal", return_value=journal),
        ):
            response = self.client.post(
                "/order/7/tracking",
                data={"csrf_token": "test-token", "tracking": "  TRACK-7  "},
            )
        self.assertEqual(saved["7"]["tracking"], "TRACK-7")
        self.assertEqual(parse_qs(urlsplit(response.location).query)["notice"], ["success"])
        journal.record.assert_called_once()

    def test_tracking_validation_preserves_submitted_value(self):
        tracking = "T" * 256
        response = self.client.post(
            "/order/7/tracking",
            data={"csrf_token": "test-token", "tracking": tracking},
        )
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query["notice"], ["error"])
        self.assertEqual(query["tracking_value"], [tracking])

    def test_readiness_aggregates_duplicate_lines_for_stock(self):
        order = {
            "status": "A",
            "products": [
                {"id": "line-1", "product_id": "bx-1", "quantity": 2},
                {"id": "line-2", "product_id": "bx-1", "quantity": 2},
            ],
        }
        mapped = {"state": "mapped", "product": {"id": "1", "stock": 3}}
        mapping = {"line:line-1": mapped, "line:line-2": mapped}
        result = web.build_order_sale_readiness(order, mapping)
        self.assertFalse(result["ready"])
        self.assertIn("Недостаточно остатка", result["issues"])

    def test_unknown_status_and_three_versioned_modes_are_rendered(self):
        order = {
            "id": "7", "number": "7", "status": "?",
            "status_name": "Неизвестный статус", "products": [],
            "sync_state": "partial", "sync_missing": ["items"],
        }
        with (
            mock.patch.object(web, "get_orders", return_value=[order]),
            mock.patch.object(web, "get_order", return_value=order),
            mock.patch.object(web, "load_order_product_mappings", return_value={}),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
            mock.patch.object(web, "get_order_conducted_sale", return_value=None),
        ):
            html = self.client.get("/order/7").get_data(as_text=True)
        self.assertIn("Неизвестный статус", html)
        self.assertNotIn(">?</span>", html)
        for mode in ("list", "split", "card"):
            self.assertIn('data-layout-mode="{}"'.format(mode), html)
        self.assertIn("vechasu:orders:view:v2", html)
        styles = (
            Path(__file__).resolve().parents[1]
            / "app/static/css/orders.css"
        ).read_text(encoding="utf-8")
        self.assertIn("@media (max-width:780px)", styles)
        self.assertNotIn("Свернуть список", html)
        self.assertNotIn("data-collapse-list", html)
        self.assertIn('data-has-selected-order="1"', html)
        self.assertNotIn("window.confirm", html)
        self.assertNotIn("orderStatusConfirm", html)
        self.assertNotIn("Отметить собранным", html)

    def test_mobile_list_page_does_not_force_first_order_card(self):
        order = {"id": "7", "number": "7", "status": "N", "products": []}
        with (
            mock.patch.object(web, "get_orders", return_value=[order]),
            mock.patch.object(web, "get_order", return_value=order),
            mock.patch.object(web, "load_order_product_mappings", return_value={}),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
            mock.patch.object(web, "get_order_conducted_sale", return_value=None),
        ):
            html = self.client.get("/app/orders").get_data(as_text=True)
        self.assertIn('data-has-selected-order="0"', html)

    def test_incomplete_calculation_blocks_sale_before_inventory_change(self):
        order = {
            "id": "8", "number": "8", "status": "A", "sync_state": "complete",
            "calculation_complete": False, "calculation_consistent": False,
            "products": [{"id": "line", "product_id": "bx", "name": "Часы", "quantity": 1, "price": 1000}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            database = CatalogDatabase(Path(temporary) / "catalog.db")
            database.initialize()
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO catalog_excel_batches (id, file_sha256, source_filename, row_count, total_stock, positive_rows, zero_rows, status, created_at, applied_at) "
                    "VALUES ('batch', 'hash', 'orders.xlsx', 0, 0, 0, 0, 'active', '2026-08-18', '2026-08-18')"
                )
            product = ExcelProductCatalog(database).create_product(
                name="Часы", article="SKU", brand="Brand", category="Часы", stock=2,
            )
            inventory = SalesInventory(database)
            with (
                mock.patch.object(web, "get_order", return_value=order),
                mock.patch.object(web, "load_order_product_mappings", return_value={"line:line": {"product_id": str(product["id"])}}),
                mock.patch.object(web, "SharedCatalog", return_value=SharedCatalog(database)),
                mock.patch.object(web, "SalesInventory", return_value=inventory),
            ):
                response = self.client.post("/order/8/stock-writeoff", data={"csrf_token": "test-token"})
            message = parse_qs(urlsplit(response.location).query)["message"][0]
            self.assertIn("полный расчёт доставки и скидки", message)
            self.assertEqual(inventory.list_sales(), [])
            self.assertEqual(ExcelProductCatalog(database).get_product(product["id"])["stock"], 2)


if __name__ == "__main__":
    unittest.main()
