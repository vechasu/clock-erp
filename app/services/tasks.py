"""Unified, migration-backed internal ERP task center."""

import calendar
import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain_schema_migrations import validate_tasks_database


MOSCOW_TIMEZONE = timezone(timedelta(hours=3), "Europe/Moscow")
SECTIONS = {"inbox", "anytime", "someday"}
STATUSES = {"new", "in_progress", "waiting", "completed", "cancelled"}
ACTIVE_STATUSES = {"new", "in_progress", "waiting"}
PRIORITIES = {"urgent", "important", "other"}
ENTITY_TYPES = {"customer", "order", "sale", "repair", "product", "purchase"}
VIEWS = {"inbox", "overdue", "today", "plans", "waiting", "anytime", "someday", "logbook"}
REPEAT_TYPES = {"none", "daily", "weekdays", "weekly", "monthly", "custom"}


class TaskValidationError(ValueError):
    def __init__(self, message, field=""):
        super().__init__(message)
        self.field = field


class TaskNotFoundError(LookupError):
    pass


class TaskConflictError(RuntimeError):
    pass


class TaskPermissionError(PermissionError):
    pass


def moscow_today(now=None):
    current = now or datetime.now(MOSCOW_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW_TIMEZONE)
    return current.astimezone(MOSCOW_TIMEZONE).date().isoformat()


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value, maximum):
    return str(value or "").strip()[:maximum]


def _date(value, field="due_date"):
    value = _text(value, 10)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise TaskValidationError("Укажите корректную дату.", field)


