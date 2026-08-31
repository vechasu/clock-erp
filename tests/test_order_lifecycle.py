import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.order_lifecycle import OrderLifecycle
from app.services.order_status import ERP_ASSEMBLED, ERP_CONFIRMED, OrderStatusService


class OrderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(
            Path(self.temp.name) / "catalog.sqlite3",
            cache_initialization=False,
        )
        self.statuses = OrderStatusService(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def audit_rows(self, order_id="42"):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM erp_audit_events WHERE entity_type='order' "
                "AND entity_id=? ORDER BY occurred_at,id",
                (order_id,),
            ).fetchall()]

    def test_creation_and_every_real_transition_are_immutable_audit_facts(self):
        self.statuses.ingest(
            "42", "N", created_at="2020-08-31T20:59:50+00:00",
            order_number="20078",
        )
        actor = {
            "actor_id": "7", "actor_name": "Максим", "actor_type": "user",
        }
        self.statuses.record_synced_change("42", ERP_CONFIRMED, actor, "20078")
        self.statuses.record_synced_change("42", ERP_ASSEMBLED, actor, "20078")
        self.statuses.record_synced_change("42", ERP_CONFIRMED, actor, "20078")
        self.statuses.record_synced_change("42", ERP_ASSEMBLED, actor, "20078")
        self.statuses.record_synced_change("42", ERP_ASSEMBLED, actor, "20078")

        rows = self.audit_rows()
        self.assertEqual([row["action"] for row in rows], [
            "created", "status_changed", "status_changed",
            "status_changed", "status_changed",
        ])
        self.assertEqual(rows[0]["occurred_at"], "2020-08-31T20:59:50+00:00")
        self.assertEqual(rows[0]["object_label_snapshot"], "Заказ №20078")
        self.assertEqual({row["actor_id"] for row in rows[1:]}, {"7"})
        self.assertEqual({row["actor_display_name_snapshot"] for row in rows[1:]}, {"Максим"})
        with self.database.connect() as connection:
            duplicate_count = connection.execute(
                "SELECT COUNT(*) FROM erp_order_status_events "
                "WHERE external_order_id='42'"
            ).fetchone()[0]
        self.assertEqual(duplicate_count, 0)
        found = AuditJournal(self.database).list_events(
            entity_type="order", query="20078", limit=20,
        )
        self.assertEqual(len(found["events"]), 5)

    def test_unknown_legacy_creation_time_does_not_invent_an_event(self):
        self.statuses.ingest("legacy", "A")
        self.assertEqual(self.audit_rows("legacy"), [])
        self.statuses.record_synced_change("legacy", ERP_ASSEMBLED, "Максим")
        self.statuses.record_synced_change("legacy", ERP_CONFIRMED, "Максим")
        self.assertEqual(
            OrderLifecycle(self.database).timeline("legacy")["total_display"],
            "",
        )

    def test_two_users_and_system_are_attributed_distinctly(self):
        self.statuses.ingest("42", "N")
        self.statuses.record_synced_change("42", ERP_CONFIRMED, {
            "actor_id": "7", "actor_name": "Максим", "actor_type": "user",
        })
        self.statuses.record_synced_change("42", ERP_ASSEMBLED, {
            "actor_id": "8", "actor_name": "Сотрудник 2", "actor_type": "user",
        })
        self.statuses.record_synced_change("42", ERP_CONFIRMED, {
            "actor_id": "", "actor_name": "", "actor_type": "system",
        })
        rows = self.audit_rows()
        self.assertEqual(
            [(row["actor_id"], row["actor_display_name_snapshot"], row["actor_type"])
             for row in rows],
            [("7", "Максим", "user"), ("8", "Сотрудник 2", "user"),
             (None, "Система", "system")],
        )

    def test_audit_failure_rolls_back_the_status_change(self):
        self.statuses.ingest("42", "N")
        with mock.patch.object(AuditJournal, "record", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.statuses.change("42", ERP_CONFIRMED, "Максим")
        self.assertEqual(self.statuses.get("42")["erp_status"], "unconfirmed")

    def test_sale_and_order_timeline_use_the_same_audit_record(self):
        self.statuses.ingest(
            "42", "N", created_at="2026-08-31T20:59:50+00:00"
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_sales (id,source,external_order_id,status,created_at,"
                "metadata_json,inserted_at,updated_at) VALUES "
                "('sale-1','tictactoy','42','completed',?,'{}',?,?)",
                (
                    "2026-09-01T00:00:10+00:00",
                    "2026-09-01T00:00:10+00:00",
                    "2026-09-01T00:00:10+00:00",
                ),
            )
            sale_event_id = AuditJournal(self.database).record(
                "sale", "sale-1", "created", "Продажа #20078", "tictactoy",
                after={"status": "completed", "order_number": "20078"},
                metadata={"number": "20078", "external_order_id": "42"},
                actor_id="7", actor_name="Максим",
                occurred_at="2026-09-01T00:00:10+00:00",
                status="completed", source="tictactoy", connection=connection,
            )

        timeline = OrderLifecycle(self.database).timeline("42")
        self.assertEqual([event["id"] for event in timeline["events"]][-1], sale_event_id)
        self.assertEqual(timeline["events"][-1]["title"], "Продажа проведена")
        self.assertEqual(timeline["total_seconds"], 10820)
        self.assertTrue(timeline["has_multiple_days"])

        journal = AuditJournal(self.database).list_events(
            entity_type="order", entity_id="42", limit=20,
        )
        self.assertEqual(
            {event["id"] for event in timeline["events"]},
            {event["id"] for event in journal["events"]},
        )


if __name__ == "__main__":
    unittest.main()
