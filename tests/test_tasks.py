import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain_schema_migrations import (
    apply_domain_migrations,
    domain_snapshot,
    validate_tasks_database,
)
from app.services.tasks import (
    MOSCOW_TIMEZONE,
    TaskStore,
    TaskValidationError,
    moscow_today,
)


class TaskStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "tasks.db"
        apply_domain_migrations(self.path, "tasks", "test")
        self.store = TaskStore(self.path)
        self.users = {1, 2}
        self.entities = {
            (kind, "1"): {"id": "1", "label": "{} объект".format(kind),
                           "href": "/app/{}s/1".format(kind)}
            for kind in ("customer", "order", "sale", "repair", "product")
        }

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, **overrides):
        payload = {"title": "Вернуться к клиенту", "description": "Обсудить возврат",
                   "section": "inbox", "priority": "other", "assignee_id": 1}
        payload.update(overrides)
        return self.store.create(
            payload, 1, lambda value: int(value) in self.users,
            lambda kind, value: self.entities.get((kind, str(value))),
        )[0]

    def test_undated_sections_and_automatic_date_views(self):
        self.create(title="Входящие", section="inbox")
        self.create(title="В любое время", section="anytime")
        self.create(title="Когда-нибудь", section="someday")
        self.create(title="Сегодня", due_date="2026-08-27")
        self.create(title="Просрочено", due_date="2026-08-26")
        self.create(title="Планы", due_date="2026-08-28")
        self.assertEqual([row["title"] for row in self.store.list("inbox", today="2026-08-27")["rows"]], ["Входящие"])
        self.assertEqual([row["title"] for row in self.store.list("anytime", today="2026-08-27")["rows"]], ["В любое время"])
        self.assertEqual([row["title"] for row in self.store.list("someday", today="2026-08-27")["rows"]], ["Когда-нибудь"])
        self.assertEqual({row["title"] for row in self.store.list("today", today="2026-08-27")["rows"]}, {"Сегодня", "Просрочено"})
        self.assertEqual([row["title"] for row in self.store.list("plans", today="2026-08-27")["rows"]], ["Планы"])

    def test_today_is_sorted_overdue_then_priority_and_time(self):
        self.create(title="Другое", due_date="2026-08-27", due_time="09:00", priority="other")
        self.create(title="Важно", due_date="2026-08-27", due_time="13:00", priority="important")
        self.create(title="Срочно поздно", due_date="2026-08-27", due_time="12:00", priority="urgent")
        self.create(title="Срочно рано", due_date="2026-08-27", due_time="08:00", priority="urgent")
        self.create(title="Просрочено", due_date="2026-08-26", priority="other")
        titles = [row["title"] for row in self.store.list("today", today="2026-08-27")["rows"]]
        self.assertEqual(titles, ["Просрочено", "Срочно рано", "Срочно поздно", "Важно", "Другое"])

    def test_complete_logbook_reopen_edit_and_move(self):
        task = self.create(due_date="2026-08-27", priority="important")
        completed = self.store.set_completed(task["id"], True, 2)
        self.assertTrue(completed["completed"])
        self.assertEqual(self.store.list("today", today="2026-08-27")["total"], 0)
        self.assertEqual(self.store.list("logbook")["rows"][0]["id"], task["id"])
        reopened = self.store.set_completed(task["id"], False, 1)
        self.assertFalse(reopened["completed"])
        edited = self.store.update(
            task["id"], {"title": "Новое название", "assignee_id": 2}, 1,
            lambda value: int(value) in self.users,
            lambda kind, value: self.entities.get((kind, str(value))),
        )
        self.assertEqual((edited["title"], edited["assignee_id"]), ("Новое название", 2))
        moved = self.store.move(task["id"], "someday", 1)
        self.assertIsNone(moved["due_date"])
        self.assertEqual(self.store.list("someday")["total"], 1)

    def test_all_entity_types_validate_and_search_snapshot(self):
        for kind in ("customer", "order", "sale", "repair", "product"):
            task = self.create(title=kind, entity_type=kind, entity_id="1")
            self.assertEqual(task["entity_label"], "{} объект".format(kind))
            self.assertEqual(self.store.list("inbox", query=kind)["total"], 1)
        with self.assertRaisesRegex(TaskValidationError, "не найдена"):
            self.create(entity_type="customer", entity_id="404")

    def test_filters_counts_pagination_and_idempotency(self):
        for index in range(7):
            self.create(title="Задача {}".format(index), due_date="2026-08-28",
                        priority="urgent" if index % 2 else "other", assignee_id=2)
        self.create(title="Сегодня", due_date="2026-08-27")
        self.create(title="Просрочено", due_date="2026-08-26")
        self.assertEqual(self.store.counts("2026-08-27"), {"today": 1, "overdue": 1, "active": 2})
        page = self.store.list("plans", assignee_id=2, priority="urgent", page=2, per_page=2, today="2026-08-27")
        self.assertEqual((page["total"], page["page"], page["pages"], len(page["rows"])), (3, 2, 2, 1))
        first, created = self.store.create(
            {"title": "Один раз", "section": "inbox", "priority": "other", "assignee_id": 1,
             "idempotency_key": "same-request"}, 1, lambda value: True, lambda kind, value: None)
        second, duplicate_created = self.store.create(
            {"title": "Один раз", "section": "inbox", "priority": "other", "assignee_id": 1,
             "idempotency_key": "same-request"}, 1, lambda value: True, lambda kind, value: None)
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first["id"], second["id"])

    def test_validation_rejects_bad_user_enum_time_and_relation(self):
        for payload in (
            {"title": "", "assignee_id": 1},
            {"title": "X", "assignee_id": 99},
            {"title": "X", "assignee_id": 1, "priority": "critical"},
            {"title": "X", "assignee_id": 1, "due_time": "10:00"},
            {"title": "X", "assignee_id": 1, "entity_type": "customer"},
        ):
            with self.subTest(payload=payload), self.assertRaises(TaskValidationError):
                self.store.create(payload, 1, lambda value: int(value) in self.users,
                                  lambda kind, value: None)

    def test_moscow_day_boundary(self):
        before_midnight_utc = datetime(2026, 8, 26, 21, 30, tzinfo=timezone.utc)
        self.assertEqual(before_midnight_utc.astimezone(MOSCOW_TIMEZONE).hour, 0)
        self.assertEqual(moscow_today(before_midnight_utc), "2026-08-27")

    def test_migration_is_idempotent_and_runtime_store_emits_no_ddl(self):
        first = domain_snapshot(self.path, "tasks")
        second = apply_domain_migrations(self.path, "tasks", "repeat")
        self.assertEqual(first["schema_fingerprint"], second["schema_fingerprint"])
        validate_tasks_database(self.path)
        statements = []
        original = sqlite3.connect
        def traced(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        try:
            sqlite3.connect = traced
            TaskStore(self.path).counts("2026-08-27")
        finally:
            sqlite3.connect = original
        self.assertFalse(any(sql.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP ")) for sql in statements))


if __name__ == "__main__":
    unittest.main()
