import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.services.customer_identity import (
    CustomerStore,
    analyze_orders,
    backfill_customers,
    match_or_create_customer,
    normalize_email,
    normalize_phone,
)
from app.services.orders_snapshot import OrdersSnapshotStore


def order(order_id, name="Иван Иванов", phone="+7 921 123-45-67", email="ivan@example.ru", **values):
    row = {
        "id": str(order_id), "number": str(order_id), "customer": name,
        "phone": phone, "email": email, "source": "tictactoy",
        "source_name": "Tictactoy", "status": "N", "status_name": "Не подтверждён",
        "created_at": "2026-08-{:02d}T10:00:00".format(int(order_id) % 20 + 1),
        "order_total": 1200.0, "country": "Россия", "region": "Москва", "city": "Москва",
    }
    row.update(values)
    return row


class CustomerNormalizationTest(unittest.TestCase):
    def test_russian_phone_variants_are_equivalent(self):
        expected = "+79211234567"
        for value in (
            "+7 921 123-45-67", "8 (921) 123-45-67", "89211234567",
            "+79211234567", "9211234567", "+7(921)1234567",
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_phone(value), expected)

    def test_empty_malformed_and_foreign_phone_policy(self):
        for value in ("", None, "телефон", "+7", "12345", "+49+30123456", "4930123456"):
            self.assertEqual(normalize_phone(value), "")
        self.assertEqual(normalize_phone("+49 30 123456"), "+4930123456")

    def test_email_normalization_is_conservative(self):
        self.assertEqual(normalize_email(" MAX@MAIL.RU "), "max@mail.ru")
        self.assertEqual(normalize_email(""), "")
        self.assertEqual(normalize_email("not-an-email"), "")
        self.assertEqual(normalize_email("first.last@gmail.com"), "first.last@gmail.com")


class CustomerMatchingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = OrdersSnapshotStore(Path(self.temporary.name) / "orders.db")
        self.store.initialize()
        self.connection = self.store.connect()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def test_phone_then_email_matching_and_repeated_processing(self):
        first = match_or_create_customer(self.connection, order(1))
        by_phone = match_or_create_customer(
            self.connection, order(2, name="Опечатка", email="ivan@example.ru")
        )
        by_email = match_or_create_customer(
            self.connection, order(3, phone="", email="IVAN@EXAMPLE.RU")
        )
        repeated = match_or_create_customer(self.connection, order(1))
        self.assertEqual(
            {first["customer_id"], by_phone["customer_id"], by_email["customer_id"], repeated["customer_id"]},
            {first["customer_id"]},
        )
        customer = self.connection.execute("SELECT * FROM customers").fetchone()
        self.assertEqual(customer["name"], "Иван Иванов")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)

    def test_conflicting_new_contact_does_not_overwrite_or_link(self):
        first = match_or_create_customer(self.connection, order(1))
        conflict = match_or_create_customer(
            self.connection, order(2, email="other@example.ru")
        )
        self.assertIsNotNone(first["customer_id"])
        self.assertIsNone(conflict["customer_id"])
        self.assertEqual(conflict["reason"], "phone_email_value_conflict")
        customer = self.connection.execute("SELECT * FROM customers").fetchone()
        self.assertEqual(customer["email"], "ivan@example.ru")

    def test_only_blank_customer_fields_are_filled_and_repeat_is_stable(self):
        first = match_or_create_customer(
            self.connection, order(1, phone="", city="", email="ivan@example.ru")
        )
        match_or_create_customer(
            self.connection, order(2, phone="+7 921 123-45-67", city="Москва", email="ivan@example.ru")
        )
        after_fill = dict(self.connection.execute(
            "SELECT * FROM customers WHERE id = ?", (first["customer_id"],)
        ).fetchone())
        match_or_create_customer(
            self.connection, order(2, phone="+7 921 123-45-67", city="Москва", email="ivan@example.ru")
        )
        after_repeat = dict(self.connection.execute(
            "SELECT * FROM customers WHERE id = ?", (first["customer_id"],)
        ).fetchone())
        self.assertEqual(after_fill, after_repeat)
        self.assertEqual(after_fill["normalized_phone"], "+79211234567")
        self.assertEqual(after_fill["city"], "Москва")

    def test_name_alone_never_creates_or_matches(self):
        result = match_or_create_customer(self.connection, order(1, phone="", email=""))
        self.assertIsNone(result["customer_id"])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 0)

    def test_conflicting_phone_email_and_duplicate_candidates_are_unlinked(self):
        phone_customer = match_or_create_customer(self.connection, order(1, email="one@example.ru"))
        email_customer = match_or_create_customer(
            self.connection, order(2, phone="+7 999 000-00-00", email="two@example.ru")
        )
        cross = match_or_create_customer(
            self.connection, order(3, phone="+7 921 123-45-67", email="two@example.ru")
        )
        self.assertNotEqual(phone_customer["customer_id"], email_customer["customer_id"])
        self.assertEqual(cross["reason"], "phone_email_cross_conflict")
        self.connection.execute(
            "INSERT INTO customers (name, name_fold, phone, normalized_phone, email, normalized_email, country, region, city, created_at, updated_at) "
            "SELECT name, name_fold, phone, normalized_phone, '', '', country, region, city, created_at, updated_at FROM customers WHERE id = ?",
            (phone_customer["customer_id"],),
        )
        duplicate = match_or_create_customer(self.connection, order(4, email=""))
        self.assertEqual(duplicate["reason"], "duplicate_phone_candidates")


class CustomerBackfillTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = OrdersSnapshotStore(Path(self.temporary.name) / "orders.db")

    def tearDown(self):
        self.temporary.cleanup()

    def test_analysis_excludes_cross_conflicts_deterministically(self):
        rows = [
            order(1, email="a@example.ru"), order(2, email="b@example.ru"),
            order(3, phone="", email="solo@example.ru"), order(4, phone="", email=""),
        ]
        report = analyze_orders(list(reversed(rows)))["report"]
        self.assertEqual(report["phone_conflicts"], 1)
        self.assertEqual(report["orders_without_identity"], 1)
        self.assertEqual(report["safe_customer_groups"], 1)
        self.assertEqual(report["estimated_linked_orders"], 1)

    def test_backfill_is_idempotent_and_preserves_order_snapshots(self):
        rows = [order(1), order(2), order(3, name="Без контактов", phone="", email="")]
        self.store.replace(rows, 1)
        with self.store.connection() as connection:
            connection.execute("UPDATE orders_snapshot SET customer_id = NULL")
            connection.execute("DELETE FROM customers")
            before = connection.execute(
                "SELECT order_id, payload_json FROM orders_snapshot ORDER BY order_id"
            ).fetchall()
            first = backfill_customers(connection)
        with self.store.connection() as connection:
            second = backfill_customers(connection)
            after = connection.execute(
                "SELECT order_id, payload_json FROM orders_snapshot ORDER BY order_id"
            ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in before], [(row[0], row[1]) for row in after])
        self.assertEqual(first["actual_customers"], 1)
        self.assertEqual(first["linked_orders"], 2)
        self.assertEqual(second["new_links"], 0)
        self.assertEqual(second["actual_customers"], 1)


class CustomerRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "orders.db"
        OrdersSnapshotStore(self.path).replace([order(1), order(2)], 1)
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.environment = mock.patch.dict(
            "os.environ", {"ORDERS_DATABASE_PATH": str(self.path), "ERP_AUTH_ENABLED": "0"}, clear=False
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_list_search_pagination_detail_tabs_and_links(self):
        listing = self.client.get("/app/customers?q=9211234567&per_page=20")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("Найдено: 1 клиентов", listing.get_data(as_text=True))
        self.assertIn(
            "Найдено: 1 клиентов",
            self.client.get("/app/customers?q=иван").get_data(as_text=True),
        )
        customer_id = sqlite3.connect(str(self.path)).execute("SELECT id FROM customers").fetchone()[0]
        overview = self.client.get("/app/customers/{}".format(customer_id))
        orders_tab = self.client.get("/app/customers/{}?tab=orders".format(customer_id))
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(orders_tab.status_code, 200)
        self.assertIn("Обзор", overview.get_data(as_text=True))
        self.assertIn('href="/order/1"', orders_tab.get_data(as_text=True))
        self.assertEqual(self.client.get("/app/customers/999999").status_code, 404)

        with mock.patch.object(web, "get_order", return_value=order(1)), mock.patch.object(
            web, "get_orders", return_value=[order(1), order(2)]
        ):
            order_card = self.client.get("/order/1")
        self.assertEqual(order_card.status_code, 200)
        self.assertIn(
            'href="/app/customers/{}"'.format(customer_id),
            order_card.get_data(as_text=True),
        )

    def test_customer_routes_follow_global_auth_protection(self):
        with mock.patch.dict("os.environ", {"ERP_AUTH_ENABLED": "1"}, clear=False), mock.patch.dict(
            web.app.config, {"AUTH_TESTING": True}
        ):
            response = self.client.get("/app/customers")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_storage_error_has_safe_visible_state(self):
        with mock.patch.object(web, "customer_store", side_effect=sqlite3.Error("failed")):
            response = self.client.get("/app/customers")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Не удалось загрузить клиентов", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
