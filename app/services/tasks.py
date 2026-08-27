"""Persistent internal ERP tasks without runtime schema mutation."""

import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain_schema_migrations import validate_tasks_database


MOSCOW_TIMEZONE = timezone(timedelta(hours=3), "Europe/Moscow")
SECTIONS = {"inbox", "anytime", "someday"}
PRIORITIES = {"urgent", "important", "other"}
ENTITY_TYPES = {"customer", "order", "sale", "repair", "product"}
VIEWS = {"inbox", "today", "plans", "anytime", "someday", "logbook"}


class TaskValidationError(ValueError):
    def __init__(self, message, field=""):
        super().__init__(message)
        self.field = field


class TaskNotFoundError(LookupError):
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


def _date(value):
    value = _text(value, 10)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise TaskValidationError("Укажите корректную дату.", "due_date")


def _time(value):
    value = _text(value, 5)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except ValueError:
        raise TaskValidationError("Укажите корректное время.", "due_time")


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
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @staticmethod
    def normalize(payload, partial=False):
        result = {}
        if not partial or "title" in payload:
            title = _text(payload.get("title"), 240)
            if not title:
                raise TaskValidationError("Название задачи обязательно.", "title")
            result["title"] = title
        if not partial or "description" in payload:
            result["description"] = _text(payload.get("description"), 10000)
        if not partial or "section" in payload:
            section = _text(payload.get("section") or "inbox", 20)
            if section not in SECTIONS:
                raise TaskValidationError("Неизвестный раздел.", "section")
            result["section"] = section
        if not partial or "priority" in payload:
            priority = _text(payload.get("priority") or "other", 20)
            if priority not in PRIORITIES:
                raise TaskValidationError("Неизвестный приоритет.", "priority")
            result["priority"] = priority
        if not partial or "due_date" in payload:
            result["due_date"] = _date(payload.get("due_date"))
        if not partial or "due_time" in payload:
            result["due_time"] = _time(payload.get("due_time"))
        if result.get("due_time") and not result.get("due_date") and not partial:
            raise TaskValidationError("Для времени укажите дату.", "due_date")
        if not partial or "assignee_id" in payload:
            try:
                assignee = int(payload.get("assignee_id"))
            except (TypeError, ValueError):
                raise TaskValidationError("Выберите ответственного.", "assignee_id")
            if assignee < 1:
                raise TaskValidationError("Выберите ответственного.", "assignee_id")
            result["assignee_id"] = assignee
        entity_type_present = "entity_type" in payload
        entity_id_present = "entity_id" in payload
        if not partial or entity_type_present or entity_id_present:
            entity_type = _text(payload.get("entity_type"), 20) or None
            entity_id = _text(payload.get("entity_id"), 120) or None
            if bool(entity_type) != bool(entity_id):
                raise TaskValidationError("Выберите связанную сущность из поиска.", "entity_id")
            if entity_type and entity_type not in ENTITY_TYPES:
                raise TaskValidationError("Неизвестный тип связи.", "entity_type")
            result["entity_type"] = entity_type
            result["entity_id"] = entity_id
        return result

    @staticmethod
    def _serialize(row):
        task = dict(row)
        task["completed"] = task["status"] == "completed"
        return task

    def create(self, payload, actor_id, user_exists, entity_resolver):
        values = self.normalize(payload)
        if not user_exists(values["assignee_id"]):
            raise TaskValidationError("Ответственный сотрудник не найден.", "assignee_id")
        entity = None
        if values["entity_type"]:
            entity = entity_resolver(values["entity_type"], values["entity_id"])
            if not entity:
                raise TaskValidationError("Связанная сущность не найдена.", "entity_id")
        now = utc_now()
        idempotency_key = _text(payload.get("idempotency_key"), 120) or None
        with self.connect() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO tasks(title,description,section,status,priority,due_date,due_time,"
                    "author_id,assignee_id,entity_type,entity_id,entity_label,entity_href,"
                    "created_at,updated_at,idempotency_key) VALUES(?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,?)",
                    (values["title"], values["description"], values["section"], values["priority"],
                     values["due_date"], values["due_time"], int(actor_id), values["assignee_id"],
                     values["entity_type"], values["entity_id"],
                     entity.get("label") if entity else None, entity.get("href") if entity else None,
                     now, now, idempotency_key),
                )
                connection.commit()
                task_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                if not idempotency_key:
                    raise
                row = connection.execute(
                    "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                return self._serialize(row), False
        return self.get(task_id), True

    def get(self, task_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
        if row is None:
            raise TaskNotFoundError("Задача не найдена.")
        return self._serialize(row)

    def update(self, task_id, payload, actor_id, user_exists, entity_resolver):
        current = self.get(task_id)
        values = self.normalize(payload, partial=True)
        assignee = values.get("assignee_id", current["assignee_id"])
        if not user_exists(assignee):
            raise TaskValidationError("Ответственный сотрудник не найден.", "assignee_id")
        entity_type = values.get("entity_type", current["entity_type"])
        entity_id = values.get("entity_id", current["entity_id"])
        entity = None
        if entity_type:
            entity = entity_resolver(entity_type, entity_id)
            if not entity:
                raise TaskValidationError("Связанная сущность не найдена.", "entity_id")
        merged = dict(current)
        merged.update(values)
        if merged.get("due_time") and not merged.get("due_date"):
            raise TaskValidationError("Для времени укажите дату.", "due_date")
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET title=?,description=?,section=?,priority=?,due_date=?,due_time=?,"
                "assignee_id=?,entity_type=?,entity_id=?,entity_label=?,entity_href=?,updated_at=?,updated_by=? WHERE id=?",
                (merged["title"], merged["description"], merged["section"], merged["priority"],
                 merged["due_date"], merged["due_time"], assignee, entity_type, entity_id,
                 entity.get("label") if entity else None, entity.get("href") if entity else None,
                 utc_now(), int(actor_id), int(task_id)),
            )
            connection.commit()
        return self.get(task_id)

    def set_completed(self, task_id, completed, actor_id):
        self.get(task_id)
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET status=?,completed_at=?,completed_by=?,updated_at=?,updated_by=? WHERE id=?",
                ("completed" if completed else "active", now if completed else None,
                 int(actor_id) if completed else None, now, int(actor_id), int(task_id)),
            )
            connection.commit()
        return self.get(task_id)

    def move(self, task_id, section, actor_id):
        if section not in SECTIONS:
            raise TaskValidationError("Неизвестный раздел.", "section")
        self.get(task_id)
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET section=?,due_date=NULL,due_time=NULL,updated_at=?,updated_by=? WHERE id=?",
                (section, utc_now(), int(actor_id), int(task_id)),
            )
            connection.commit()
        return self.get(task_id)

    def counts(self, today=None):
        today = today or moscow_today()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT SUM(CASE WHEN due_date = ? THEN 1 ELSE 0 END) today_count, "
                "SUM(CASE WHEN due_date < ? THEN 1 ELSE 0 END) overdue_count "
                "FROM tasks WHERE status='active' AND due_date IS NOT NULL", (today, today)
            ).fetchone()
        return {"today": int(row[0] or 0), "overdue": int(row[1] or 0),
                "active": int(row[0] or 0) + int(row[1] or 0)}

    def list(self, view="today", query="", assignee_id=None, priority="",
             entity_type="", page=1, per_page=50, today=None):
        if view not in VIEWS:
            raise TaskValidationError("Неизвестное представление.", "view")
        today = today or moscow_today()
        clauses, parameters = [], []
        if view == "logbook":
            clauses.append("status='completed'")
        else:
            clauses.append("status='active'")
            if view == "today":
                clauses.append("due_date IS NOT NULL AND due_date <= ?")
                parameters.append(today)
            elif view == "plans":
                clauses.append("due_date > ?")
                parameters.append(today)
            else:
                clauses.append("due_date IS NULL AND section = ?")
                parameters.append(view)
        query = _text(query, 200)
        if query:
            clauses.append("(erp_casefold(title) LIKE ? OR erp_casefold(description) LIKE ? OR erp_casefold(entity_label) LIKE ?)")
            folded = "%{}%".format(query.casefold())
            parameters.extend((folded, folded, folded))
        if assignee_id:
            clauses.append("assignee_id = ?")
            parameters.append(int(assignee_id))
        if priority:
            if priority not in PRIORITIES:
                raise TaskValidationError("Неизвестный приоритет.", "priority")
            clauses.append("priority = ?")
            parameters.append(priority)
        if entity_type:
            if entity_type not in ENTITY_TYPES:
                raise TaskValidationError("Неизвестный тип связи.", "entity_type")
            clauses.append("entity_type = ?")
            parameters.append(entity_type)
        try:
            page = max(1, int(page))
            per_page = max(1, min(int(per_page), 100))
        except (TypeError, ValueError):
            page, per_page = 1, 50
        where = " WHERE " + " AND ".join(clauses)
        if view == "today":
            order = " ORDER BY CASE WHEN due_date < ? THEN 0 WHEN priority='urgent' THEN 1 WHEN priority='important' THEN 2 ELSE 3 END,due_time IS NULL,due_time,created_at,id"
            order_parameters = [today]
        elif view == "plans":
            order, order_parameters = " ORDER BY due_date,due_time IS NULL,due_time,created_at,id", []
        elif view == "logbook":
            order, order_parameters = " ORDER BY completed_at DESC,id DESC", []
        else:
            order, order_parameters = " ORDER BY created_at DESC,id DESC", []
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM tasks" + where, parameters).fetchone()[0])
            pages = max(1, int(math.ceil(float(total) / per_page)))
            page = min(page, pages)
            rows = connection.execute(
                "SELECT * FROM tasks" + where + order + " LIMIT ? OFFSET ?",
                parameters + order_parameters + [per_page, (page - 1) * per_page],
            ).fetchall()
        return {"rows": [self._serialize(row) for row in rows], "total": total,
                "page": page, "per_page": per_page, "pages": pages, "today": today}
