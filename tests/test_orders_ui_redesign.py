import unittest
from pathlib import Path
from unittest import mock

from app import web


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OrdersUiRedesignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        template = (
            PROJECT_ROOT / "app/templates/orders.html"
        ).read_text(encoding="utf-8")
        styles = (
            PROJECT_ROOT / "app/static/css/orders.css"
        ).read_text(encoding="utf-8")
        cls.source = template + "\n" + styles

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
            "Не подтверждён",
        ):
            self.assertIn(expected, active_row)

    def test_order_number_never_uses_ellipsis_and_badges_can_wrap(self):
        html = self.render_selected_order()
        active_row = html.split('class="order-row active"', 1)[1].split(
            "</a>", 1
        )[0]
        self.assertIn('<span class="order-number">Заказ №7007</span>', active_row)

        number_rule = self.source.split(".order-number {", 1)[1].split("}", 1)[0]
        self.assertIn("flex:0 0 auto", number_rule)
        self.assertIn("max-width:100%", number_rule)
        self.assertIn("color:var(--blue)", number_rule)
        self.assertIn("overflow-wrap:anywhere", number_rule)
        self.assertNotIn("overflow:hidden", number_rule)
        self.assertNotIn("text-overflow:ellipsis", number_rule)

        badges_rule = self.source.split(".order-row-badges {", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn("flex:1 1 160px", badges_rule)
        self.assertIn("flex-wrap:wrap", badges_rule)
        self.assertIn("min-width:0", badges_rule)
        self.assertNotIn(
            ".orders-page .order-number,\n.orders-page .order-meta span",
            self.source,
        )

    def test_filters_include_source_and_no_find_button(self):
        filters = self.source.split(
            'class="panel-toolbar filters erp-toolbar"', 1
        )[1].split("</form>", 1)[0]
        self.assertIn('class="field field-search erp-search-input"', filters)
        self.assertIn('class="filter-selects"', filters)
        self.assertEqual(filters.count("data-auto-submit-filter"), 3)
        self.assertEqual(filters.count('<select class="field"'), 3)
        self.assertIn('name="source"', filters)
        self.assertIn('name="status"', filters)
        self.assertIn('name="period"', filters)
        self.assertNotIn("Свернуть список", filters)
        self.assertNotIn("data-collapse-list", self.source)
        self.assertNotIn(">Найти<", filters)
        self.assertIn("url_for('orders_page')", filters)
        self.assertIn("loadOrdersResults(ordersFilterUrl())", self.source)
        self.assertIn("event.key==='Enter'", self.source)
        self.assertIn("ordersSearchController?.abort()", self.source)

        styles = (
            Path(__file__).resolve().parents[1]
            / "app/static/css/orders.css"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "grid-template-columns:repeat(3,minmax(0,1fr))", styles
        )
        self.assertIn("@media (max-width: 960px)", styles)

    def test_shared_erp_foundation_kpis_and_order_context_are_rendered(self):
        html = self.render_selected_order()
        self.assertIn("css/erp-components.css", html)
        self.assertIn("css/orders.css", html)
        self.assertIn('class="orders-kpis erp-workspace-metrics"', html)
        for label in ("Не подтверждены", "Подтверждены", "Собраны"):
            self.assertIn(label, html)
        self.assertNotIn("Всего заказов", html)
        for label in (
            "Способ доставки", "Способ оплаты", "Статус оплаты",
            "Комментарий покупателя", "Внутренние комментарии",
        ):
            self.assertIn(label, html)

    def test_pending_guard_covers_status_tracking_and_sale_actions(self):
        self.assertIn("function setPending(form,label)", self.source)
        self.assertIn("form.dataset.submitting==='1'", self.source)
        self.assertIn("data-status-autosave", self.source)
        self.assertIn("async function saveRequested()", self.source)
        self.assertIn("while(requested!==saved)", self.source)
        self.assertIn("[data-tracking-form]')?.addEventListener('submit'", self.source)
        self.assertIn("[data-order-sale-form]')?.addEventListener('submit'", self.source)

    def test_internal_comments_are_compact_append_only_and_have_shortcuts(self):
        comments = self.source.split(
            'class="section order-comments"', 1
        )[1].split('<section class="section">', 1)[0]
        self.assertIn("Видны только сотрудникам", comments)
        self.assertIn('rows="3"', comments)
        self.assertIn("Добавить внутренний комментарий…", comments)
        self.assertIn("event.ctrlKey||event.metaKey", self.source)
        self.assertIn("employeeCommentForm.requestSubmit()", self.source)
        self.assertNotIn("Удалить комментарий", comments)
        self.assertIn("Редактировать", comments)

    def test_order_card_uses_two_columns_and_escapes_comment_text(self):
        self.assertIn(
            "grid-template-columns:minmax(0,1.65fr) minmax(280px,1fr)",
            self.source,
        )
        for section in (
            "order-products", "order-information", "order-delivery",
            "order-payment", "order-comments",
        ):
            self.assertIn(section, self.source)
        self.assertIn("@container (max-width:760px)", self.source)
        with mock.patch.object(web, "load_order_comments", return_value=[{
            "id": 1,
            "text": "<script>alert(1)</script>",
            "author_name": "Bitrix",
            "author_user_id": None,
            "created_at": "2026-08-24T12:00:00+00:00",
            "updated_at": "2026-08-24T12:00:00+00:00",
            "source": "bitrix_legacy",
            "sync_status": "synced",
        }]):
            html = self.render_selected_order()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)

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
        self.assertIn("minmax(0,63%)", self.source)
        self.assertIn("min-width:0", self.source)
        self.assertIn("Проведение недоступно:", html)
        self.assertIn("Заказ не подтверждён · Есть несопоставленные позиции", html)


if __name__ == "__main__":
    unittest.main()
