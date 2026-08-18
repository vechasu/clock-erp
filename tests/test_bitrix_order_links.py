import unittest
from unittest import mock

from app import web


BITRIX_ORDER_URL = (
    "https://www.tictactoy.ru/bitrix/admin/"
    "sale_order_view.php?ID=18593&lang=ru"
)


class BitrixOrderLinksTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def test_order_route_renders_internal_card_with_bitrix_action(self):
        order = {
            "id": "18593",
            "number": "18593",
            "status": "A",
            "customer": "Иван Иванов",
            "products": [],
        }
        with (
            mock.patch.object(web, "get_orders", return_value=[order]),
            mock.patch.object(web, "get_order", return_value=order),
            mock.patch.object(web, "get_warehouse_items", return_value=[]),
            mock.patch.object(web, "load_product_mappings", return_value={}),
        ):
            response = self.client.get("/order/18593")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Заказ №18593", html)
        self.assertIn(BITRIX_ORDER_URL.replace("&", "&amp;"), html)
        self.assertIn("Открыть в Bitrix", html)

    def test_order_id_is_normalized_and_invalid_values_are_rejected(self):
        self.assertEqual(web.build_bitrix_order_url("0018593"), BITRIX_ORDER_URL)
        for invalid_id in ("", "0", "-1", "18593&lang=en", "１８５９３", "<script>"):
            with self.subTest(order_id=invalid_id):
                self.assertEqual(web.build_bitrix_order_url(invalid_id), "")

        for path in ("/order/0", "/order/-1", "/order/not-an-id", "/order/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_order_url_builder_does_not_require_str_isascii(self):
        class Python36Text(str):
            def __getattribute__(self, name):
                if name == "isascii":
                    raise AttributeError(name)
                return super().__getattribute__(name)

        with mock.patch.object(
            web,
            "str",
            side_effect=lambda value: Python36Text(value),
            create=True,
        ):
            self.assertEqual(web.build_bitrix_order_url("0018593"), BITRIX_ORDER_URL)

    def test_repair_table_and_card_links_open_safely_in_new_tab(self):
        repair = {
            "id": "repair-1",
            "order_id": "18593",
            "order_number": "18593",
            "status": "new",
            "request_type": "paid_repair",
            "location": "with_customer",
            "communication_channel": "phone",
            "waiting_for": "us",
            "history": [],
            "shipments": [],
        }
        with (
            mock.patch.object(web, "load_repair_cases", return_value=[repair]),
            mock.patch.object(web, "build_repair_catalog_items", return_value=[]),
        ):
            html = self.client.get("/app/repairs").get_data(as_text=True)

        expected_attributes = (
            f'href="{BITRIX_ORDER_URL.replace("&", "&amp;")}" '
            'target="_blank" rel="noopener noreferrer"'
        )
        self.assertIn(expected_attributes, html)
        self.assertIn(
            'href="${escapeHtml(repair.order_url)}" target="_blank" '
            'rel="noopener noreferrer"',
            html,
        )

    def test_invalid_repair_order_id_does_not_form_link(self):
        prepared = web.prepare_repair_case({
            "order_id": "18593&lang=en",
            "order_number": "18593",
            "history": [],
            "shipments": [],
        })
        self.assertEqual(prepared["order_url"], "")

    def test_missing_order_returns_not_found(self):
        with (
            mock.patch.object(web, "get_orders", return_value=[]),
            mock.patch.object(web, "get_order", return_value=None),
        ):
            response = self.client.get("/order/18593")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
