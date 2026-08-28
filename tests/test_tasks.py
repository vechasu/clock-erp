import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain_schema_migrations import (
    LEDGER_SQL,
    TASKS_INDEX_STATEMENTS,
    TASKS_MIGRATION,
    TASKS_TABLE_STATEMENTS,
    apply_domain_migrations,
    domain_snapshot,
    validate_tasks_database,
)
from app.services.tasks import (
    MOSCOW_TIMEZONE,
    TaskStore,
    TaskPermissionError,
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
            for kind in ("customer", "order", "sale", "repair", "product", "purchase")
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
        self.assertEqual([row["title"] for row in self.store.list("today", today="2026-08-27")["rows"]], ["Сегодня"])
        self.assertEqual([row["title"] for row in self.store.list("overdue", today="2026-08-27")["rows"]], ["Просрочено"])
        self.assertEqual([row["title"] for row in self.store.list("plans", today="2026-08-27")["rows"]], ["Планы"])

    def test_today_is_sorted_overdue_then_priority_and_time(self):
        self.create(title="Другое", due_date="2026-08-27", due_time="09:00", priority="other")
        self.create(title="Важно", due_date="2026-08-27", due_time="13:00", priority="important")
        self.create(title="Срочно поздно", due_date="2026-08-27", due_time="12:00", priority="urgent")
        self.create(title="Срочно рано", due_date="2026-08-27", due_time="08:00", priority="urgent")
        self.create(title="Просрочено", due_date="2026-08-26", priority="other")
        titles = [row["title"] for row in self.store.list("today", today="2026-08-27")["rows"]]
        self.assertEqual(set(titles), {"Срочно рано", "Срочно поздно", "Важно", "Другое"})
        self.assertEqual(self.store.list("overdue", today="2026-08-27")["rows"][0]["title"], "Просрочено")

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
        for kind in ("customer", "order", "sale", "repair", "product", "purchase"):
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
        counts = self.store.counts("2026-08-27")
        self.assertEqual((counts["today"], counts["overdue"], counts["active"]), (1, 1, 2))
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

    def test_calendar_range_scope_filters_completed_undated_and_stable_order(self):
        mine = self.create(title="Моя срочная", due_date="2026-08-27", due_time="09:00",
                           priority="urgent", assignee_id=1)
        created = self.create(title="Поставлена мной", due_date="2026-08-28", assignee_id=2)
        self.create(title="За диапазоном", due_date="2026-09-10")
        undated = self.create(title="Без даты", section="anytime", assignee_id=1)
        completed = self.create(title="Готово", due_date="2026-08-29")
        self.store.set_status(completed["id"], "completed", 1)
        result = self.store.calendar("2026-08-24", "2026-08-30", scope="all", current_user_id=1)
        self.assertEqual([row["id"] for row in result["rows"]], [mine["id"], created["id"]])
        self.assertEqual([row["id"] for row in result["undated"]], [undated["id"]])
        self.assertEqual(result["undated_total"], 1)
        mine_scope = self.store.calendar("2026-08-24", "2026-08-30", scope="mine", current_user_id=1)
        self.assertEqual([row["id"] for row in mine_scope["rows"]], [mine["id"]])
        created_scope = self.store.calendar("2026-08-24", "2026-08-30", scope="assigned_by_me", current_user_id=1)
        self.assertEqual({row["id"] for row in created_scope["rows"]}, {mine["id"], created["id"]})
        with_completed = self.store.calendar(
            "2026-08-24", "2026-08-30", query="готово", include_completed=True,
            scope="all", current_user_id=1,
        )
        self.assertEqual([row["id"] for row in with_completed["rows"]], [completed["id"]])

    def test_calendar_waiting_date_reschedule_time_history_and_permissions(self):
        task = self.create(title="Ждём", status="waiting", waiting_for="Ответ",
                           check_date="2026-08-27", due_date="2026-08-27",
                           due_time="11:30", assignee_id=2)
        result = self.store.calendar("2026-08-24", "2026-08-30", current_user_id=1)
        self.assertEqual(result["rows"][0]["calendar_date"], "2026-08-27")
        moved = self.store.calendar_reschedule(
            task["id"], "2026-08-28", "16:00", 1, expected_version=task["version"]
        )
        self.assertEqual((moved["check_date"], moved["due_time"]), ("2026-08-28", "16:00"))
        event = moved["history"][0]
        self.assertEqual(event["event_type"], "date_changed")
        self.assertEqual(event["details"]["from"], "2026-08-27")
        self.assertEqual(event["details"]["to"], "2026-08-28")
        self.assertEqual(event["actor_id"], 1)
        with self.assertRaisesRegex(TaskValidationError, "обязательна"):
            self.store.calendar_reschedule(task["id"], None, None, 1,
                                           expected_version=moved["version"])
        foreign = self.store.create(
            {"title": "Чужая", "assignee_id": 2, "due_date": "2026-08-27"}, 2,
            lambda value: True, lambda kind, value: None,
        )[0]
        with self.assertRaises(TaskPermissionError):
            self.store.calendar_reschedule(foreign["id"], "2026-08-29", None, 1)

    def test_calendar_range_validation_boundaries_and_no_duplicates(self):
        first = self.create(title="Начало", due_date="2026-08-01")
        last = self.create(title="Конец", due_date="2026-08-31")
        result = self.store.calendar("2026-08-01", "2026-08-31", current_user_id=1)
        self.assertEqual([row["id"] for row in result["rows"]], [first["id"], last["id"]])
        self.assertEqual(len({row["id"] for row in result["rows"]}), len(result["rows"]))
        for start, end in (("bad", "2026-08-31"), ("2026-09-01", "2026-08-31"),
                           ("2026-01-01", "2026-04-01")):
            with self.subTest(start=start, end=end), self.assertRaises(TaskValidationError):
                self.store.calendar(start, end, current_user_id=1)

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

    def test_waiting_views_multiple_links_contacts_search_and_history(self):
        task = self.create(
            title="Ответить без ФИО", status="waiting", waiting_for="Оплату клиента",
            check_date="2026-08-27", source_comment="Нужна синяя модель",
            contact_phone="+7 999 111-22-33", contact_channel="WhatsApp",
            links=[{"entity_type": "customer", "entity_id": "1"},
                   {"entity_type": "purchase", "entity_id": "1"}],
        )
        self.assertEqual(len(task["links"]), 2)
        self.assertEqual(self.store.list("waiting", today="2026-08-27")["total"], 1)
        self.assertEqual(self.store.list("today", today="2026-08-27")["total"], 1)
        self.assertEqual(self.store.list("inbox", query="999111")["total"], 1)
        updated = self.store.update(
            task["id"], {"description": "Уточнить размер", "links": [
                {"entity_type": "purchase", "entity_id": "1"}]}, 2,
            lambda value: True,
            lambda kind, value: self.entities.get((kind, str(value))),
        )
        event_types = {event["event_type"] for event in updated["history"]}
        self.assertTrue({"created", "link_added", "link_removed", "field_changed"}.issubset(event_types))

    def test_waiting_check_becomes_overdue_without_status_change(self):
        task = self.create(status="waiting", waiting_for="Поставщика", check_date="2026-08-26")
        self.assertEqual(self.store.list("overdue", today="2026-08-27")["rows"][0]["id"], task["id"])
        self.assertEqual(self.store.get(task["id"])["status"], "waiting")

    def test_completion_result_recurrence_is_atomic_and_idempotent(self):
        task = self.create(due_date="2026-08-01", repeat_type="daily")
        completed = self.store.set_status(task["id"], "completed", 2, "Позвонили клиенту", today="2026-08-27")
        repeated = self.store.set_status(task["id"], "completed", 2, "Повтор", today="2026-08-27")
        self.assertEqual(completed["next_task_id"], repeated.get("next_task_id"))
        next_task = self.store.get(completed["next_task_id"])
        self.assertEqual(next_task["due_date"], "2026-08-28")
        self.assertEqual(self.store.get(task["id"])["completion_result"], "Позвонили клиенту")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 2)

    def test_notifications_are_idempotent(self):
        self.create(due_date="2026-08-26", assignee_id=2)
        first = self.store.generate_notifications(2, "2026-08-27")
        second = self.store.generate_notifications(2, "2026-08-27")
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.notifications(2)), 2)

    def test_v1_migration_preserves_task_and_relation(self):
        legacy = Path(self.temporary.name) / "legacy.db"
        with sqlite3.connect(str(legacy)) as connection:
            connection.execute(LEDGER_SQL)
            for statement in TASKS_TABLE_STATEMENTS + TASKS_INDEX_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO tasks(title,description,section,status,priority,author_id,assignee_id,"
                "entity_type,entity_id,entity_label,entity_href,created_at,updated_at) "
                "VALUES('Старая задача','','inbox','active','other',1,1,'order','42','Заказ №42','/order/42','x','x')"
            )
            connection.execute(
                "INSERT INTO erp_migration_ledger(migration_id,name,checksum,state,applied_at,details_json) "
                "VALUES(?,?,?,'applied','x','{}')",
                (TASKS_MIGRATION["id"], TASKS_MIGRATION["name"], TASKS_MIGRATION["checksum"]),
            )
        apply_domain_migrations(legacy, "tasks", "upgrade")
        migrated = TaskStore(legacy).get(1)
        self.assertEqual((migrated["title"], migrated["status"], migrated["entity_id"]),
                         ("Старая задача", "new", "42"))


if __name__ == "__main__":
    unittest.main()
