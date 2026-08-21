import unittest
from pathlib import Path
from unittest import mock

from app import web


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OrdersUiRedesignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            PROJECT_ROOT / "app/templates/orders.html"
        ).read_text(encoding="utf-8")

    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    def render_selected_order(self):
        summary = {
            "id": "7",
            "number": "7007",
            "status": "N",
            "status_name": "Новый",
            "customer": "Очень длинное имя клиента для проверки строки",
            "phone": "+7 900 000-00-00",
            "order_total": 12990,
            "created_at": "2026-08-21 10:30",
            "products": [],
        }
        detail = dict(summary)
        detail.update({
            "sync_state": "complete",
            "products_total": 12990,
            "delivery_price": 0,
            "address": "Тамбов, Советская улица, 1",
            "region": "468 · Тамбовская область",
            "products": [{
                "basket_id": "line-1",
                "product_id": "bx-1",
                "name": "Часы с длинным названием",
                "quantity": 1,
                "price": 12990,
            }],
        })
        mapping = {
            "line:line-1": {
                "state": "unmapped",
                "state_label": "Не сопоставлен",
                "product": None,
            },
        }
        with (
            mock.patch.object(web, "get_orders", return_value=[summary]),
            mock.patch.object(web, "get_order", return_value=detail),
            mock.patch.object(web, "load_order_product_mappings", return_value={}),
            mock.patch.object(
                web,
                "build_catalog_product_order_counts",
                return_value={},
            ),
            mock.patch.object(
                web,
                "build_order_product_mapping_context",
                return_value=mapping,
            ),
            mock.patch.object(web, "is_order_stock_written_off", return_value=False),
            mock.patch.object(web, "get_order_conducted_sale", return_value=None),
            mock.patch.object(
                web,
                "build_order_sale_readiness",
                return_value={
                    "ready": False,
                    "issues": [
                        "Заказ не подтверждён",
                        "Есть несопоставленные позиции",
                    ],
                },
            ),
            mock.patch.object(web, "SharedCatalog", return_value=mock.Mock()),
        ):
            return self.client.get("/order/7").get_data(as_text=True)

    def test_selected_order_row_keeps_complete_information(self):
        html = self.render_selected_order()
        active_row = html.split('class="order-row active"', 1)[1].split(
            "</a>", 1
        )[0]
        for expected in (
            "Заказ №7007",
            "Очень длинное имя клиента",
            "+7 900 000-00-00",
            'data-amount="12990"',
            'data-date="2026-08-21 10:30"',
            "Новый",
        ):
            self.assertIn(expected, active_row)

    def test_filters_have_two_rows_and_no_find_button(self):
        filters = self.source.split(
            'class="panel-toolbar filters"', 1
        )[1].split("</form>", 1)[0]
        self.assertIn('class="field field-search"', filters)
        self.assertIn('class="filter-selects"', filters)
        self.assertEqual(filters.count("data-auto-submit-filter"), 2)
        self.assertNotIn(">Найти<", filters)
        self.assertIn("url_for('orders_page')", filters)
        self.assertIn("filter.form?.requestSubmit()", self.source)
        self.assertIn("event.key==='Enter'", self.source)
        self.assertIn("searchInput.form?.requestSubmit()", self.source)

    def test_desktop_mapping_is_one_row_with_flexible_product(self):
        self.assertIn(
            "grid-template-columns:minmax(260px,1fr) max-content",
            self.source,
        )
        self.assertIn('data-catalog-product-global="true"', self.source)
        self.assertNotIn('data-shared-catalog-kind="brand"', self.source)
        self.assertNotIn('data-shared-catalog-kind="category"', self.source)
        self.assertIn("@media (max-width:1100px)", self.source)
        self.assertIn(".order-map-form { grid-template-columns:1fr 1fr; }", self.source)
        self.assertIn("@media (max-width:780px)", self.source)
        self.assertIn(".order-map-form { grid-template-columns:1fr; }", self.source)

    def test_layout_modes_overflow_guards_and_readiness_are_preserved(self):
        html = self.render_selected_order()
        for mode in ("list", "split", "card"):
            self.assertIn('data-layout-mode="{}"'.format(mode), html)
        self.assertNotIn(
            '.view-option[data-layout-mode="split"] { display:none; }',
            html,
        )
        self.assertIn("vechasu:orders:view:v2", html)
        self.assertIn("minmax(0,63%)", html)
        self.assertIn("min-width:0", html)
        self.assertIn("Проведение недоступно:", html)
        self.assertIn("Заказ не подтверждён · Есть несопоставленные позиции", html)


if __name__ == "__main__":
    unittest.main()
