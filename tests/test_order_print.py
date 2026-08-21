import copy
import unittest
from unittest import mock

from app import web
from app.clients.bitrix_orders import BitrixReadOnlyError
from app.services.order_print import (
    amount_in_words,
    build_order_print_context,
    format_money,
    format_order_datetime,
    format_phone,
)


def sample_order(**overrides):
    order = {
        "id": "18593",
        "number": "18593",
        "created_at": "2026-08-21T10:30:00",
        "status": "A",
        "status_name": "Подтверждён",
        "customer": "Иван Иванов",
        "phone": "+7 900 000-00-00",
        "email": "buyer@example.test",
        "delivery": "СДЭК: доставка до ПВЗ",
        "country": "Россия",
        "region": "Москва",
        "city": "Москва",
        "address": "Россия, Москва, Москва, ул. Тверская, д. 1#SMSC1",
        "postal_code": "125009",
        "tracking": "TRACK-18593",
        "comment": "Позвонить перед доставкой",
        "payment": "Оплата в пункте выдачи",
        "paid": "N",
        "products_total": 2101,
        "discount": 100,
        "delivery_price": 399,
        "order_total": 2400,
        "items": [
            {
                "name": "Часы с длинным названием",
                "sku": "WATCH-1",
                "quantity": 2,
                "sale_unit_price": 1000.5,
                "line_total": 2001,
            },
            {
                "name": "Ремешок",
                "quantity": 1,
                "sale_unit_price": 100,
                "line_total": 100,
            },
        ],
    }
    order.update(overrides)
    return order


class OrderPrintTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def get_print(self, order=None):
        with mock.patch.object(web, "get_order", return_value=order or sample_order()):
            return self.client.get("/app/orders/18593/print")

    def test_route_requires_authentication(self):
        web.app.config["AUTH_TESTING"] = True
        response = self.client.get("/app/orders/18593/print")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/app/orders/18593/print", response.location)

    def test_user_without_order_view_permission_gets_403(self):
        with mock.patch.object(web, "can_view_orders", return_value=False), mock.patch.object(
            web, "get_order"
        ) as get_order:
            response = self.client.get("/app/orders/18593/print")
        self.assertEqual(response.status_code, 403)
        get_order.assert_not_called()

    def test_invalid_and_out_of_range_ids_are_safe(self):
        for path in (
            "/app/orders/not-a-number/print",
            "/app/orders/0/print",
            "/app/orders/2147483648/print",
        ):
            with self.subTest(path=path), mock.patch.object(web, "get_order") as get_order:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                get_order.assert_not_called()

    def test_order_not_found_returns_404(self):
        with mock.patch.object(web, "get_order", return_value=None):
            response = self.client.get("/app/orders/18593/print")
        self.assertEqual(response.status_code, 404)

    def test_bitrix_error_is_not_disclosed(self):
        with mock.patch.object(
            web,
            "get_order",
            side_effect=BitrixReadOnlyError("token=super-secret internal-host"),
        ):
            response = self.client.get("/app/orders/18593/print")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Печатная форма временно недоступна", html)
        self.assertNotIn("super-secret", html)
        self.assertNotIn("internal-host", html)

    def test_print_route_is_get_only_and_does_not_mutate_order(self):
        order = sample_order()
        before = copy.deepcopy(order)
        with mock.patch.object(web, "get_order", return_value=order) as get_order:
            response = self.client.get("/app/orders/18593/print")
            post_response = self.client.post("/app/orders/18593/print")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(post_response.status_code, 405)
        self.assertEqual(order, before)
        get_order.assert_called_once_with(18593)

    def test_output_is_escaped_and_optional_values_are_omitted(self):
        response = self.get_print(sample_order(
            customer='<script>alert("x")</script>',
            email=None,
            region=None,
            postal_code=None,
            comment=None,
        ))
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;script&gt;alert", html)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("Эл. почта:</span> —", html)
        self.assertNotIn("Индекс:</span>", html)
        self.assertNotIn(">None<", html)
        self.assertNotIn(">null<", html)

    def test_totals_sku_and_money_formatting(self):
        context = build_order_print_context(sample_order())
        self.assertEqual(format_money(12000), "12 000,00")
        self.assertEqual(format_money("12000.5"), "12 000,50")
        self.assertEqual(
            format_order_datetime("2026-08-21T10:30:00+03:00"),
            "21.08.2026 10:30",
        )
        self.assertEqual(format_phone("8 900 000-00-00"), "+7 (900) 000-00-00")
        self.assertEqual(context["products_total"], "2 101,00")
        self.assertEqual(context["discount"], "100,00")
        self.assertEqual(context["delivery_price"], "399,00")
        self.assertEqual(context["total"], "2 400,00")
        self.assertEqual(context["total_words"], "две тысячи четыреста рублей 00 копеек")
        self.assertEqual(context["delivery_service"], "СДЭК")
        self.assertEqual(context["delivery_method"], "доставка до ПВЗ")
        self.assertEqual(context["address"], "ул. Тверская, д. 1")

    def test_amount_in_words_handles_russian_gender_and_kopecks(self):
        self.assertEqual(amount_in_words("11504"), "одиннадцать тысяч пятьсот четыре рубля 00 копеек")
        self.assertEqual(amount_in_words("21001.02"), "двадцать одна тысяча один рубль 02 копейки")
        self.assertEqual(amount_in_words(0), "ноль рублей 00 копеек")

    def test_many_lines_have_multi_page_print_guards(self):
        items = [
            {
                "name": "Позиция {} ".format(index) + "очень длинное название " * 5,
                "quantity": index + 1,
                "sale_unit_price": 10,
                "line_total": 10 * (index + 1),
            }
            for index in range(60)
        ]
        response = self.get_print(sample_order(items=items, products_total=18300, order_total=18699))
        html = response.get_data(as_text=True)
        self.assertIn("Позиция 0", html)
        self.assertIn("Позиция 59", html)
        self.assertIn("display: table-header-group", html)
        self.assertIn("page-break-inside: avoid", html)
        self.assertIn("@page", html)
        self.assertIn("size: A4 landscape", html)

    def test_print_has_no_erp_controls_and_fiscal_template_is_not_reused(self):
        html = self.get_print().get_data(as_text=True)
        self.assertNotIn("<button", html)
        self.assertNotIn("<nav", html)
        self.assertNotIn("onload=", html.casefold())
        for forbidden in ("ККТ", "ФН:", "ФП", "ФД:", "КАССИР", "СМЕНА"):
            self.assertNotIn(forbidden, html)
        for label in ("Подпись / печать продавца", "QR-код Telegram", "vk.com/tictactoy_ru", "t.me/tictactoy"):
            self.assertIn(label, html)

    def test_orders_card_has_new_tab_print_action(self):
        order = sample_order(sync_state="complete", products=[])
        with (
            mock.patch.object(web, "get_orders", return_value=[order]),
            mock.patch.object(web, "get_order", return_value=order),
            mock.patch.object(web, "load_product_mappings", return_value={}),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
            mock.patch.object(web, "get_order_conducted_sale", return_value=None),
        ):
            html = self.client.get("/order/18593").get_data(as_text=True)
        self.assertIn("Печать заказа", html)
        self.assertIn('href="/app/orders/18593/print"', html)
        self.assertIn('target="_blank"', html)


if __name__ == "__main__":
    unittest.main()
