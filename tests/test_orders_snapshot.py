import json
import tempfile
import unittest
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.domain_schema_migrations import apply_domain_migrations
from app.schema_migrations import apply_migrations
from app.services.orders_snapshot import (
    OrdersSnapshotStore,
    normalize_exact_order_number_query,
)


def order_row(index):
    status = ("N", "A", "D")[index % 3]
    return {
        "id": str(30000 - index),
        "number": "ORDER-{:04d}".format(index),
        "customer": "Иван Петров {:04d}".format(index),
        "phone": "+7 (900) 123-{:02d}-{:02d}".format(index % 100, index % 97),
        "order_total": 10000 + index,
        "created_at": "2026-08-{:02d} 13:30:00".format((index % 23) + 1),
        "status": status,
        "products": [
            {"id": "line-a", "quantity": 2},
            {"id": "line-b", "quantity": 3},
        ],
    }


class OrdersSnapshotStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = OrdersSnapshotStore(
            Path(self.temporary.name) / "orders.db"
        )
        apply_domain_migrations(self.store.path, "orders", "test")
        self.orders = [order_row(index) for index in range(455)]
        self.store.replace(self.orders, 1000)
        self.sorted_orders = sorted(
            self.orders,
            key=lambda row: (row["created_at"], row["id"]),
            reverse=True,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def query(self, **args):
        return self.store.query(args, now=datetime(2026, 8, 23, 15, 0))

    def test_db_total_limit_offset_page_sizes_and_stable_complete_walk(self):
        first = self.query()
        self.assertEqual(first["physical_total"], 455)
        self.assertEqual(first["total"], 455)
        self.assertEqual(first["page_size"], 50)
        self.assertEqual(first["page_count"], 10)
        self.assertEqual(len(first["rows"]), 50)

        for size in (20, 50, 100, 200):
            with self.subTest(size=size):
                state = self.query(page_size=size)
                self.assertEqual(len(state["rows"]), min(size, 455))

        walked = []
        for page in range(1, self.query(page_size=50)["page_count"] + 1):
            walked.extend(
                row["id"] for row in self.query(page=page, page_size=50)["rows"]
            )
        self.assertEqual(len(walked), 455)
        self.assertEqual(len(set(walked)), 455)
        self.assertEqual(walked, [row["id"] for row in self.sorted_orders])

        boundaries = {
            1: self.sorted_orders[:50],
            2: self.sorted_orders[50:100],
            5: self.sorted_orders[200:250],
            10: self.sorted_orders[450:],
        }
        for page, expected in boundaries.items():
            with self.subTest(page=page):
                state = self.query(page=page, page_size=50)
                self.assertEqual(
                    [row["id"] for row in state["rows"]],
                    [row["id"] for row in expected],
                )
        self.assertEqual(self.query(page=999, page_size=50)["page"], 10)

        capped_rows = self.query(page_size="all")
        self.assertEqual(capped_rows["page_size"], 50)
        self.assertEqual(len(capped_rows["rows"]), 50)
        self.assertEqual(capped_rows["page_count"], 10)

    def test_incremental_refresh_retains_history_and_erp_only_payload(self):
        oldest = dict(self.orders[-1])
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM orders_snapshot WHERE order_id=?",
                (oldest["id"],),
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload["erp_private_note"] = "Сохранить"
            connection.execute(
                "UPDATE orders_snapshot SET payload_json=? WHERE order_id=?",
                (json.dumps(payload), oldest["id"]),
            )

        refreshed = dict(oldest, customer="Обновлён Bitrix")
        self.store.replace([refreshed], 1002)

        self.assertEqual(self.store.count(), 455)
        stored = self.store.get(oldest["id"])
        self.assertEqual(stored["customer"], "Обновлён Bitrix")
        self.assertEqual(stored["erp_private_note"], "Сохранить")

    def test_search_includes_product_model_article_and_source(self):
        detailed = dict(self.orders[0])
        detailed.update(source="tictactoy", source_name="Сайт")
        detailed["products"] = [{
            "name": "Часы Север", "model": "Polar 7", "article": "ART-778",
            "quantity": 1,
        }]
        self.store.replace([detailed] + self.orders[1:], 1001)
        for query in ("Часы Север", "Polar 7", "ART-778", "Сайт"):
            with self.subTest(query=query):
                self.assertEqual(self.query(q=query)["total"], 1)

    def test_responsible_filter_is_applied_before_pagination(self):
        assigned = {row["id"] for row in self.orders[25:75]}
        first = self.store.query(
            {"page": 1, "page_size": 20},
            now=datetime(2026, 8, 23, 15, 0),
            allowed_order_ids=assigned,
        )
        third = self.store.query(
            {"page": 3, "page_size": 20},
            now=datetime(2026, 8, 23, 15, 0),
            allowed_order_ids=assigned,
        )

        self.assertEqual((first["total"], first["page_count"]), (50, 3))
        self.assertEqual(len(first["rows"]), 20)
        self.assertEqual(len(third["rows"]), 10)
        self.assertTrue(all(row["id"] in assigned for row in first["rows"] + third["rows"]))

        empty = self.store.query({}, allowed_order_ids=set())
        self.assertEqual((empty["total"], empty["rows"]), (0, []))

    def test_searches_all_required_fields(self):
        target = self.orders[123]
        cases = (
            (target["number"], target["id"]),
            ("ИВАН ПЕТРОВ 0123", target["id"]),
            ("петров 0123", target["id"]),
            (target["phone"], target["id"]),
            ("9001232326", target["id"]),
            (str(target["order_total"]), target["id"]),
            ("08.08.2026", None),
            ("08.08.26", None),
        )
        for query, expected_id in cases:
            with self.subTest(query=query):
                state = self.query(q=query, page_size=200)
                self.assertGreater(state["total"], 0)
                if expected_id:
                    self.assertIn(expected_id, {row["id"] for row in state["rows"]})
        self.assertEqual(self.query(q="результата нет")["total"], 0)

    def test_exact_order_number_normalization_preserves_string_identity(self):
        for value in ("20078", "№20078", "20078№", "#20078", "Nº 20078", " № 20078 "):
            with self.subTest(value=value):
                self.assertEqual(normalize_exact_order_number_query(value), "20078")
        self.assertEqual(normalize_exact_order_number_query("№ 0020078"), "0020078")
        self.assertIsNone(normalize_exact_order_number_query("ORDER-20078"))

    def test_exact_number_ignores_page_status_source_and_period(self):
        target = dict(self.orders[123])
        target.update(number="20078", status="D", created_at="2020-01-01 10:00:00")
        self.store.replace(self.orders[:123] + [target] + self.orders[124:], 1001)

        state = self.query(
            q=" № 20078 ", page=9, page_size=20, status="N",
            source="wildberries", period="today",
        )

        self.assertEqual(state["exact_number"], "20078")
        self.assertEqual(state["total"], 1)
        self.assertEqual(state["page"], 1)
        self.assertEqual(state["rows"][0]["id"], target["id"])

    def test_same_display_number_from_two_sources_stays_two_results(self):
        local = dict(self.orders[0], number="20078", source="tictactoy")
        self.store.replace([local], 1001)
        self.store.upsert_wildberries([{
            "wb_order_id": "wb-external-1", "number": "20078",
            "created_at": "2026-08-20", "status": "new", "source": "wildberries",
        }])

        state = self.query(q="#20078", status="D", source="tictactoy")

        self.assertEqual(state["total"], 2)
        self.assertEqual(
            {row.get("source") for row in state["rows"]},
            {"tictactoy", "wildberries"},
        )

    def test_text_fields_are_or_while_status_and_period_are_and(self):
        target = self.orders[123]
        matching = self.query(q=target["number"], status=target["status"])
        wrong_status = self.query(q=target["number"], status="N" if target["status"] != "N" else "A")
        recent = self.query(q="Иван", status="A", period="7d", page_size=200)

        self.assertEqual(matching["total"], 1)
        self.assertEqual(wrong_status["total"], 0)
        self.assertTrue(recent["rows"])
        self.assertTrue(all(row["status"] == "A" for row in recent["rows"]))
        self.assertTrue(all(row["created_at"][:10] >= "2026-08-16" for row in recent["rows"]))

    def test_replace_is_additive_cache_migration_and_preserves_derived_counts(self):
        self.store.set_item_units(self.orders[0]["id"], 9)
        summaries = [dict(row, products=[]) for row in self.orders]
        self.store.replace(summaries, 1001)

        self.assertEqual(self.store.count(), 455)
        self.assertEqual(self.store.get(self.orders[0]["id"])["item_units"], 9)

    def test_bounded_detail_backfill_sums_units_without_list_n_plus_one(self):
        backfill_store = OrdersSnapshotStore(
            Path(self.temporary.name) / "backfill-orders.db"
        )
        apply_domain_migrations(backfill_store.path, "orders", "test")
        summaries = [
            dict(row, products=[], customer="", phone="")
            for row in self.orders[:3]
        ]
        backfill_store.replace(summaries, 1001)
        client = mock.Mock()
        client.get_order.side_effect = [
            {
                "id": summaries[0]["id"], "number": summaries[0]["number"],
                "customer": "Мария Тестова", "phone": "+7 (900) 555-11-22",
                "order_total": summaries[0]["order_total"],
                "created_at": summaries[0]["created_at"], "status": "N",
                "products": [{"quantity": 2}, {"quantity": 3}],
            },
            {
                "id": summaries[1]["id"], "number": summaries[1]["number"],
                "customer": "Пётр Тестов", "phone": "+7 (901) 444-33-22",
                "order_total": summaries[1]["order_total"],
                "created_at": summaries[1]["created_at"], "status": "A",
                "products": [{"quantity": 4}],
            },
        ]

        with mock.patch.object(web, "normalize_order", side_effect=lambda order: order):
            result = web.backfill_order_item_units(
                store=backfill_store, client=client, limit=2
            )

        self.assertEqual(result, {"requested": 2, "updated": 2, "errors": 0})
        self.assertEqual(backfill_store.get(summaries[0]["id"])["item_units"], 5)
        self.assertEqual(
            backfill_store.get(summaries[0]["id"])["customer"], "Мария Тестова"
        )
        self.assertEqual(backfill_store.query({"q": "тестова"})["total"], 1)
        self.assertEqual(backfill_store.query({"q": "9005551122"})["total"], 1)
        self.assertEqual(backfill_store.get(summaries[1]["id"])["item_units"], 4)
        self.assertIsNone(backfill_store.get(summaries[2]["id"])["item_units"])
        self.assertEqual(backfill_store.missing_detail_ids(), [summaries[2]["id"]])
        self.assertEqual(client.get_order.call_count, 2)

    def test_existing_snapshot_schema_gets_additive_detail_marker(self):
        legacy_path = Path(self.temporary.name) / "legacy-orders.db"
        with sqlite3.connect(str(legacy_path)) as connection:
            connection.execute(
                "CREATE TABLE orders_snapshot ("
                "order_id TEXT PRIMARY KEY, source_position INTEGER NOT NULL, "
                "number_fold TEXT NOT NULL, customer_fold TEXT NOT NULL, "
                "phone_digits TEXT NOT NULL, amount_search TEXT NOT NULL, "
                "date_search TEXT NOT NULL, created_sort TEXT NOT NULL, "
                "status TEXT NOT NULL, item_units REAL, payload_json TEXT NOT NULL, "
                "loaded_at REAL NOT NULL)"
            )

        apply_domain_migrations(legacy_path, "orders", "test")
        legacy_store = OrdersSnapshotStore(legacy_path).initialize()

        with legacy_store.connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(orders_snapshot)"
                ).fetchall()
            }
        self.assertIn("detail_loaded", columns)


class OrdersListIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        self.orders_path = Path(self.temporary.name) / "orders.db"
        apply_domain_migrations(self.orders_path, "orders", "test")
        self.orders = [order_row(index) for index in range(75)]
        web.app.config.update(
            TESTING=True,
            AUTH_TESTING=False,
            ORDERS_SNAPSHOT_TESTING=True,
        )
        self.client = web.app.test_client()

    def tearDown(self):
        web.ORDER_SEARCH_RATE_BUCKETS.clear()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temporary.cleanup()

    def test_api_uses_snapshot_server_search_pagination_and_returns_real_total(self):
        with (
            mock.patch.dict("os.environ", {"ORDERS_DATABASE_PATH": str(self.orders_path)}),
            mock.patch.object(web, "get_orders", return_value=self.orders),
            mock.patch.object(web, "bulk_conducted_order_sales", return_value={}),
            mock.patch.dict(
                web.ORDERS_CACHE,
                {"items": self.orders, "loaded_at": 1000, "error": ""},
                clear=True,
            ),
        ):
            response = self.client.get(
                "/api/orders?q=Петров&status=A&page=2&page_size=20"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total_available"], 75)
        self.assertEqual(payload["total_filtered"], 25)
        self.assertEqual(payload["page"], 2)
        self.assertEqual(payload["total"], 25)
        self.assertEqual(payload["total_pages"], 2)
        self.assertEqual(len(payload["items"]), 5)
        self.assertIn("Показано 21–25 из 25", payload["html"])
        self.assertIn('aria-current="page">2</span>', payload["html"])

    def test_compact_header_has_live_search_filters_and_no_legacy_scope_or_kpis(self):
        with (
            mock.patch.object(web, "get_orders", return_value=[]),
            mock.patch.object(web, "bulk_conducted_order_sales", return_value={}),
            mock.patch.dict(web.ORDERS_CACHE, {"items": [], "loaded_at": 0, "error": ""}, clear=True),
        ):
            response = self.client.get("/app/orders?mine=1")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for expected in (
            'class="orders-command-bar"', 'id="orderSearch"',
            'data-status-filter="all"', 'data-status-filter="N"',
            'data-status-filter="A"', 'data-status-filter="D"',
            "Обновить WB", "Список", "Разделение", "Карточка",
        ):
            self.assertIn(expected, html)
        self.assertNotIn("Управление заказами интернет-магазина", html)
        self.assertNotIn("orders-kpis", html)
        self.assertNotIn(">Мои<", html)

    def test_legacy_mine_parameter_does_not_limit_api_results(self):
        with (
            mock.patch.object(web, "get_orders", return_value=self.orders),
            mock.patch.object(web, "bulk_conducted_order_sales", return_value={}),
        ):
            response = self.client.get("/api/orders?mine=1&page_size=20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["total_available"], 75)

    def test_external_search_runs_only_after_local_exact_miss(self):
        external_client = mock.Mock()
        external_client.get_order.return_value = {"ID": "20078"}
        with web.app.test_request_context("/api/orders?q=20078"):
            local_state = web.exact_order_search_state(
                "20078", [{"id": "20078", "number": "20078"}],
                client=external_client,
            )
        self.assertEqual(local_state["status"], "local_found")
        external_client.get_order.assert_not_called()

        with (
            web.app.test_request_context("/api/orders?q=20078"),
            mock.patch.object(web, "normalize_order", return_value={
                "id": "20078", "number": "20078", "status": "D",
                "products": [],
            }),
            mock.patch.object(web, "build_bitrix_order_url", return_value="https://www.tictactoy.ru/bitrix/admin/sale_order_view.php?ID=20078"),
        ):
            external_state = web.exact_order_search_state(
                "20078", [], client=external_client
            )
        self.assertEqual(external_state["status"], "external_found")
        self.assertEqual(external_state["external_results"][0]["external_id"], "20078")
        self.assertTrue(external_state["external_results"][0]["external_only"])
        self.assertEqual(external_client.get_order.call_count, 1)
        self.assertEqual(external_state["sources"][1]["result"], "not_supported")

    def test_external_not_found_and_unavailable_are_distinct(self):
        not_found_client = mock.Mock()
        not_found_client.get_order.return_value = None
        with web.app.test_request_context("/api/orders?q=20078"):
            not_found = web.exact_order_search_state(
                "20078", [], client=not_found_client
            )
        self.assertEqual(not_found["status"], "not_found")
        self.assertEqual(not_found["sources"][0]["result"], "not_found")

        unavailable_client = mock.Mock()
        unavailable_client.get_order.side_effect = web.BitrixReadOnlyError("timeout")
        with web.app.test_request_context("/api/orders?q=20079"):
            unavailable = web.exact_order_search_state(
                "20079", [], client=unavailable_client
            )
        self.assertEqual(unavailable["status"], "partial")
        self.assertEqual(unavailable["sources"][0]["result"], "unavailable")

    def test_external_identity_resolves_to_existing_local_record_without_duplicate(self):
        external_client = mock.Mock()
        external_client.get_order.return_value = {"ID": "20078"}
        store = mock.Mock()
        store.get_by_identity.return_value = {
            "id": "local-1", "number": "legacy-number", "source": "tictactoy"
        }
        with (
            web.app.test_request_context("/api/orders?q=20078"),
            mock.patch.object(web, "normalize_order", return_value={
                "id": "20078", "number": "20078", "products": [],
            }),
        ):
            state = web.exact_order_search_state(
                "20078", [], client=external_client, store=store
            )

        self.assertEqual(state["status"], "local_found")
        self.assertEqual(state["local_results"][0]["id"], "local-1")
        self.assertEqual(state["external_results"], [])

    def test_exact_search_api_returns_external_read_only_card(self):
        external_client = mock.Mock()
        external_client.get_order.return_value = {"ID": "20078"}
        with (
            mock.patch.dict("os.environ", {"ORDERS_DATABASE_PATH": str(self.orders_path)}),
            mock.patch.object(web, "get_orders", return_value=self.orders),
            mock.patch.object(web, "bulk_conducted_order_sales", return_value={}),
            mock.patch.object(web, "bitrix_orders_client", return_value=external_client),
            mock.patch.object(web, "normalize_order", return_value={
                "id": "20078", "number": "20078", "status": "D",
                "status_name": "Собран", "products": [],
            }),
            mock.patch.dict(web.ORDERS_CACHE, {"items": self.orders, "loaded_at": 1000, "error": ""}, clear=True),
        ):
            response = self.client.get(
                "/api/orders?q=%E2%84%96%2020078&status=N&period=today&source=wildberries"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["exact_search"]["status"], "external_found")
        self.assertIn("Заказ ещё не загружен в ERP", payload["html"])
        self.assertIn("по всем статусам, источникам и периодам", payload["html"])
        self.assertNotIn("Загрузить в ERP", payload["html"])

    def test_search_ui_preserves_list_state_and_cancels_stale_requests(self):
        template = Path(web.PROJECT_ROOT / "app/templates/orders.html").read_text(encoding="utf-8")
        for expected in (
            "ordersSearchController?.abort()",
            "requestId!==ordersSearchRequest",
            "let listReturnUrl=",
            "loadOrdersResults(listReturnUrl,{push:true})",
            "displayUrl.searchParams.set('q',normalizedNumber)",
            "displayUrl.searchParams.delete('q')",
            "Ищем заказ №${normalizedNumber} в ERP и подключённых источниках",
        ):
            self.assertIn(expected, template)

    def test_row_has_customer_phone_amount_datetime_units_status_and_sale_badge(self):
        row = dict(
            order_row(1), item_units=5,
            sale_completed=True, conducted_sale_id="sale-1",
        )
        with web.app.test_request_context("/app/orders?page_size=20"):
            html = web.render_template(
                "_orders_list_results.html",
                orders=[row],
                selected_order=row,
                orders_total=1,
                orders_page=1,
                orders_page_size=20,
                orders_page_sizes=(20, 50, 100, 200),
                orders_page_count=1,
                sync_error="",
            )
        for expected in (
            row["number"], row["customer"], row["phone"],
            'data-amount="10001"', 'data-date="2026-08-02 13:30:00"',
            "×2", "×3", "Подтверждён", "Продажа проведена",
        ):
            self.assertIn(expected, html)

    def test_list_mode_table_is_compact_and_uses_latest_employee_comment(self):
        row = dict(
            order_row(1),
            source="wildberries",
            source_name="Wildberries",
            wb_order_id="991122",
            email="buyer@example.test",
            delivery_type="FBS",
            payment_system="Онлайн",
            paid="N",
            sale_completed=False,
            products=[
                {"name": "BLM Blue AUTOMATIC", "article": "BLM-01", "quantity": 1},
                {"name": "Bradley Black", "article": "BR-02", "quantity": 2},
                {"name": "Bradley White", "article": "BR-03", "quantity": 1},
            ],
            internal_comment={
                "text": "Уточнил цвет ремешка",
                "author_name": "Максим У.",
                "created_at": "2026-09-01T18:35:00",
            },
        )
        with web.app.test_request_context("/app/orders?page_size=20"):
            html = web.render_template(
                "_orders_list_results.html",
                orders=[row], selected_order=row, orders_total=1,
                orders_page=1, orders_page_size=20,
                orders_page_sizes=(20, 50, 100, 200), orders_page_count=1,
                sync_error="",
            )

        list_table = html.split('class="orders-table orders-list-table"', 1)[1].split(
            'class="orders-table orders-split-table"', 1
        )[0]
        expected_headers = (
            "Заказ", "Создан", "Статус", "Сумма", "Покупатель", "Товары",
            "Доставка", "Оплата", "Комментарий сотрудника", "Действия",
        )
        header_positions = [list_table.index(">{}<".format(label)) for label in expected_headers]
        self.assertEqual(header_positions, sorted(header_positions))
        for expected in (
            "WB FBS", "BLM Blue AUTOMATIC ×1", "арт. BLM-01", "+ ещё 2",
            "Уточнил цвет ремешка", "Максим У.",
            'data-date="2026-09-01T18:35:00"', "Не оплачен", "Открыть",
            'aria-current="true"',
        ):
            self.assertIn(expected, list_table)
        self.assertNotIn("Bradley Black", list_table)

        css = Path(web.PROJECT_ROOT / "app/static/css/orders.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('.workspace[data-layout-mode="list"] .orders-list-table', css)
        self.assertIn("-webkit-line-clamp:2", css)
        self.assertIn(
            '.workspace[data-layout-mode="list"] .orders-split-table-scroll { display:none; }',
            css,
        )

    def test_bulk_sale_evidence_uses_only_canonical_active_order_relation(self):
        catalog = CatalogDatabase(Path(self.temporary.name) / "catalog.db")
        catalog.initialize()
        with catalog.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_sales (id, source, external_order_id, idempotency_key, "
                "status, created_at, metadata_json, inserted_at, updated_at) "
                "VALUES ('sale-1','tictactoy','29999','key-1','completed',"
                "'2026-08-01','{}','2026-08-01','2026-08-01')"
            )
            connection.execute(
                "INSERT INTO erp_sales (id, source, external_order_id, idempotency_key, "
                "status, created_at, metadata_json, inserted_at, updated_at) "
                "VALUES ('legacy-same-amount','manual','29996','key-2','completed',"
                "'2026-08-01','{}','2026-08-01','2026-08-01')"
            )
            connection.execute(
                "INSERT INTO erp_sales (id, source, external_order_id, idempotency_key, "
                "status, created_at, metadata_json, inserted_at, updated_at, cancelled_at) "
                "VALUES ('cancelled','tictactoy','29998','key-3','completed',"
                "'2026-08-01','{}','2026-08-01','2026-08-01','2026-08-02')"
            )
            connection.execute(
                "INSERT INTO erp_sales (id, source, external_order_id, idempotency_key, "
                "status, created_at, metadata_json, inserted_at, updated_at, deleted_at) "
                "VALUES ('deleted','tictactoy','29997','key-4','completed',"
                "'2026-08-01','{}','2026-08-01','2026-08-01','2026-08-02')"
            )
        enriched = web.enrich_orders_list_rows(
            [order_row(1), order_row(2), order_row(3), order_row(4)],
            database=catalog,
        )
        self.assertTrue(enriched[0]["sale_completed"])
        self.assertEqual(enriched[0]["conducted_sale_id"], "sale-1")
        self.assertFalse(enriched[1]["sale_completed"])
        self.assertFalse(enriched[2]["sale_completed"])
        self.assertFalse(enriched[3]["sale_completed"])

    def test_list_metadata_is_loaded_in_bulk_without_n_plus_one(self):
        statements = []

        class TracedCatalog(CatalogDatabase):
            def connect(inner_self):
                connection = super(TracedCatalog, inner_self).connect()
                connection.set_trace_callback(statements.append)
                return connection

        catalog = TracedCatalog(Path(self.temporary.name) / "metadata-catalog.db")
        apply_migrations(catalog.path, app_commit="orders-list-test")
        catalog.initialize()
        order_ids = [row["id"] for row in self.orders[:50]]
        with catalog.transaction() as connection:
            for index, order_id in enumerate(order_ids[:2]):
                connection.execute(
                    "INSERT INTO erp_order_comments (order_id,text,author_name,created_at,source) "
                    "VALUES (?,?,?,?, 'erp')",
                    (order_id, "Внутренний {}".format(index), "Сотрудник", "2026-08-28T10:00:00"),
                )
                connection.execute(
                    "INSERT INTO erp_order_status_events (external_order_id,old_status,new_status,actor,source,sync_result,created_at) "
                    "VALUES (?,?,? ,?,'erp','synced',?)",
                    (order_id, "N", "A", "Сотрудник", "2026-08-28T11:00:00"),
                )
            connection.execute(
                "INSERT INTO erp_order_comments (order_id,text,author_name,created_at,source) "
                "VALUES (?,?,?,?, 'erp')",
                (order_ids[0], "Последний внутренний", "Сотрудник", "2026-08-28T12:00:00"),
            )
            connection.execute(
                "INSERT INTO erp_order_status_events (external_order_id,old_status,new_status,actor,source,sync_result,created_at) "
                "VALUES (?,?,? ,?,'erp','synced',?)",
                (order_ids[0], "A", "F", "Последний сотрудник", "2026-08-28T12:00:00"),
            )
        statements.clear()
        enriched = web.enrich_orders_list_rows(self.orders[:50], database=catalog)
        selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        self.assertLessEqual(len(selects), 3)
        self.assertFalse(any("ROW_NUMBER" in statement for statement in selects))
        self.assertEqual(enriched[0]["internal_comment"]["text"], "Последний внутренний")
        self.assertEqual(enriched[0]["status_event"]["actor"], "Последний сотрудник")


if __name__ == "__main__":
    unittest.main()