def _time(value):
    value = _text(value, 5)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except ValueError:
        raise TaskValidationError("Укажите корректное время.", "due_time")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class TaskStore:
    def __init__(self, path=None):
        configured = path or os.getenv("ERP_TASKS_DATABASE", "").strip()
        self.path = Path(configured) if configured else Path("instance/tasks.db")
        self._validated = False

    def initialize(self):
        if not self._validated:
            validate_tasks_database(self.path)
            self._validated = True
        return self

    def connect(self):
        self.initialize()
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.create_function("erp_casefold", 1, lambda value: str(value or "").casefold())
        connection.create_function("erp_digits", 1, lambda value: "".join(
            character for character in str(value or "") if character.isdigit()
        ))
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @staticmethod
    def normalize(payload, partial=False):
        result = {}
        text_fields = {
            "title": 240, "description": 10000, "source_comment": 10000,
            "contact_name": 240, "contact_phone": 120, "contact_email": 320,
            "contact_channel": 120, "waiting_for": 500, "waiting_comment": 5000,
            "completion_result": 10000,
        }
        for field, maximum in text_fields.items():
            if not partial or field in payload:
                result[field] = _text(payload.get(field), maximum)
        if "title" in result and not result["title"]:
            raise TaskValidationError("Название задачи обязательно.", "title")
        for field, allowed, default in (
            ("section", SECTIONS, "inbox"), ("status", STATUSES, "new"),
            ("priority", PRIORITIES, "other"), ("repeat_type", REPEAT_TYPES, "none"),
        ):
            if not partial or field in payload:
                value = _text(payload.get(field) or default, 30)
                if value not in allowed:
                    raise TaskValidationError("Недопустимое значение.", field)
                result[field] = value
        for field in ("due_date", "check_date"):
            if not partial or field in payload:
                result[field] = _date(payload.get(field), field)
        if not partial or "due_time" in payload:
            result["due_time"] = _time(payload.get("due_time"))
        if not partial or "reminder_at" in payload:
            result["reminder_at"] = _text(payload.get("reminder_at"), 32) or None
        if not partial or "assignee_id" in payload:
            try:
                result["assignee_id"] = int(payload.get("assignee_id"))
            except (TypeError, ValueError):
                raise TaskValidationError("Выберите ответственного.", "assignee_id")
            if result["assignee_id"] < 1:
                raise TaskValidationError("Выберите ответственного.", "assignee_id")
        if not partial or "repeat_interval" in payload:
            try:
                interval = int(payload.get("repeat_interval") or 1)
            except (TypeError, ValueError):
                raise TaskValidationError("Интервал должен быть целым числом.", "repeat_interval")
            if interval < 1 or interval > 365:
                raise TaskValidationError("Интервал должен быть от 1 до 365.", "repeat_interval")
            result["repeat_interval"] = interval
        return result

    @staticmethod
    def normalize_links(payload):
        raw = payload.get("links")
        if raw is None and (payload.get("entity_type") or payload.get("entity_id")):
            raw = [{"entity_type": payload.get("entity_type"), "entity_id": payload.get("entity_id")}]
        if raw is None:
            return None
        if not isinstance(raw, list) or len(raw) > 20:
            raise TaskValidationError("Передан некорректный список связей.", "links")
        result, seen = [], set()
        for item in raw:
            if not isinstance(item, dict):
                raise TaskValidationError("Передана некорректная связь.", "links")
            entity_type = _text(item.get("entity_type") or item.get("type"), 20)
            entity_id = _text(item.get("entity_id") or item.get("id"), 120)
            if entity_type not in ENTITY_TYPES or not entity_id:
                raise TaskValidationError("Передана некорректная связь.", "links")
            key = (entity_type, entity_id)
            if key not in seen:
                seen.add(key)
                result.append(key)
        return result

    @staticmethod
    def _history(connection, task_id, event_type, actor_id, details=None, created_at=None):
        connection.execute(
            "INSERT INTO task_history(task_id,event_type,actor_id,created_at,details_json) VALUES(?,?,?,?,?)",
            (int(task_id), event_type, int(actor_id), created_at or utc_now(), _json(details or {})),
        )

    @staticmethod
    def _resolve_links(links, resolver):
        resolved = []
        for entity_type, entity_id in links or []:
            entity = resolver(entity_type, entity_id)
            if not entity:
                raise TaskValidationError("Связанная сущность не найдена или недоступна.", "links")
            resolved.append({"entity_type": entity_type, "entity_id": entity_id,
                             "entity_label": _text(entity.get("label"), 500),
                             "entity_href": _text(entity.get("href"), 1000)})
        return resolved

    def _replace_links(self, connection, task_id, links, actor_id, now, emit_history=True):
        old = {(str(row[0]), str(row[1])) for row in connection.execute(
            "SELECT entity_type,entity_id FROM task_links WHERE task_id=?", (int(task_id),)
        ).fetchall()}
        new = {(item["entity_type"], item["entity_id"]) for item in links}
        connection.execute("DELETE FROM task_links WHERE task_id=?", (int(task_id),))
        for item in links:
            connection.execute(
                "INSERT INTO task_links(task_id,entity_type,entity_id,entity_label,entity_href,created_at,created_by) "
                "VALUES(?,?,?,?,?,?,?)",
                (int(task_id), item["entity_type"], item["entity_id"], item["entity_label"],
                 item["entity_href"], now, int(actor_id)),
            )
        if emit_history:
            for key in sorted(new - old):
                self._history(connection, task_id, "link_added", actor_id,
                              {"entity_type": key[0], "entity_id": key[1]}, now)
            for key in sorted(old - new):
                self._history(connection, task_id, "link_removed", actor_id,
                              {"entity_type": key[0], "entity_id": key[1]}, now)

    @staticmethod
    def _serialize(row, links=None, history=None):
        task = dict(row)
        task["completed"] = task["status"] == "completed"
        task["links"] = links or []
        task["history"] = history or []
        first = task["links"][0] if task["links"] else {}
        task["entity_type"] = first.get("entity_type")
        task["entity_id"] = first.get("entity_id")
        task["entity_label"] = first.get("entity_label")
        task["entity_href"] = first.get("entity_href")
        return task

    def _enrich(self, connection, rows, include_history=False):
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        link_rows = connection.execute(
            "SELECT task_id,entity_type,entity_id,entity_label,entity_href FROM task_links "
            "WHERE task_id IN ({}) ORDER BY id".format(marks), ids
        ).fetchall()
        links = {task_id: [] for task_id in ids}
        for row in link_rows:
            links[int(row["task_id"])].append(dict(row))
        histories = {task_id: [] for task_id in ids}
        if include_history:
            event_rows = connection.execute(
                "SELECT id,task_id,event_type,actor_id,created_at,details_json FROM task_history "
                "WHERE task_id IN ({}) ORDER BY id DESC".format(marks), ids
            ).fetchall()
            for row in event_rows:
                item = dict(row)
                try:
                    item["details"] = json.loads(item.pop("details_json"))
                except (TypeError, ValueError):
                    item["details"] = {}
                histories[int(row["task_id"])].append(item)
        return [self._serialize(row, links[int(row["id"])], histories[int(row["id"])]) for row in rows]

    def create(self, payload, actor_id, user_exists, entity_resolver, collaboration=None,
               actor=None):
        values = self.normalize(payload)
        if not user_exists(values["assignee_id"]):
            raise TaskValidationError("Ответственный сотрудник не найден.", "assignee_id")
        links = self._resolve_links(self.normalize_links(payload) or [], entity_resolver)
        if values["due_time"] and not values["due_date"]:
            raise TaskValidationError("Для времени укажите дату.", "due_date")
        if values["status"] == "waiting" and (not values["waiting_for"] or not values["check_date"]):
            raise TaskValidationError("Укажите, кого ожидаем, и дату следующей проверки.", "waiting_for")
        now = utc_now()
        key = _text(payload.get("idempotency_key"), 120) or None
        series_id = _text(payload.get("series_id"), 120) or None
        with self.connect() as connection:
            try:
                if collaboration is not None:
                    collaboration.prepare(connection)
                cursor = connection.execute(
                    "INSERT INTO tasks(title,description,section,status,priority,due_date,due_time,reminder_at,"
                    "author_id,assignee_id,source_comment,contact_name,contact_phone,contact_email,contact_channel,"
                    "waiting_for,check_date,waiting_comment,repeat_type,repeat_interval,series_id,created_at,updated_at,idempotency_key) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (values["title"], values["description"], values["section"], values["status"],
                     values["priority"], values["due_date"], values["due_time"], values["reminder_at"],
                     int(actor_id), values["assignee_id"], values["source_comment"], values["contact_name"],
                     values["contact_phone"], values["contact_email"], values["contact_channel"],
                     values["waiting_for"], values["check_date"], values["waiting_comment"],
                     values["repeat_type"], values["repeat_interval"], series_id, now, now, key),
                )
                task_id = cursor.lastrowid
                if values["repeat_type"] != "none" and not series_id:
                    series_id = "task-series-{}-{}".format(task_id, uuid.uuid4().hex)
                    connection.execute("UPDATE tasks SET series_id=? WHERE id=?", (series_id, task_id))
                self._replace_links(connection, task_id, links, actor_id, now, False)
                self._history(connection, task_id, "created", actor_id, {"status": values["status"]}, now)
                for link in links:
                    self._history(connection, task_id, "link_added", actor_id,
                                  {"entity_type": link["entity_type"], "entity_id": link["entity_id"]}, now)
                if values["assignee_id"] != int(actor_id):
                    self._notification(connection, task_id, values["assignee_id"], "assigned", "assigned", now)
                if collaboration is not None:
                    collaboration.record_assignment(
                        connection, "task", str(task_id), None, values["assignee_id"],
                        actor or {"id": actor_id}, values["title"],
                        "/app/tasks?task_id={}".format(task_id),
                        operation_key=key or "task-create:{}".format(task_id), created_at=now,
                    )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                if not key:
                    raise
                row = connection.execute("SELECT id FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
                if not row:
                    raise
                return self.get(row[0]), False
        return self.get(task_id), True

    def get(self, task_id, include_deleted=False):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=?" +
                ("" if include_deleted else " AND deleted_at IS NULL"),
                (int(task_id),),
            ).fetchone()
            if row is None:
                raise TaskNotFoundError("Задача не найдена.")
            return self._enrich(connection, [row], True)[0]

    def soft_delete(self, task_id, actor_id, actor_role="employee"):
        try:
            actor_id = int(actor_id)
        except (TypeError, ValueError):
            raise TaskPermissionError("У вас нет права удалить эту задачу.")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=?", (int(task_id),)
            ).fetchone()
            if row is None:
                raise TaskNotFoundError("Задача не найдена.")
            if row["deleted_at"]:
                raise TaskConflictError("Задача уже удалена.")
            if actor_role != "admin" and actor_id not in {
                    int(row["author_id"]), int(row["assignee_id"])}:
                raise TaskPermissionError("У вас нет права удалить эту задачу.")
            now = utc_now()
            cursor = connection.execute(
                "UPDATE tasks SET deleted_at=?,deleted_by=?,updated_at=?,updated_by=?,version=version+1 "
                "WHERE id=? AND deleted_at IS NULL",
                (now, actor_id, now, actor_id, int(task_id)),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise TaskConflictError("Задача уже удалена.")
            self._history(connection, task_id, "deleted", actor_id, {}, now)
            connection.execute(
                "UPDATE task_notifications SET seen_at=? WHERE task_id=? AND seen_at IS NULL",
                (now, int(task_id)),
            )
            connection.commit()
        return self.get(task_id, include_deleted=True)

    def update(self, task_id, payload, actor_id, user_exists, entity_resolver,
               collaboration=None, actor=None):
        current = self.get(task_id)
        if current["status"] in {"completed", "cancelled"} and "status" not in payload:
            raise TaskValidationError("Сначала восстановите задачу.", "status")
        values = self.normalize(payload, partial=True)
        links_raw = self.normalize_links(payload)
        links = self._resolve_links(links_raw, entity_resolver) if links_raw is not None else None
        merged = dict(current)
        merged.update(values)
        if not user_exists(merged["assignee_id"]):
            raise TaskValidationError("Ответственный сотрудник не найден.", "assignee_id")
        if merged.get("due_time") and not merged.get("due_date"):
            raise TaskValidationError("Для времени укажите дату.", "due_date")
        if merged["status"] == "waiting" and (not merged.get("waiting_for") or not merged.get("check_date")):
            raise TaskValidationError("Укажите, кого ожидаем, и дату следующей проверки.", "waiting_for")
        editable = ("title", "description", "section", "status", "priority", "due_date", "due_time",
                    "reminder_at", "assignee_id", "source_comment", "contact_name", "contact_phone",
                    "contact_email", "contact_channel", "waiting_for", "check_date", "waiting_comment",
                    "repeat_type", "repeat_interval", "completion_result")
        changed = {field: {"from": current.get(field), "to": merged.get(field)}
                   for field in editable if current.get(field) != merged.get(field)}
        now = utc_now()
        with self.connect() as connection:
            if collaboration is not None and "assignee_id" in changed:
                collaboration.prepare(connection)
            assignments = ",".join("{}=?".format(field) for field in editable)
            expected_version = int(payload.get("version") or current.get("version") or 1)
            cursor = connection.execute(
                "UPDATE tasks SET {},updated_at=?,updated_by=?,version=version+1 "
                "WHERE id=? AND version=? AND deleted_at IS NULL".format(assignments),
                [merged.get(field) for field in editable] + [now, int(actor_id), int(task_id), expected_version],
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise TaskConflictError(
                    "Эта запись была изменена другим сотрудником после того, как вы её открыли."
                )
            if links is not None:
                self._replace_links(connection, task_id, links, actor_id, now)
            for field, change in changed.items():
                event = "status_changed" if field == "status" else "field_changed"
                self._history(connection, task_id, event, actor_id,
                              {"field": field, "from": change["from"], "to": change["to"]}, now)
            if "assignee_id" in changed and merged["assignee_id"] != int(actor_id):
                self._notification(connection, task_id, merged["assignee_id"], "assigned", now, now)
            if collaboration is not None and "assignee_id" in changed:
                collaboration.record_assignment(
                    connection, "task", str(task_id), changed["assignee_id"]["from"],
                    merged["assignee_id"], actor or {"id": actor_id}, merged["title"],
                    "/app/tasks?task_id={}".format(task_id),
                    comment=payload.get("assignment_comment", ""),
                    operation_key=payload.get("assignment_operation_key") or
                    payload.get("idempotency_key") or uuid.uuid4().hex,
                    created_at=now,
                )
            connection.commit()
        return self.get(task_id)

    @staticmethod
    def _next_date(base_value, repeat_type, interval, today):
        base = datetime.strptime(base_value or today, "%Y-%m-%d").date()
        current = datetime.strptime(today, "%Y-%m-%d").date()
        candidate = base
        while candidate <= current:
            if repeat_type == "daily":
                candidate += timedelta(days=interval)
            elif repeat_type == "weekdays":
                candidate += timedelta(days=1)
                while candidate.weekday() >= 5:
                    candidate += timedelta(days=1)
            elif repeat_type == "weekly":
                candidate += timedelta(days=7 * interval)
            elif repeat_type == "monthly":
                month_index = candidate.year * 12 + candidate.month - 1 + interval
                year, month = divmod(month_index, 12)
                month += 1
                candidate = candidate.replace(year=year, month=month,
                                              day=min(candidate.day, calendar.monthrange(year, month)[1]))
            else:
                candidate += timedelta(days=interval)
        return candidate.isoformat()

    def set_status(self, task_id, status, actor_id, result="", continue_series=False, today=None):
        if status not in STATUSES:
            raise TaskValidationError("Неизвестный статус.", "status")
        current = self.get(task_id)
        if status == "waiting" and (not current["waiting_for"] or not current["check_date"]):
            raise TaskValidationError("Сначала заполните данные ожидания.", "waiting_for")
        now = utc_now()
        next_id = None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=? AND deleted_at IS NULL", (int(task_id),)
            ).fetchone()
            if row is None:
                raise TaskNotFoundError("Задача не найдена.")
            previous = str(row["status"])
            if previous == status and status in {"completed", "cancelled"}:
                task = self.get(task_id)
                occurrence_key = "{}:{}".format(row["series_id"] or task_id, task_id)
                child = connection.execute(
                    "SELECT id FROM tasks WHERE next_occurrence_key=? AND deleted_at IS NULL", (occurrence_key,)
                ).fetchone()
                task["next_task_id"] = int(child[0]) if child else None
                return task
            completed = status == "completed"
            cancelled = status == "cancelled"
            connection.execute(
                "UPDATE tasks SET status=?,previous_status=?,completion_result=?,completed_at=?,completed_by=?,"
                "cancelled_at=?,cancelled_by=?,updated_at=?,updated_by=? "
                "WHERE id=? AND deleted_at IS NULL",
                (status, previous, _text(result, 10000) if completed else row["completion_result"],
                 now if completed else None, int(actor_id) if completed else None,
                 now if cancelled else None, int(actor_id) if cancelled else None, now, int(actor_id), int(task_id)),
            )
            event = "completed" if completed else "cancelled" if cancelled else "restored" if previous in {"completed", "cancelled"} else "status_changed"
            self._history(connection, task_id, event, actor_id,
                          {"from": previous, "to": status, "result": _text(result, 10000)}, now)
            should_repeat = completed or (cancelled and continue_series)
            if should_repeat and row["repeat_type"] != "none":
                occurrence_key = "{}:{}".format(row["series_id"] or task_id, task_id)
                existing = connection.execute(
                    "SELECT id FROM tasks WHERE next_occurrence_key=? AND deleted_at IS NULL",
                    (occurrence_key,),
                ).fetchone()
                if existing:
                    next_id = int(existing[0])
                else:
                    next_due = self._next_date(row["due_date"], row["repeat_type"], row["repeat_interval"], today or moscow_today())
                    cursor = connection.execute(
                        "INSERT INTO tasks(title,description,section,status,priority,due_date,due_time,reminder_at,author_id,assignee_id,"
                        "source_comment,contact_name,contact_phone,contact_email,contact_channel,repeat_type,repeat_interval,series_id,parent_task_id,"
                        "created_at,updated_at,next_occurrence_key) VALUES(?,?,?,'new',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row["title"], row["description"], row["section"], row["priority"], next_due, row["due_time"],
                         row["reminder_at"], row["author_id"], row["assignee_id"], row["source_comment"], row["contact_name"],
                         row["contact_phone"], row["contact_email"], row["contact_channel"], row["repeat_type"],
                         row["repeat_interval"], row["series_id"], int(task_id), now, now, occurrence_key),
                    )
                    next_id = cursor.lastrowid
                    old_links = [dict(item) for item in connection.execute(
                        "SELECT entity_type,entity_id,entity_label,entity_href FROM task_links WHERE task_id=? ORDER BY id",
                        (int(task_id),),
                    ).fetchall()]
                    self._replace_links(connection, next_id, old_links, actor_id, now, False)
                    self._history(connection, next_id, "created_from_recurrence", actor_id,
                                  {"previous_task_id": int(task_id)}, now)
                    self._history(connection, task_id, "next_recurrence_created", actor_id,
                                  {"next_task_id": int(next_id)}, now)
                    self._notification(connection, next_id, row["assignee_id"], "recurrence", occurrence_key, now)
            connection.commit()
        task = self.get(task_id)
        task["next_task_id"] = next_id
        return task

    def set_completed(self, task_id, completed, actor_id, result=""):
        return self.set_status(task_id, "completed" if completed else "new", actor_id, result)

    def move(self, task_id, section, actor_id):
        if section not in SECTIONS:
            raise TaskValidationError("Неизвестный раздел.", "section")
        now = utc_now()
        current = self.get(task_id)
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET section=?,due_date=NULL,due_time=NULL,updated_at=?,updated_by=? "
                "WHERE id=? AND deleted_at IS NULL",
                (section, now, int(actor_id), int(task_id)),
            )
            self._history(connection, task_id, "date_changed", actor_id,
                          {"from": current["due_date"], "to": None, "section": section}, now)
            connection.commit()
        return self.get(task_id)

    def reschedule(self, task_id, due_date, actor_id):
        value = _date(due_date)
        current = self.get(task_id)
        now = utc_now()
        with self.connect() as connection:
            connection.execute("UPDATE tasks SET due_date=?,section='inbox',updated_at=?,updated_by=? "
                               "WHERE id=? AND deleted_at IS NULL",
                               (value, now, int(actor_id), int(task_id)))
            self._history(connection, task_id, "date_changed", actor_id,
                          {"from": current["due_date"], "to": value}, now)
            connection.commit()
        return self.get(task_id)

    @staticmethod
    def _notification(connection, task_id, user_id, kind, key, now):
        connection.execute(
            "INSERT OR IGNORE INTO task_notifications(task_id,user_id,notification_type,notification_key,created_at) "
            "VALUES(?,?,?,?,?)", (int(task_id), int(user_id), kind, str(key), now),
        )

    def generate_notifications(self, user_id, today=None):
        today = today or moscow_today()
        now = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,status,due_date,check_date FROM tasks WHERE assignee_id=? "
                "AND deleted_at IS NULL "
                "AND status IN ('new','in_progress','waiting') AND "
                "((due_date IS NOT NULL AND due_date<=?) OR (status='waiting' AND check_date IS NOT NULL AND check_date<=?))",
                (int(user_id), today, today),
            ).fetchall()
            for row in rows:
                if row["status"] == "waiting" and row["check_date"] and row["check_date"] <= today:
                    kind, key = "waiting_check", row["check_date"]
                else:
                    kind = "overdue" if row["due_date"] < today else "due"
                    key = row["due_date"]
                self._notification(connection, row["id"], user_id, kind, key, now)
            connection.commit()
            return int(connection.execute(
                "SELECT COUNT(*) FROM task_notifications n JOIN tasks t ON t.id=n.task_id "
                "WHERE n.user_id=? AND n.seen_at IS NULL AND t.deleted_at IS NULL",
                (int(user_id),),
            ).fetchone()[0])

    def notifications(self, user_id, mark_seen=False):
        with self.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT n.*,t.title FROM task_notifications n JOIN tasks t ON t.id=n.task_id "
                "WHERE n.user_id=? AND t.deleted_at IS NULL ORDER BY n.id DESC LIMIT 100", (int(user_id),)
            ).fetchall()]
            if mark_seen:
                connection.execute("UPDATE task_notifications SET seen_at=? WHERE user_id=? AND seen_at IS NULL",
                                   (utc_now(), int(user_id)))
                connection.commit()
        return rows

    def counts(self, today=None, assignee_id=None):
        today = today or moscow_today()
        clauses = "deleted_at IS NULL AND status IN ('new','in_progress','waiting')"
        params = []
        if assignee_id:
            clauses += " AND assignee_id=?"
            params.append(int(assignee_id))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT "
                "SUM(CASE WHEN due_date IS NULL AND section='inbox' THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN (due_date<? OR (status='waiting' AND check_date<?)) THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN due_date=? OR (status='waiting' AND check_date=?) THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN due_date>? THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN status='waiting' THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN due_date IS NULL AND section='anytime' THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN due_date IS NULL AND section='someday' THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN (due_date<=? OR (status='waiting' AND check_date<=?)) THEN 1 ELSE 0 END) "
                "FROM tasks WHERE " + clauses,
                [today, today, today, today, today, today, today] + params,
            ).fetchone()
            journal = int(connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE deleted_at IS NULL AND status IN ('completed','cancelled')" +
                (" AND assignee_id=?" if assignee_id else ""), params
            ).fetchone()[0])
        keys = ("inbox", "overdue", "today", "plans", "waiting", "anytime", "someday", "active")
        result = {key: int(row[index] or 0) for index, key in enumerate(keys)}
        result["logbook"] = journal
        return result

    def list(self, view="today", query="", assignee_id=None, priority="", entity_type="",
             status="", due="", only_mine=None, scope="all", current_user_id=None,
             page=1, per_page=50, today=None):
        if view not in VIEWS:
            raise TaskValidationError("Неизвестное представление.", "view")
        today = today or moscow_today()
        clauses, parameters = ["t.deleted_at IS NULL"], []
        if view == "logbook":
            clauses.append("t.status IN ('completed','cancelled')")
        else:
            clauses.append("t.status IN ('new','in_progress','waiting')")
            if view == "overdue":
                clauses.append("(t.due_date<? OR (t.status='waiting' AND t.check_date<?))")
                parameters.extend((today, today))
            elif view == "today":
                clauses.append("(t.due_date=? OR (t.status='waiting' AND t.check_date=?))")
                parameters.extend((today, today))
            elif view == "plans":
                clauses.append("t.due_date>?")
                parameters.append(today)
            elif view == "waiting":
                clauses.append("t.status='waiting'")
            else:
                clauses.append("t.due_date IS NULL AND t.section=?")
                parameters.append(view)
        query = _text(query, 200)
        if query:
            folded = "%{}%".format(query.casefold())
            digit_value = "".join(character for character in query if character.isdigit())
            digits = "%{}%".format(digit_value) if digit_value else "__no_phone_match__"
            clauses.append("(erp_casefold(t.title) LIKE ? OR erp_casefold(t.description) LIKE ? OR "
                           "erp_casefold(t.source_comment) LIKE ? OR erp_casefold(t.completion_result) LIKE ? OR "
                           "erp_casefold(t.contact_name) LIKE ? OR erp_casefold(t.contact_phone) LIKE ? OR "
                           "erp_casefold(t.contact_email) LIKE ? OR erp_digits(t.contact_phone) LIKE ? OR EXISTS(SELECT 1 FROM task_links l WHERE l.task_id=t.id "
                           "AND (erp_casefold(l.entity_label) LIKE ? OR erp_casefold(l.entity_id) LIKE ?)))")
            parameters.extend([folded] * 7 + [digits] + [folded] * 2)
        if assignee_id or only_mine:
            clauses.append("t.assignee_id=?")
            parameters.append(int(assignee_id or only_mine))
        if scope not in {"mine", "created", "team", "all"}:
            raise TaskValidationError("Неизвестная область задач.", "scope")
        if scope == "mine":
            clauses.append("t.assignee_id=?")
            parameters.append(int(current_user_id))
        elif scope == "created":
            clauses.append("t.author_id=?")
            parameters.append(int(current_user_id))
        if priority:
            if priority not in PRIORITIES:
                raise TaskValidationError("Неизвестный приоритет.", "priority")
            clauses.append("t.priority=?")
            parameters.append(priority)
        if status:
            if status not in STATUSES:
                raise TaskValidationError("Неизвестный статус.", "status")
            clauses.append("t.status=?")
            parameters.append(status)
        if entity_type:
            if entity_type not in ENTITY_TYPES:
                raise TaskValidationError("Неизвестный тип связи.", "entity_type")
            clauses.append("EXISTS(SELECT 1 FROM task_links le WHERE le.task_id=t.id AND le.entity_type=?)")
            parameters.append(entity_type)
        if due in {"none", "today", "overdue", "future"}:
            if due == "none": clauses.append("t.due_date IS NULL")
            elif due == "today": clauses.append("t.due_date=?"); parameters.append(today)
            elif due == "overdue": clauses.append("t.due_date<?"); parameters.append(today)
            else: clauses.append("t.due_date>?"); parameters.append(today)
        try:
            page, per_page = max(1, int(page)), max(1, min(int(per_page), 100))
        except (TypeError, ValueError):
            page, per_page = 1, 50
        where = " WHERE " + " AND ".join(clauses)
        if view == "logbook":
            order = " ORDER BY COALESCE(t.completed_at,t.cancelled_at) DESC,t.id DESC"
        elif view in {"today", "overdue", "plans", "waiting"}:
            order = " ORDER BY COALESCE(t.check_date,t.due_date),t.due_time IS NULL,t.due_time,CASE t.priority WHEN 'urgent' THEN 0 WHEN 'important' THEN 1 ELSE 2 END,t.id"
        else:
            order = " ORDER BY t.created_at DESC,t.id DESC"
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM tasks t" + where, parameters).fetchone()[0])
            pages = max(1, int(math.ceil(float(total) / per_page)))
            page = min(page, pages)
            rows = connection.execute("SELECT t.* FROM tasks t" + where + order + " LIMIT ? OFFSET ?",
                                      parameters + [per_page, (page - 1) * per_page]).fetchall()
            serialized = self._enrich(connection, rows, False)
        return {"rows": serialized, "total": total, "page": page, "per_page": per_page,
                "pages": pages, "today": today}

    def calendar(self, start, end, query="", assignee_id=None, priority="", entity_type="",
                 status="", due="", only_mine=None, scope="all", current_user_id=None,
                 include_completed=False, today=None):
        """Return only tasks needed by a bounded calendar window plus undated tasks."""
        start_value = _date(start, "start")
        end_value = _date(end, "end")
        if not start_value or not end_value or start_value > end_value:
            raise TaskValidationError("Укажите корректный диапазон календаря.", "range")
        start_date = datetime.strptime(start_value, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_value, "%Y-%m-%d").date()
        if (end_date - start_date).days > 62:
            raise TaskValidationError("Диапазон календаря не может превышать 63 дня.", "range")
        if scope == "assigned_by_me":
            scope = "created"
        if scope not in {"mine", "created", "team", "all"}:
            raise TaskValidationError("Неизвестная область задач.", "scope")

        clauses, parameters = ["t.deleted_at IS NULL"], []
        if include_completed:
            clauses.append("t.status!='cancelled'")
        else:
            clauses.append("t.status IN ('new','in_progress','waiting')")
        query = _text(query, 200)
        if query:
            folded = "%{}%".format(query.casefold())
            digit_value = "".join(character for character in query if character.isdigit())
            digits = "%{}%".format(digit_value) if digit_value else "__no_phone_match__"
            clauses.append("(erp_casefold(t.title) LIKE ? OR erp_casefold(t.description) LIKE ? OR "
                           "erp_casefold(t.source_comment) LIKE ? OR erp_casefold(t.completion_result) LIKE ? OR "
                           "erp_casefold(t.contact_name) LIKE ? OR erp_casefold(t.contact_phone) LIKE ? OR "
                           "erp_casefold(t.contact_email) LIKE ? OR erp_digits(t.contact_phone) LIKE ? OR EXISTS("
                           "SELECT 1 FROM task_links l WHERE l.task_id=t.id AND "
                           "(erp_casefold(l.entity_label) LIKE ? OR erp_casefold(l.entity_id) LIKE ?)))")
            parameters.extend([folded] * 7 + [digits] + [folded] * 2)
        if assignee_id or only_mine:
            clauses.append("t.assignee_id=?")
            parameters.append(int(assignee_id or only_mine))
        if scope == "mine":
            clauses.append("t.assignee_id=?")
            parameters.append(int(current_user_id))
        elif scope == "created":
            clauses.append("t.author_id=?")
            parameters.append(int(current_user_id))
        if priority:
            if priority not in PRIORITIES:
                raise TaskValidationError("Неизвестный приоритет.", "priority")
            clauses.append("t.priority=?")
            parameters.append(priority)
        if status:
            if status not in STATUSES:
                raise TaskValidationError("Неизвестный статус.", "status")
            clauses.append("t.status=?")
            parameters.append(status)
        if entity_type:
            if entity_type not in ENTITY_TYPES:
                raise TaskValidationError("Неизвестный тип связи.", "entity_type")
            clauses.append("EXISTS(SELECT 1 FROM task_links le WHERE le.task_id=t.id AND le.entity_type=?)")
            parameters.append(entity_type)
        today = today or moscow_today()
        if due in {"none", "today", "overdue", "future"}:
            date_sql = "CASE WHEN t.status='waiting' AND t.check_date IS NOT NULL THEN t.check_date ELSE t.due_date END"
            if due == "none": clauses.append("{} IS NULL".format(date_sql))
            elif due == "today": clauses.append("{}=?".format(date_sql)); parameters.append(today)
            elif due == "overdue": clauses.append("{}<?".format(date_sql)); parameters.append(today)
            else: clauses.append("{}>?".format(date_sql)); parameters.append(today)

        base_where = " AND ".join(clauses)
        calendar_date = "CASE WHEN t.status='waiting' AND t.check_date IS NOT NULL THEN t.check_date ELSE t.due_date END"
        order = (" ORDER BY calendar_date,t.due_time IS NULL,t.due_time,"
                 "CASE t.priority WHEN 'urgent' THEN 0 WHEN 'important' THEN 1 ELSE 2 END,t.id")
        with self.connect() as connection:
            dated = connection.execute(
                "SELECT t.*,{} AS calendar_date FROM tasks t WHERE {} AND {} BETWEEN ? AND ?{}".format(
                    calendar_date, base_where, calendar_date, order
                ), parameters + [start_value, end_value]
            ).fetchall()
            undated_total = int(connection.execute(
                "SELECT COUNT(*) FROM tasks t WHERE {} AND {} IS NULL".format(base_where, calendar_date),
                parameters,
            ).fetchone()[0])
            undated = connection.execute(
                "SELECT t.*,NULL AS calendar_date FROM tasks t WHERE {} AND {} IS NULL "
                "ORDER BY t.created_at DESC,t.id DESC LIMIT 100".format(base_where, calendar_date),
                parameters,
            ).fetchall()
            rows = self._enrich(connection, list(dated) + list(undated), False)
        return {
            "rows": rows[:len(dated)], "undated": rows[len(dated):],
            "undated_total": undated_total, "start": start_value, "end": end_value,
            "today": today,
        }

    def calendar_reschedule(self, task_id, due_date, due_time, actor_id, actor_role="employee",
                            section="inbox", expected_version=None):
        current = self.get(task_id)
        if actor_role != "admin" and int(actor_id) not in {
                int(current["author_id"]), int(current["assignee_id"])}:
            raise TaskPermissionError("У вас нет права переносить эту задачу.")
        date_value = _date(due_date)
        time_value = _time(due_time) if due_time is not None else current.get("due_time")
        if not date_value and current["status"] == "waiting" and current.get("check_date"):
            raise TaskValidationError("У задачи в ожидании обязательна дата следующей проверки.", "due_date")
        if not date_value:
            if section not in SECTIONS:
                raise TaskValidationError("Неизвестный раздел.", "section")
            time_value = None
        date_field = "check_date" if current["status"] == "waiting" and current.get("check_date") else "due_date"
        old_date = current.get(date_field)
        old_time = current.get("due_time")
        version = int(expected_version or current.get("version") or 1)
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET {}=?,due_time=?,section=?,updated_at=?,updated_by=?,version=version+1 "
                "WHERE id=? AND version=? AND deleted_at IS NULL".format(date_field),
                (date_value, time_value, "inbox" if date_value else section, now, int(actor_id),
                 int(task_id), version),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise TaskConflictError(
                    "Эта запись была изменена другим сотрудником после загрузки календаря."
                )
            self._history(connection, task_id, "date_changed", actor_id, {
                "field": date_field, "from": old_date, "to": date_value,
                "time_from": old_time, "time_to": time_value,
            }, now)
            connection.commit()
        return self.get(task_id)

    def for_entity(self, entity_type, entity_id, limit=20):
        if entity_type not in ENTITY_TYPES:
            raise TaskValidationError("Неизвестный тип связи.", "entity_type")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT t.* FROM tasks t JOIN task_links l ON l.task_id=t.id WHERE t.deleted_at IS NULL "
                "AND l.entity_type=? AND l.entity_id=? "
                "ORDER BY CASE WHEN t.status IN ('new','in_progress','waiting') THEN 0 ELSE 1 END,t.updated_at DESC LIMIT ?",
                (entity_type, str(entity_id), int(limit)),
            ).fetchall()
            return self._enrich(connection, rows, False)
