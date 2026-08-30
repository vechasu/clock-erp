import unittest
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse
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

    def render_selected_order(self, path="/order/7"):
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
        second_summary = dict(summary, id="8", number="8008", customer="Другой клиент")
        second_detail = dict(detail, id="8", number="8008", customer="Другой клиент")
        mapping = {
            "line:line-1": {
                "state": "unmapped",
                "state_label": "Не сопоставлен",
                "product": None,
            },
        }
        with (
            mock.patch.object(web, "get_orders", return_value=[summary, second_summary]),
            mock.patch.object(
                web,
                "get_order",
                side_effect=lambda order_id: detail if int(order_id) == 7 else second_detail,
            ),
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
            return self.client.get(path).get_data(as_text=True)

    def test_selected_order_comes_from_route_and_moves_with_card(self):
        first = self.render_selected_order("/order/7")
        second = self.render_selected_order("/order/8")

        self.assertIn(
            'class="order-table-row active is-selected" data-order-id="7"', first
        )
        self.assertIn('data-order-id="7" data-order-href=', first)
        self.assertIn('aria-current="true"', first)
        self.assertIn("Заказ №7007", first)
        self.assertNotIn(
            'class="order-table-row active is-selected" data-order-id="8"', first
        )

        self.assertIn(
            'class="order-table-row active is-selected" data-order-id="8"', second
        )
        self.assertIn("Заказ №8008", second)
        self.assertNotIn(
            'class="order-table-row active is-selected" data-order-id="7"', second
        )

    def test_order_links_keep_all_query_parameters(self):
        html = self.render_selected_order(
            "/order/7?page=3&page_size=50&q=Другой&status=N&period=30d"
            "&source=tictactoy&sort=created_at&direction=desc"
        )
        row = html.split('data-order-id="8"', 1)[1].split("</tr>", 1)[0]
        href = unescape(row.split('data-order-href="', 1)[1].split('"', 1)[0])
        parsed = urlparse(href)
        self.assertEqual(parsed.path, "/order/8")
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "page": ["3"], "page_size": ["50"], "q": ["Другой"],
                "status": ["N"], "period": ["30d"],
                "source": ["tictactoy"], "sort": ["created_at"],
                "direction": ["desc"],
            },
        )

    def test_navigation_script_keeps_route_selection_modes_and_history(self):
        for expected in (
            "const selectedOrderId=",
            "window.location.pathname.match",
            "window.location.assign(row.dataset.orderHref)",
            "window.addEventListener('popstate'",
            "new URL(window.location.href)",
            "localStorage.setItem(storageKey,safe)",
            "localStorage.getItem(storageKey)",
            "target.searchParams.set('selected_id',selectedOrderId)",
        ):
            self.assertIn(expected, self.source)

    def test_list_route_does_not_select_first_order_implicitly(self):
        html = self.render_selected_order("/app/orders")
        self.assertIn('data-has-selected-order="0"', html)
        self.assertNotIn('aria-current="true"', html)

    def test_selected_order_row_keeps_complete_information(self):
        html = self.render_selected_order()
        active_row = html.split('class="order-table-row active is-selected"', 1)[1].split(
            "</tr>", 1
        )[0]
        for expected in (
            "№7007",
            "Очень длинное имя клиента",
            "+7 900 000-00-00",
            'data-amount="12990"',
            'data-date="2026-08-21 10:30"',
            "Не подтверждён",
        ):
            self.assertIn(expected, active_row)

    def test_order_number_never_uses_ellipsis_and_badges_can_wrap(self):
        html = self.render_selected_order()
        active_row = html.split('class="order-table-row active is-selected"', 1)[1].split(
            "</tr>", 1
        )[0]
        self.assertIn('class="order-number"', active_row)
        self.assertIn("№7007", active_row)

        number_rule = self.source.split(".order-number {", 1)[1].split("}", 1)[0]
        self.assertIn("flex:0 0 auto", number_rule)
        self.assertIn("max-width:100%", number_rule)
        self.assertIn("color:var(--blue)", number_rule)
        self.assertIn("overflow-wrap:anywhere", number_rule)
        self.assertNotIn("overflow:hidden", number_rule)
        self.assertNotIn("text-overflow:ellipsis", number_rule)

        self.assertIn(".orders-table {", self.source)
        self.assertIn("overflow-wrap:anywhere", self.source)
        self.assertNotIn(
            ".orders-page .order-number,\n.orders-page .order-meta span",
            self.source,
        )

    def test_filters_include_source_and_no_find_button(self):
        filters = self.source.split(
            'class="orders-command-bar"', 1
        )[1].split("</form>", 1)[0]
        self.assertIn('class="field field-search erp-search-input"', filters)
        self.assertIn('class="status-filter-tabs"', filters)
        self.assertEqual(filters.count("data-auto-submit-filter"), 2)
        self.assertEqual(filters.count('<select class="field"'), 2)
        self.assertIn('name="source"', filters)
        self.assertIn('name="status"', filters)
        self.assertIn('name="period"', filters)
        self.assertIn('data-search-clear', filters)
        self.assertIn('data-status-filter="all"', filters)
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
        self.assertIn(".orders-command-bar", styles)
        self.assertIn(".status-filter.active", styles)
        self.assertIn("@media (max-width:780px)", styles)

    def test_shared_erp_foundation_compact_filters_and_order_context_are_rendered(self):
        html = self.render_selected_order()
        self.assertIn("css/erp-components.css", html)
        self.assertIn("css/orders.css", html)
        self.assertIn('class="orders-command-bar"', html)
        for label in ("Не подтверждены", "Подтверждены", "Собраны"):
            self.assertIn(label, html)
        self.assertNotIn('class="orders-kpis erp-workspace-metrics"', html)
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

    def test_order_header_is_compact_and_actions_menu_keeps_existing_urls(self):
        header = self.source.split('class="card-head order-card-head"', 1)[1].split(
            '</header>', 1
        )[0]
        self.assertIn('class="order-card-summary"', header)
        self.assertIn('class="actions order-card-actions"', header)
        self.assertIn('class="sale-completed-badge"', header)
        self.assertNotIn('class="button button-secondary" aria-label="Продажа проведена"', header)
        self.assertEqual(header.count('data-order-actions-trigger'), 1)
        menu = header.split('data-order-actions-dropdown', 1)[1]
        labels = (
            "Создать задачу", "Отправить SMS", "История SMS",
            "Открыть в Bitrix", "Печать заказа",
        )
        positions = [menu.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("url_for('tasks_page'", menu)
        self.assertIn("url_for('sms_page'", menu)
        self.assertIn("selected_order_bitrix_url", menu)
        self.assertIn("url_for('order_print_page'", menu)
        self.assertIn('data-status-autosave', header)
        self.assertIn('data-order-sale-action', header)

    def test_actions_menu_keyboard_and_responsive_contract(self):
        self.assertIn("event.key==='ArrowDown'||event.key==='ArrowUp'", self.source)
        self.assertIn("event.key==='Escape'", self.source)
        self.assertIn("!orderActions.contains(event.target)", self.source)
        self.assertIn("position: fixed", self.source)
        self.assertIn("@media (max-width: 900px)", self.source)
        self.assertIn("@media (max-width: 560px)", self.source)
        self.assertIn("grid-template-columns: 1fr 1fr", self.source)

    def test_order_task_block_exposes_context_create_and_safe_delete_ui(self):
        script = (PROJECT_ROOT / "app/static/js/entity-tasks.js").read_text(encoding="utf-8")
        styles = (PROJECT_ROOT / "app/static/css/entity-tasks.css").read_text(encoding="utf-8")
        self.assertIn('data-entity-label="Заказ №{{ detail_number }}"', self.source)
        self.assertIn("Создать задачу", script)
        self.assertIn("Удалить задачу?", script)
        self.assertIn("исчезнет из карточки заказа", script)
        self.assertIn('method: "DELETE"', script)
        self.assertIn('"X-CSRF-Token"', script)
        self.assertIn("Задача удалена", script)
        self.assertIn("В этом заказе пока нет задач", script)
        self.assertIn("confirm.disabled = true", script)
        self.assertIn("event.key === \"Escape\"", script)
        self.assertIn("position: fixed", styles)

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
