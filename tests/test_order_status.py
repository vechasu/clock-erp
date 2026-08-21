import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.order_status import (
    ERP_ASSEMBLED,
    ERP_CONFIRMED,
    ERP_UNCONFIRMED,
    OrderStatusError,
    OrderStatusService,
)


class OrderStatusServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.sqlite3")
        self.service = OrderStatusService(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, query, parameters=()):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def insert_sale(self, connection, sale_id="sale-1"):
        now = "2026-08-21T12:00:00+00:00"
        connection.execute(
            "INSERT INTO erp_sales (id, source, external_order_id, status, "
            "created_at, metadata_json, inserted_at, updated_at) "
            "VALUES (?, 'tictactoy', '42', 'completed', ?, '{}', ?, ?)",
            (sale_id, now, now, now),
        )

    def test_new_order_and_legacy_initial_codes_are_unconfirmed(self):
        for order_id, code in (("1", "N"), ("2", "O"), ("3", "0")):
            state = self.service.ingest(order_id, code)
            self.assertEqual(state["erp_status"], ERP_UNCONFIRMED)
            self.assertEqual(state["sync_status"], "synced")

    def test_schema_upgrade_preserves_existing_orders_sales_and_identifiers(self):
        with self.database.transaction() as connection:
            self.insert_sale(connection, "legacy-sale")
            connection.execute("DROP TABLE erp_order_status_sync_queue")
            connection.execute("DROP TABLE erp_order_status_events")
            connection.execute("DROP TABLE erp_order_statuses")
        self.database.initialize()
        sales = self.rows(
            "SELECT id, external_order_id FROM erp_sales WHERE id='legacy-sale'"
        )
        self.assertEqual(sales, [{
            "id": "legacy-sale", "external_order_id": "42",
        }])
        state = OrderStatusService(self.database).ingest("42", "A")
        self.assertEqual(state["erp_status"], ERP_CONFIRMED)

    def test_sale_index_uses_production_compatible_sqlite_syntax(self):
        index = self.rows(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_erp_order_status_sale'"
        )[0]["sql"]
        self.assertNotIn(" WHERE ", index.upper())

    def test_status_queue_avoids_upsert_unsupported_by_production_sqlite(self):
        statements = []

        class ProductionSQLiteConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, parameters=()):
                statements.append(sql)
                if "ON CONFLICT" in sql.upper():
                    raise AssertionError("SQLite 3.7.17 cannot execute UPSERT")
                return self.connection.execute(sql, parameters)

        self.service.ingest("42", "A")
        with self.database.transaction() as connection:
            self.insert_sale(connection)
            legacy_connection = ProductionSQLiteConnection(connection)
            self.service.change(
                "42", ERP_ASSEMBLED, "Максим", sale_id="sale-1",
                connection=legacy_connection,
            )
        queue = self.rows(
            "SELECT external_order_id, erp_status, attempts "
            "FROM erp_order_status_sync_queue"
        )
        self.assertEqual(queue, [{
            "external_order_id": "42",
            "erp_status": ERP_ASSEMBLED,
            "attempts": 0,
        }])
        self.assertFalse(any("ON CONFLICT" in sql.upper() for sql in statements))

    def test_overlay_uses_bitrix_status_when_database_writer_is_busy(self):
        writer = self.database.connect()
        writer.execute("BEGIN IMMEDIATE")
        try:
            order = self.service.overlay({"id": "21110", "status": "A"})
        finally:
            writer.rollback()
            writer.close()

        self.assertEqual(order["status"], "A")
        self.assertEqual(order["erp_status"], ERP_CONFIRMED)
        self.assertEqual(order["status_sync_state"], "pending")
        self.assertIsNone(self.service.get("21110"))

    def test_confirm_is_idempotent_and_manual_assembled_is_forbidden(self):
        self.service.ingest("42", "N")
        first = self.service.change("42", ERP_CONFIRMED, "Максим")
        second = self.service.change("42", ERP_CONFIRMED, "Максим")
        self.assertEqual(first["erp_status"], ERP_CONFIRMED)
        self.assertEqual(second["erp_status"], ERP_CONFIRMED)
        with self.assertRaises(OrderStatusError):
            self.service.change("42", ERP_ASSEMBLED, "Максим")
        self.assertEqual(len(self.rows(
            "SELECT * FROM erp_order_status_events "
            "WHERE external_order_id='42' AND new_status='confirmed'"
        )), 1)

    def test_assembled_status_commits_with_sale_in_same_transaction(self):
        self.service.ingest("42", "A")
        with self.database.transaction() as connection:
            self.insert_sale(connection)
            self.service.change(
                "42", ERP_ASSEMBLED, "Максим", sale_id="sale-1",
                connection=connection,
            )
        state = self.service.get("42")
        self.assertEqual(state["erp_status"], ERP_ASSEMBLED)
        self.assertEqual(state["sale_id"], "sale-1")

    def test_transaction_failure_rolls_back_sale_and_assembled_status(self):
        self.service.ingest("42", "A")
        with self.assertRaises(RuntimeError):
            with self.database.transaction() as connection:
                self.insert_sale(connection)
                self.service.change(
                    "42", ERP_ASSEMBLED, "Максим", sale_id="sale-1",
                    connection=connection,
                )
                raise RuntimeError("rollback")
        self.assertEqual(self.service.get("42")["erp_status"], ERP_CONFIRMED)
        self.assertEqual(self.rows("SELECT * FROM erp_sales"), [])

    def test_bitrix_error_keeps_local_change_and_schedules_retry(self):
        self.service.ingest("42", "N")
        self.service.change("42", ERP_CONFIRMED, "Максим")
        sent = []

        def unavailable(order_id, status):
            sent.append((order_id, status))
            return {"status": "error", "code": "TEMPORARY"}

        self.assertFalse(self.service.sync_one("42", unavailable))
        self.assertEqual(sent, [("42", "A")])
        self.assertEqual(self.service.get("42")["erp_status"], ERP_CONFIRMED)
        self.assertEqual(self.service.get("42")["sync_status"], "error")
        queue = self.rows("SELECT * FROM erp_order_status_sync_queue")
        self.assertEqual(queue[0]["attempts"], 1)

    def test_bitrix_exception_is_safely_scheduled_for_retry(self):
        self.service.ingest("42", "N")
        self.service.change("42", ERP_CONFIRMED, "Максим")

        def unavailable(*_args):
            raise TimeoutError("secret details must not escape")

        self.assertFalse(self.service.sync_one("42", unavailable))
        queue = self.rows("SELECT * FROM erp_order_status_sync_queue")
        self.assertEqual(queue[0]["last_error"], "BITRIX_TIMEOUTERROR")
        self.assertNotIn("secret", queue[0]["last_error"])

    def test_successful_retry_is_idempotent_and_clears_queue(self):
        self.service.ingest("42", "N")
        self.service.change("42", ERP_CONFIRMED, "Максим")
        sent = []
        sender = lambda order_id, status: (
            sent.append((order_id, status)) or {"status": "ok"}
        )
        self.assertTrue(self.service.sync_one("42", sender))
        self.assertTrue(self.service.sync_one("42", sender))
        self.assertEqual(sent, [("42", "A")])
        self.assertEqual(self.service.get("42")["sync_status"], "synced")

    def test_incoming_sync_updates_status_without_creating_echo(self):
        self.service.ingest("42", "N")
        state = self.service.ingest("42", "A")
        self.assertEqual(state["erp_status"], ERP_CONFIRMED)
        self.assertEqual(self.rows("SELECT * FROM erp_order_status_sync_queue"), [])
        events = self.rows(
            "SELECT * FROM erp_order_status_events WHERE external_order_id='42'"
        )
        self.assertEqual(events[-1]["source"], "bitrix")

    def test_unknown_bitrix_status_is_logged_and_preserves_erp_status(self):
        self.service.ingest("42", "A")
        state = self.service.ingest("42", "CUSTOM")
        self.assertEqual(state["erp_status"], ERP_CONFIRMED)
        self.assertEqual(state["sync_status"], "unknown")
        event = self.rows(
            "SELECT * FROM erp_order_status_events "
            "WHERE external_order_id='42' ORDER BY id DESC LIMIT 1"
        )[0]
        self.assertEqual(event["sync_result"], "unknown_status")
        self.assertEqual(event["bitrix_status"], "CUSTOM")

    def test_unknown_incoming_status_does_not_hide_pending_local_sync(self):
        self.service.ingest("42", "N")
        self.service.change("42", ERP_CONFIRMED, "Максим")
        state = self.service.ingest("42", "CUSTOM")
        self.assertEqual(state["erp_status"], ERP_CONFIRMED)
        self.assertEqual(state["sync_status"], "pending")

    def test_assembled_is_final_for_stale_incoming_status(self):
        self.service.ingest("42", "A")
        with self.database.transaction() as connection:
            self.insert_sale(connection)
            self.service.change(
                "42", ERP_ASSEMBLED, "Максим", sale_id="sale-1",
                connection=connection,
            )
        self.service.sync_one("42", lambda *_: {"status": "ok"})
        state = self.service.ingest("42", "A")
        self.assertEqual(state["erp_status"], ERP_ASSEMBLED)

    def test_existing_assembled_order_can_be_linked_to_recovered_sale(self):
        self.service.ingest("42", "D")
        with self.database.transaction() as connection:
            self.insert_sale(connection, sale_id="sale-recovered")
            state = self.service.change(
                "42", ERP_ASSEMBLED, "Максим",
                sale_id="sale-recovered", connection=connection,
            )

        self.assertEqual(state["erp_status"], ERP_ASSEMBLED)
        self.assertEqual(state["sale_id"], "sale-recovered")


class OrderStatusFrontendContractTests(unittest.TestCase):
    def test_orders_template_has_exact_status_actions_and_mobile_layout(self):
        template = (
            Path(__file__).resolve().parents[1] / "app/templates/orders.html"
        ).read_text(encoding="utf-8")
        self.assertIn("('N','Не подтверждён')", template)
        self.assertIn("('A','Подтверждён')", template)
        self.assertIn("('D','Собран')", template)
        self.assertIn(">Подтвердить заказ</button>", template)
        self.assertIn(">Провести продажу</button>", template)
        self.assertNotIn("Отметить собранным", template)
        self.assertNotIn("Не дозвонились", template)
        self.assertIn("@media (max-width:780px)", template)


if __name__ == "__main__":
    unittest.main()
