import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from app import web


class OrderTictactoySaleTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()
        self.order = {
            "id": "18593",
            "number": "18593",
            "status": "A",
            "customer": "Иван Иванов",
            "phone": "+7 900 000-00-00",
            "order_total": 17400,
            "created_at": "2026-08-18 10:00",
            "products": [
                {"id": "b1", "name": "Часы", "quantity": 2, "price": 7500},
                {"id": "b2", "name": "Ремешок", "quantity": 1, "price": 2400},
            ],
        }
        self.mappings = {
            "b1": {"moysklad_product_id": "m1", "moysklad_product_name": "Часы"},
            "b2": {"moysklad_product_id": "m2", "moysklad_product_name": "Ремешок"},
        }
        self.warehouse = [
            {"id": "m1", "name": "Часы", "stock": 5},
            {"id": "m2", "name": "Ремешок", "stock": 3},
        ]

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def render_order(self, query=""):
        with (
            mock.patch.object(web, "get_orders", return_value=[self.order]),
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "get_warehouse_items", return_value=self.warehouse),
            mock.patch.object(web, "load_product_mappings", return_value=self.mappings),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
        ):
            return self.client.get("/order/18593" + query)

    def conduct(self, order=None, mappings=None, warehouse=None, operations=None):
        saved = []
        client = mock.Mock()
        client.create_stock_loss.side_effect = [
            {"id": "loss-1", "name": "LOSS-1"},
            {"id": "loss-2", "name": "LOSS-2"},
        ]
        with (
            mock.patch.object(web, "get_order", return_value=order or self.order),
            mock.patch.object(web, "load_product_mappings", return_value=mappings if mappings is not None else self.mappings),
            mock.patch.object(web, "get_warehouse_items", return_value=warehouse if warehouse is not None else self.warehouse),
            mock.patch.object(web, "load_stock_operations", return_value=list(operations or [])),
            mock.patch.object(web, "save_stock_operations", side_effect=lambda value: saved.extend(value)),
            mock.patch.object(web, "MoySkladClient", return_value=client),
        ):
            response = self.client.post(
                "/order/18593/stock-writeoff",
                data={"csrf_token": "test-token"},
            )
        return response, saved, client

    def test_confirmed_status_opens_sale_dialog_on_return(self):
        with (
            mock.patch.object(web, "update_order_status", return_value={"status": "ok"}) as update,
            mock.patch.object(web, "get_orders", return_value=[self.order]),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
        ):
            response = self.client.post(
                "/order/18593/status",
                data={"status": "A", "csrf_token": "test-token"},
            )

        self.assertEqual(response.status_code, 302)
        update.assert_called_once_with(18593, "A")
        self.assertEqual(parse_qs(urlsplit(response.location).query)["open_sale"], ["1"])

    def test_bitrix_error_does_not_open_sale_dialog(self):
        with (
            mock.patch.object(web, "update_order_status", return_value={
                "status": "error", "message": "Bitrix недоступен",
            }),
            mock.patch.object(web, "get_orders", return_value=[self.order]),
        ):
            response = self.client.post(
                "/order/18593/status",
                data={"status": "A", "csrf_token": "test-token"},
            )

        query = parse_qs(urlsplit(response.location).query)
        self.assertNotIn("open_sale", query)
        self.assertEqual(query["message"], ["Bitrix недоступен"])

    def test_dialog_auto_opens_and_manual_button_reopens_it(self):
        html = self.render_order("?open_sale=1").get_data(as_text=True)

        self.assertIn('id="orderSaleModal"', html)
        self.assertIn('data-auto-open="1"', html)
        self.assertIn("Провести продажу", html)
        self.assertIn("TicTacToy", html)
        self.assertIn("Иван Иванов", html)
        self.assertIn("Часы — 2 шт.", html)
        self.assertIn("openModal(openButtons[0] || null)", html)
        self.assertIn("[data-open-sale-dialog]", html)

    def test_closing_dialog_cannot_submit_sale_and_csrf_is_preserved(self):
        html = self.render_order().get_data(as_text=True)

        self.assertIn('type="button" data-close-sale-dialog', html)
        self.assertIn("event.target === modal", html)
        self.assertIn('name="csrf_token"', html)
        self.assertIn("submit.disabled = true", html)
        self.assertIn("@media (max-width: 480px)", html)
        self.assertIn('event.key === "Tab"', html)

    def test_success_creates_tictactoy_sale_lines_and_redirects(self):
        response, saved, client = self.conduct()

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(urlsplit(response.location).path, "/sales")
        self.assertEqual(query["source"], ["tictactoy"])
        self.assertEqual(query["message"], ["Заказ №18593 проведён в продажу"])
        self.assertEqual(client.create_stock_loss.call_count, 2)
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(item["source"] == "Заказ Битрикс" for item in saved))
        self.assertTrue(all(item["sales_source"] == "tictactoy" for item in saved))

        sales = web.build_sales_report_records(
            warehouse_items=self.warehouse,
            operations=saved,
            stored_manual_sales=[],
            automatic_overrides={},
        )
        self.assertEqual(len(sales), 2)
        self.assertTrue(all(item["source_key"] == "tictactoy" for item in sales))
        self.assertEqual(sum(item["total_amount"] for item in sales), 17400)
        self.assertEqual(web.calculate_sales_kpis(sales)["sales_count"], 1)

    def test_repeated_post_does_not_write_or_create_duplicate(self):
        existing = [{
            "id": "existing", "source": "Заказ Битрикс",
            "type": "writeoff", "order_id": "18593",
        }]
        response, saved, client = self.conduct(operations=existing)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "уже проведена",
            parse_qs(urlsplit(response.location).query)["message"][0],
        )
        self.assertEqual(saved, [])
        client.create_stock_loss.assert_not_called()

    def test_unconfirmed_order_cannot_be_conducted(self):
        order = dict(self.order)
        order["status"] = "N"
        response, saved, client = self.conduct(order=order)

        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query["message"], ["Сначала подтвердите заказ"])
        self.assertEqual(saved, [])
        client.create_stock_loss.assert_not_called()

    def test_all_unmapped_products_block_before_moysklad(self):
        response, saved, client = self.conduct(mappings={})

        self.assertIn("%D0%A7%D0%B0%D1%81%D1%8B", response.location)
        self.assertIn("%D0%A0%D0%B5%D0%BC%D0%B5%D1%88%D0%BE%D0%BA", response.location)
        self.assertEqual(saved, [])
        client.create_stock_loss.assert_not_called()

    def test_aggregate_shortage_blocks_entire_sale(self):
        order = dict(self.order)
        order["products"] = [
            {"id": "b1", "name": "Часы A", "quantity": 3},
            {"id": "b1", "name": "Часы B", "quantity": 3},
        ]
        response, saved, client = self.conduct(order=order)

        query = parse_qs(urlsplit(response.location).query)
        self.assertIn("нужно 6, есть 5", query["message"][0])
        self.assertEqual(saved, [])
        client.create_stock_loss.assert_not_called()

    def test_empty_order_is_rejected(self):
        order = dict(self.order)
        order["products"] = []
        response, saved, client = self.conduct(order=order)

        self.assertIn(
            "без товаров",
            parse_qs(urlsplit(response.location).query)["message"][0],
        )
        self.assertEqual(saved, [])
        client.create_stock_loss.assert_not_called()

    def test_moysklad_error_does_not_create_local_sale(self):
        saved = []
        client = mock.Mock()
        client.create_stock_loss.side_effect = RuntimeError("API unavailable")
        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "load_product_mappings", return_value=self.mappings),
            mock.patch.object(web, "get_warehouse_items", return_value=self.warehouse),
            mock.patch.object(web, "load_stock_operations", return_value=[]),
            mock.patch.object(web, "save_stock_operations", side_effect=lambda value: saved.extend(value)),
            mock.patch.object(web, "MoySkladClient", return_value=client),
        ):
            response = self.client.post("/order/18593/stock-writeoff")

        query = parse_qs(urlsplit(response.location).query)
        self.assertIn("Не удалось провести продажу", query["message"][0])
        self.assertEqual(saved, [])

    def test_completed_sale_disables_reopening(self):
        with (
            mock.patch.object(web, "get_orders", return_value=[self.order]),
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "get_warehouse_items", return_value=self.warehouse),
            mock.patch.object(web, "load_product_mappings", return_value=self.mappings),
            mock.patch.object(web, "is_order_stock_written_off", return_value=True),
        ):
            html = self.client.get("/order/18593?open_sale=1").get_data(as_text=True)

        self.assertIn("Продажа уже проведена", html)
        self.assertNotIn('id="orderSaleModal"', html)


if __name__ == "__main__":
    unittest.main()
