import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.domain_schema_migrations import apply_domain_migrations
from app.services.customer_identity import (
    CustomerStore,
    analyze_orders,
    backfill_customers,
    match_or_create_customer,
    normalize_email,
    normalize_phone,
)
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.customer_registry import (
    CustomerRegistry, masked_email, migrate_database,
    normalize_email as registry_normalize_email,
    normalize_phone as registry_normalize_phone,
)


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
        apply_domain_migrations(self.store.path, "orders", "test")
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
        apply_domain_migrations(self.store.path, "orders", "test")

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


class CanonicalCustomerRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "customers.db"
        migrate_database(self.path)
        self.registry = CustomerRegistry(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def add(self, external_id, **values):
        payload = {
            "operation_type": "order", "source": "tictactoy",
            "external_id": str(external_id), "name": "Клиент",
            "occurred_at": "2026-01-{:02d}".format((int(external_id) % 28) + 1),
        }
        payload.update(values)
        with self.registry.connection() as connection:
            result = self.registry.upsert_operation(connection, payload)
            self.registry.recompute(connection)
        return result

    def test_phone_email_masking_name_and_contactless_rules(self):
        self.assertEqual(registry_normalize_phone("8 (921) 123-45-67"), "+79211234567")
        self.assertEqual(registry_normalize_phone("+49 30 123456"), "+4930123456")
        self.assertEqual(registry_normalize_email(" A@EXAMPLE.RU "), "a@example.ru")
        self.assertTrue(masked_email("abc@privaterelay.example"))
        first = self.add(1, phone="8 (921) 123-45-67", email="A@EXAMPLE.RU")
        same = self.add(2, phone="+7 921 123-45-67")
        different_name_only = self.add(3, name="Клиент", phone="", email="")
        another_name_only = self.add(4, name="Клиент", phone="", email="")
        self.assertEqual(first["customer_id"], same["customer_id"])
        self.assertNotEqual(different_name_only["customer_id"], another_name_only["customer_id"])
        relay_one = self.add(5, email="same@privaterelay.example", source="wildberries")
        relay_two = self.add(6, email="same@privaterelay.example", source="amazon")
        self.assertNotEqual(relay_one["customer_id"], relay_two["customer_id"])

    def test_conflict_idempotency_cancellation_and_blank_preservation(self):
        phone_customer = self.add(1, name="Полное имя", phone="+7 900 000-00-01", email="one@example.ru", external_customer_id="u1")
        email_customer = self.add(2, phone="+7 900 000-00-02", email="two@example.ru")
        conflict = self.add(3, phone="+7 900 000-00-01", email="two@example.ru")
        self.assertNotIn(conflict["customer_id"], {phone_customer["customer_id"], email_customer["customer_id"]})
        self.assertEqual(conflict["reason"], "phone_email_cross_conflict")
        repeated = self.add(1, name="", phone="", email="", completed=True, amount=100)
        self.assertEqual(repeated["action"], "updated")
        cancelled = self.add(4, phone="+7 900 000-00-01", external_customer_id="u1", cancelled=True, completed=False, amount=999)
        customer = self.registry.get(phone_customer["customer_id"])
        self.assertEqual(customer["name"], "Полное имя")
        self.assertEqual(customer["cancelled_orders_count"], 1)
        self.assertEqual(customer["total_completed_amount"], 0)

    def test_shared_external_id_never_overrides_personal_contacts(self):
        first = self.add(11, phone="+7 900 000-00-11", email="first@example.ru", external_customer_id="shared")
        second = self.add(12, phone="+7 900 000-00-12", email="second@example.ru", external_customer_id="shared")
        repeated = self.add(13, phone="+7 900 000-00-12", email="second@example.ru", external_customer_id="shared")
        self.assertNotEqual(first["customer_id"], second["customer_id"])
        self.assertEqual(second["reason"], "external_id_contact_conflict")
        self.assertEqual(repeated["customer_id"], second["customer_id"])
        reused_email = self.add(14, phone="+7 900 000-00-14", email="second@example.ru")
        self.assertNotEqual(reused_email["customer_id"], second["customer_id"])
        self.assertEqual(reused_email["reason"], "phone_email_value_conflict")

    def test_more_than_100_server_paginated_customers_and_global_search(self):
        for index in range(1, 126):
            self.add(index, name="Клиент {}".format(index), phone="+1 202 555 {:04d}".format(index))
        first = self.registry.list(page=1, per_page=50)
        third = self.registry.list(page=3, per_page=50)
        search = self.registry.list(query="Клиент 120", per_page=20)
        self.assertEqual(first["total"], 125)
        self.assertEqual(len(first["rows"]), 50)
        self.assertEqual(len(third["rows"]), 25)
        self.assertEqual(search["total"], 1)

    def test_quality_counters_segments_merge_unmerge_and_candidates(self):
        first = self.add(201, name="Первый", phone="+7 900 100-00-01", city="4334")
        second = self.add(202, name="Второй", phone="+7 900 100-00-02", email="One@EXAMPLE.RU")
        third = self.add(203, name="Третий", phone="+7 900 100-00-03", email="Two@example.ru")
        with self.registry.connection() as connection:
            connection.execute(
                "INSERT INTO customer_contacts(customer_id,kind,normalized_value,display_value,source,masked,created_at,updated_at) VALUES(?, 'email', 'one@example.ru', 'One@example.ru', 'legacy', 0, '2026-01-01', '2026-01-01')",
                (third["customer_id"],),
            )
        with self.registry.connection() as connection:
            self.registry.upsert_operation(connection, {
                "operation_type": "sale", "source": "erp", "external_id": "s1",
                "related_order_source": "tictactoy", "related_order_id": "201",
                "occurred_at": "2026-08-20", "completed": True, "amount": 12500,
            })
            self.registry.upsert_operation(connection, {
                "operation_type": "sale", "source": "erp", "external_id": "s2",
                "related_order_source": "tictactoy", "related_order_id": "201",
                "occurred_at": "2099-12-31", "completed": True, "amount": 2500,
            })
            self.registry.recompute(connection)
        customer = self.registry.get(first["customer_id"])
        self.assertEqual(customer["city_display"], "Не указан")
        self.assertEqual(customer["sales_count"], 2)
        self.assertEqual(customer["sales_amount"], 15000)
        self.assertEqual(customer["last_operation_at"][:10], "2026-08-20")
        self.assertIn("Повторный", customer["segments"])
        analytics = self.registry.analytics(source="erp")
        self.assertEqual(analytics["buyers"], 1)
        self.assertEqual(analytics["revenue"], 15000)
        candidates = self.registry.duplicate_candidates(second["customer_id"])
        self.assertTrue(any(item["right_customer_id"] == third["customer_id"] for item in candidates))
        audit, created = self.registry.merge(second["customer_id"], third["customer_id"], "1", "test-merge")
        self.assertTrue(created)
        self.assertIsNotNone(self.registry.get(third["customer_id"])["merged_into_id"])
        repeated, repeated_created = self.registry.merge(second["customer_id"], third["customer_id"], "1", "test-merge")
        self.assertFalse(repeated_created)
        self.assertEqual(repeated["id"], audit["id"])
        self.registry.unmerge(audit["id"], "1")
        self.assertIsNone(self.registry.get(third["customer_id"])["merged_into_id"])


class CustomerRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "orders.db"
        self.customers_path = Path(self.temporary.name) / "customers.db"
        apply_domain_migrations(self.path, "orders", "test")
        OrdersSnapshotStore(self.path).replace([order(1), order(2)], 1)
        migrate_database(self.customers_path)
        registry = CustomerRegistry(self.customers_path)
        with registry.connection() as connection:
            registry.upsert_operation(connection, {
                "operation_type": "order", "source": "tictactoy", "external_id": "1",
                "name": "Иван Иванов", "phone": "+7 921 123-45-67",
                "email": "ivan@example.ru", "occurred_at": "2026-08-01", "completed": True,
            })
            registry.recompute(connection)
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.environment = mock.patch.dict(
            "os.environ", {"ORDERS_DATABASE_PATH": str(self.path),
                           "CUSTOMERS_DATABASE_PATH": str(self.customers_path),
                           "ERP_AUTH_ENABLED": "0"}, clear=False
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
        customer_id = sqlite3.connect(str(self.customers_path)).execute("SELECT id FROM customers").fetchone()[0]
        overview = self.client.get("/app/customers/{}".format(customer_id))
        orders_tab = self.client.get("/app/customers/{}?tab=orders".format(customer_id))
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(orders_tab.status_code, 200)
        self.assertIn("Обзор", overview.get_data(as_text=True))
        self.assertIn("Заказ · №1", orders_tab.get_data(as_text=True))
        self.assertEqual(self.client.get("/app/customers/999999").status_code, 404)

        self.assertIn("Продажи", overview.get_data(as_text=True))

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
