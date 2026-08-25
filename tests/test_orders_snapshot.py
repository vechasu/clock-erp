import tempfile
import unittest
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.domain_schema_migrations import apply_domain_migrations
from app.services.orders_snapshot import OrdersSnapshotStore


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

    def tearDown(self):
        self.temporary.cleanup()

    def query(self, **args):
        return self.store.query(args, now=datetime(2026, 8, 23, 15, 0))

    def test_db_total_limit_offset_page_sizes_and_stable_complete_walk(self):
        first = self.query()
        self.assertEqual(first["physical_total"], 455)
        self.assertEqual(first["total"], 455)
        self.assertEqual(first["page_size"], 20)
        self.assertEqual(first["page_count"], 23)
        self.assertEqual(len(first["rows"]), 20)

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
        self.assertEqual(walked, [row["id"] for row in self.orders])

        boundaries = {
            1: self.orders[:50],
            2: self.orders[50:100],
            5: self.orders[200:250],
            10: self.orders[450:],
        }
        for page, expected in boundaries.items():
            with self.subTest(page=page):
                state = self.query(page=page, page_size=50)
                self.assertEqual(
                    [row["id"] for row in state["rows"]],
                    [row["id"] for row in expected],
                )
        self.assertEqual(self.query(page=999, page_size=50)["page"], 10)

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
        self.assertIn("Страница 2 из 2", payload["html"])
        self.assertNotIn("2 / 2", payload["html"])

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
            "5 товаров", "Подтверждён", "✓ Продажа проведена",
        ):
            self.assertIn(expected, html)

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


if __name__ == "__main__":
    unittest.main()
