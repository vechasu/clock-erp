import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import web


class InternalOrdersTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()
        self.orders = [
            {
                "id": "18593",
                "number": "18593",
                "status": "A",
                "customer": "Иван Иванов",
                "phone": "+7 900 000-00-00",
                "price": 12990,
                "products": [{"id": "204699", "name": "Часы", "quantity": 1, "price": 12990}],
            },
            {
                "id": "18594",
                "number": "18594",
                "status": "N",
                "customer": "Анна Петрова",
                "products": [],
            },
        ]

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def get_orders_page(self, path="/app/orders", orders=None):
        with (
            mock.patch.object(web, "get_orders", return_value=self.orders if orders is None else orders),
            mock.patch.object(web, "build_order_product_mapping_context", return_value={}),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
            mock.patch.object(web, "get_order_conducted_sale", return_value=None),
        ):
            return self.client.get(path)

    def test_internal_orders_page_and_legacy_url_open_without_redirect(self):
        for path in ("/app/orders", "/orders"):
            with self.subTest(path=path):
                response = self.get_orders_page(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    "Управление заказами интернет-магазина",
                    response.get_data(as_text=True),
                )

    def test_orders_workspace_does_not_fetch_bitrix_detail(self):
        with (
            mock.patch.object(web, "get_orders", return_value=self.orders),
            mock.patch.object(web, "get_order") as get_order,
            mock.patch.object(web, "build_order_product_mapping_context", return_value={}),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
            mock.patch.object(web, "get_order_conducted_sale", return_value=None),
        ):
            response = self.client.get("/app/orders")

        self.assertEqual(response.status_code, 200)
        get_order.assert_not_called()

    def test_orders_workspace_reads_durable_snapshot_without_network(self):
        original_cache = dict(web.ORDERS_CACHE)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "orders_cache.json"
                path.write_text(
                    '{"items":[{"id":"18593","status":"A"}],'
                    '"loaded_at":%s}' % time.time(),
                    encoding="utf-8",
                )
                web.ORDERS_CACHE.update(items=[], loaded_at=0, error="")
                with (
                    mock.patch.object(web, "get_orders_cache_path", return_value=path),
                    mock.patch.object(web, "schedule_orders_refresh") as schedule,
                    mock.patch.object(web.requests, "get") as request_get,
                ):
                    orders = web.get_orders()

            self.assertEqual(orders[0]["id"], "18593")
            request_get.assert_not_called()
            schedule.assert_not_called()
        finally:
            web.ORDERS_CACHE.clear()
            web.ORDERS_CACHE.update(original_cache)

    def test_list_search_and_status_filters_are_restored(self):
        html = self.get_orders_page().get_data(as_text=True)

        self.assertIn("Заказ №18593", html)
        self.assertIn("Иван Иванов", html)
        self.assertIn('id="orderSearch"', html)
        for status in ("all", "N", "A", "D"):
            self.assertIn('data-filter="{}"'.format(status), html)
        for removed_status in ("T", "C"):
            self.assertNotIn(
                'data-filter="{}"'.format(removed_status), html
            )
        self.assertIn("async function loadOrdersResults", html)
        self.assertIn("ordersSearchController?.abort()", html)
        self.assertIn("requestId!==ordersSearchRequest", html)

    def test_detail_route_keeps_server_pagination_and_three_status_kpis(self):
        orders = [
            {
                "id": str(index), "number": str(index),
                "status": ("N", "A", "D")[index % 3], "products": [],
            }
            for index in range(1, 46)
        ]
        with (
            mock.patch.object(web, "get_orders", return_value=orders),
            mock.patch.object(web, "get_order", return_value=orders[0]),
            mock.patch.object(web, "build_order_product_mapping_context", return_value={}),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
            mock.patch.object(web, "get_order_conducted_sale", return_value=None),
        ):
            html = self.client.get("/order/1?page=2").get_data(as_text=True)

        self.assertEqual(
            html.count('class="order-row"')
            + html.count('class="order-row active"'),
            20,
        )
        self.assertIn("Найдено: 45", html)
        self.assertIn("Страница 2 из 3", html)
        self.assertIn('aria-label="Заказов на странице"', html)
        self.assertNotIn("Всего заказов", html)
        self.assertEqual(
            html.count('class="erp-stat-value">15</strong>'), 3
        )

    def test_order_without_bitrix_id_does_not_break_the_list(self):
        response = self.get_orders_page(orders=[{
            "number": "Без внешнего ID",
            "status": "N",
            "customer": "Клиент",
            "products": [],
        }])

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Без внешнего ID", html)
        self.assertIn('href="#"', html)
        self.assertNotIn("Открыть в Bitrix", html)

    def test_navigation_points_to_internal_orders(self):
        orders_item = next(
            item for item in web.NAVIGATION_DEFINITIONS
            if item["key"] == "orders"
        )
        self.assertEqual(orders_item["href"], "/app/orders")
        self.assertNotIn("/orders", web.LEGACY_FRONTEND_REDIRECTS)

    def test_orders_keep_csrf_protection_and_sales_route_is_separate(self):
        html = self.get_orders_page().get_data(as_text=True)
        self.assertIn('name="csrf_token"', html)
        self.assertNotEqual(web.orders_page, web.sales_page)
        rules = {rule.rule: rule.endpoint for rule in web.app.url_map.iter_rules()}
        self.assertEqual(rules["/app/orders"], "orders_page")
        self.assertEqual(rules["/app/sales"], "sales_page")

    def test_orders_route_keeps_global_auth_protection(self):
        web.app.config["AUTH_TESTING"] = True
        response = self.client.get("/app/orders")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/app/orders", response.location)


if __name__ == "__main__":
    unittest.main()
