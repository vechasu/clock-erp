"""Responsibility, assignment history and personal inbox on the tasks database."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.domain_schema_migrations import validate_tasks_database
from app.services.audit_journal import AuditJournal


ENTITY_TYPES = {"order", "customer", "purchase", "repair", "task"}


class CollaborationValidationError(ValueError):
    pass


class CollaborationPermissionError(PermissionError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CollaborationStore:
    def __init__(self, tasks_path, auth_path, catalog_path):
        self.tasks_path = Path(tasks_path).resolve()
        self.auth_path = Path(auth_path).resolve()
        self.catalog_path = Path(catalog_path).resolve()

    def connect(self):
        validate_tasks_database(self.tasks_path)
        connection = sqlite3.connect(str(self.tasks_path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def active_user(self, user_id):
        try:
            target = int(user_id)
        except (TypeError, ValueError):
            raise CollaborationValidationError("Выберите действующего сотрудника.")
        connection = sqlite3.connect(
            "file:{}?mode=ro".format(self.auth_path), uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT id,first_name,last_name,email,role,active FROM users WHERE id=?",
                (target,),
            ).fetchone()
        finally:
            connection.close()
        if row is None or not int(row["active"] or 0):
            raise CollaborationValidationError("Назначить можно только активного сотрудника.")
        return dict(row)

    def prepare(self, connection):
        aliases = {str(row[1]) for row in connection.execute("PRAGMA database_list")}
        if "audit_catalog" not in aliases:
            connection.execute(
                "ATTACH DATABASE ? AS audit_catalog", (str(self.catalog_path),)
            )

    @staticmethod
    def _operation_key(value):
        return str(value or "").strip()[:160] or uuid.uuid4().hex

    def record_assignment(self, connection, entity_type, entity_id, previous_user_id,
                          new_user_id, actor, label, href="", comment="",
                          operation_key="", created_at=None):
        if entity_type not in ENTITY_TYPES:
            raise CollaborationValidationError("Ответственный для этого объекта не поддерживается.")
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            raise CollaborationValidationError("Объект не найден.")
        actor_id = int(actor["id"])
        target = self.active_user(new_user_id) if new_user_id is not None else None
        target_id = int(target["id"]) if target else None
        key = self._operation_key(operation_key)
        now = created_at or utc_now()
        duplicate = connection.execute(
            "SELECT id FROM assignment_history WHERE operation_key=?", (key,)
        ).fetchone()
        if duplicate:
            return False
        connection.execute(
            "INSERT OR REPLACE INTO entity_assignments(entity_type,entity_id,responsible_user_id,updated_at,updated_by) "
            "VALUES(?,?,?,?,?)",
            (entity_type, entity_id, target_id, now, actor_id),
        )
        connection.execute(
            "INSERT INTO assignment_history(entity_type,entity_id,previous_user_id,new_user_id,actor_user_id,comment,operation_key,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (entity_type, entity_id, previous_user_id, target_id, actor_id,
             str(comment or "").strip()[:2000], key, now),
        )
        event_type = (
            "task_assigned" if entity_type == "task" and previous_user_id is None
            else "task_reassigned" if entity_type == "task"
            else "assigned" if previous_user_id is None else "reassigned"
        )
        if target_id is not None and target_id != actor_id:
            connection.execute(
                "INSERT OR IGNORE INTO inbox_events(recipient_user_id,actor_user_id,event_type,entity_type,entity_id,created_at,metadata_json,operation_key) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (target_id, actor_id, event_type, entity_type, entity_id, now,
                 json.dumps({"label": label, "href": href, "comment": str(comment or "").strip()[:2000]},
                            ensure_ascii=False, sort_keys=True), key),
            )
        actor_name = " ".join(
            str(actor.get(field) or "").strip() for field in ("first_name", "last_name")
        ).strip() or str(actor.get("email") or actor_id)
        AuditJournal().record(
            entity_type, entity_id, "updated", str(label or entity_id),
            changes={"responsible_user_id": {"before": previous_user_id, "after": target_id}},
            metadata={"assignment_comment": str(comment or "").strip()[:2000], "operation_key": key},
            actor_id=actor_id, actor_name=actor_name, connection=connection,
        )
        return True

    def assign(self, entity_type, entity_id, new_user_id, actor, label, href="",
               comment="", operation_key=""):
        self.active_user(new_user_id)
        with self.connect() as connection:
            self.prepare(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT responsible_user_id FROM entity_assignments WHERE entity_type=? AND entity_id=?",
                    (entity_type, str(entity_id)),
                ).fetchone()
                previous = int(row[0]) if row and row[0] is not None else None
                created = self.record_assignment(
                    connection, entity_type, entity_id, previous, new_user_id,
                    actor, label, href, comment, operation_key,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_assignment(entity_type, entity_id), created

    def get_assignment(self, entity_type, entity_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM entity_assignments WHERE entity_type=? AND entity_id=?",
                (entity_type, str(entity_id)),
            ).fetchone()
            history = connection.execute(
                "SELECT * FROM assignment_history WHERE entity_type=? AND entity_id=? ORDER BY id DESC LIMIT 50",
                (entity_type, str(entity_id)),
            ).fetchall()
        return {"assignment": dict(row) if row else None,
                "history": [dict(item) for item in history]}

    def assigned_entity_ids(self, entity_type, user_id):
        with self.connect() as connection:
            return {str(row[0]) for row in connection.execute(
                "SELECT entity_id FROM entity_assignments WHERE entity_type=? AND responsible_user_id=?",
                (entity_type, int(user_id)),
            ).fetchall()}

    def list_inbox(self, user_id, unread_only=False, page=1, per_page=30):
        page = max(1, int(page or 1))
        per_page = max(1, min(100, int(per_page or 30)))
        where = "recipient_user_id=?" + (" AND read_at IS NULL" if unread_only else "")
        with self.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM inbox_events WHERE " + where, (int(user_id),)
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM inbox_events WHERE " + where +
                " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (int(user_id), per_page, (page - 1) * per_page),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json"))
            except (TypeError, ValueError):
                item["metadata"] = {}
            result.append(item)
        return {"rows": result, "total": total, "page": page, "per_page": per_page,
                "pages": max(1, (total + per_page - 1) // per_page)}

    def unread_count(self, user_id):
        with self.connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM inbox_events WHERE recipient_user_id=? AND read_at IS NULL",
                (int(user_id),),
            ).fetchone()[0])

    def mark_read(self, event_id, user_id):
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE inbox_events SET read_at=COALESCE(read_at,?) WHERE id=? AND recipient_user_id=?",
                (utc_now(), int(event_id), int(user_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    def mark_all_read(self, user_id):
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE inbox_events SET read_at=? WHERE recipient_user_id=? AND read_at IS NULL",
                (utc_now(), int(user_id)),
            )
            connection.commit()
            return int(cursor.rowcount)
