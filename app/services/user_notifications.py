"""Persistent per-user notifications for new ERP orders and task assignments."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


NOTIFICATION_TYPES = {"order", "task"}
DEFAULT_PREFERENCES = {
    "order_sound": True,
    "task_sound": True,
    "browser_notifications": False,
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value, maximum=1000):
    return str(value or "").strip()[:maximum]


class UserNotificationStore:
    def __init__(self, path=None):
        configured = path or os.getenv("ERP_NOTIFICATIONS_DATABASE", "").strip()
        self.path = Path(configured) if configured else Path("instance/notifications.db")
        self._initialized = False

    def connect(self):
        self.initialize()
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self):
        if self._initialized:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS notification_entities (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    PRIMARY KEY(entity_type, entity_id)
                );
                CREATE TABLE IF NOT EXISTS user_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL CHECK(type IN ('order','task')),
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    target_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    read_at TEXT,
                    delivered_at TEXT,
                    dedupe_key TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_user_notifications_feed
                    ON user_notifications(user_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_user_notifications_unread
                    ON user_notifications(user_id, read_at, id DESC);
                CREATE TABLE IF NOT EXISTS user_notification_preferences (
                    user_id INTEGER PRIMARY KEY,
                    order_sound INTEGER NOT NULL DEFAULT 1 CHECK(order_sound IN (0,1)),
                    task_sound INTEGER NOT NULL DEFAULT 1 CHECK(task_sound IN (0,1)),
                    browser_notifications INTEGER NOT NULL DEFAULT 0 CHECK(browser_notifications IN (0,1)),
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.commit()
        finally:
            connection.close()
        self._initialized = True
        return self

    @staticmethod
    def _insert(connection, user_id, kind, entity_type, entity_id, title,
                message, target_url, metadata, created_at=None):
        if kind not in NOTIFICATION_TYPES:
            raise ValueError("Unsupported notification type")
        dedupe_key = "new_{}:{}:{}".format(kind, entity_id, int(user_id))
        cursor = connection.execute(
            "INSERT OR IGNORE INTO user_notifications "
            "(user_id,type,entity_type,entity_id,title,message,metadata_json,target_url,created_at,dedupe_key) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                int(user_id), kind, _text(entity_type, 40), _text(entity_id, 160),
                _text(title, 240), _text(message, 1000),
                json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                _text(target_url, 1000), created_at or utc_now(), dedupe_key,
            ),
        )
        return cursor.rowcount == 1

    def publish_saved_orders(self, source, previous_ids, orders, recipient_ids):
        """Record only orders that became durable in this completed sync.

        The first empty snapshot is a migration baseline, so historical rows never
        produce a notification flood. Entity tombstones also prevent a removed and
        later reappearing order from becoming "new" again.
        """
        source = _text(source, 40)
        previous_ids = {_text(value, 160) for value in previous_ids if _text(value, 160)}
        prepared = []
        for order in orders:
            entity_id = _text(order.get("id") or order.get("ID"), 160)
            if entity_id:
                prepared.append((entity_id, order))
        recipients = sorted({int(value) for value in recipient_ids if int(value) > 0})
        now = utc_now()
        created = 0
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            known_count = int(connection.execute(
                "SELECT COUNT(*) FROM notification_entities WHERE entity_type=?",
                ("order:" + source,),
            ).fetchone()[0])
            if known_count == 0:
                baseline = previous_ids or ({item[0] for item in prepared} if not previous_ids else set())
                for entity_id in baseline:
                    connection.execute(
                        "INSERT OR IGNORE INTO notification_entities(entity_type,entity_id,first_seen_at) VALUES(?,?,?)",
                        ("order:" + source, entity_id, now),
                    )
                if not previous_ids:
                    connection.commit()
                    return 0
            for entity_id, order in prepared:
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO notification_entities(entity_type,entity_id,first_seen_at) VALUES(?,?,?)",
                    ("order:" + source, entity_id, now),
                ).rowcount == 1
                if not inserted:
                    continue
                number = _text(order.get("number") or order.get("wb_order_id") or entity_id, 160)
                source_name = _text(order.get("source_name"), 240) or (
                    "Wildberries" if source == "wildberries" else "Сайт / Ziro (Bitrix)"
                )
                target = (
                    "/order/wildberries/{}".format(_text(order.get("wb_order_id") or entity_id.removeprefix("wb:"), 160))
                    if source == "wildberries" else "/order/{}".format(entity_id)
                )
                for user_id in recipients:
                    created += int(self._insert(
                        connection, user_id, "order", "order", entity_id,
                        "Новый заказ #{}".format(number), source_name, target,
                        {"source": source_name}, now,
                    ))
            connection.commit()
        return created

    def publish_task(self, task, user_id, author_name=""):
        task_id = int(task["id"])
        due = ""
        if task.get("due_date"):
            due = str(task["due_date"])
            if task.get("due_time"):
                due += " " + str(task["due_time"])
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO notification_entities(entity_type,entity_id,first_seen_at) VALUES('task',?,?)",
                (str(task_id), utc_now()),
            )
            created = self._insert(
                connection, user_id, "task", "task", str(task_id), "Новая задача",
                _text(task.get("title"), 1000), "/app/tasks?task={}".format(task_id),
                {"author": _text(author_name, 240), "due": due},
            )
            connection.commit()
        return created

    def feed(self, user_id, limit=100, claim_delivery=True):
        limit = max(1, min(int(limit), 100))
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM user_notifications WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (int(user_id), limit),
            ).fetchall()
            fresh_ids = [int(row["id"]) for row in rows if row["delivered_at"] is None]
            if claim_delivery and fresh_ids:
                marks = ",".join("?" for _ in fresh_ids)
                connection.execute(
                    "UPDATE user_notifications SET delivered_at=? WHERE user_id=? AND id IN ({}) AND delivered_at IS NULL".format(marks),
                    [now, int(user_id)] + fresh_ids,
                )
            unread = int(connection.execute(
                "SELECT COUNT(*) FROM user_notifications WHERE user_id=? AND read_at IS NULL",
                (int(user_id),),
            ).fetchone()[0])
            connection.commit()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except (TypeError, ValueError):
                item["metadata"] = {}
                item.pop("metadata_json", None)
            item["fresh"] = int(item["id"]) in fresh_ids
            items.append(item)
        return {"items": items, "unread": unread, "preferences": self.preferences(user_id)}

    def mark_read(self, user_id, notification_id):
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE user_notifications SET read_at=COALESCE(read_at,?) WHERE id=? AND user_id=?",
                (utc_now(), int(notification_id), int(user_id)),
            )
            connection.commit()
        return cursor.rowcount == 1

    def mark_all_read(self, user_id):
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE user_notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",
                (utc_now(), int(user_id)),
            )
            connection.commit()
        return cursor.rowcount

    def preferences(self, user_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT order_sound,task_sound,browser_notifications FROM user_notification_preferences WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
        if row is None:
            return dict(DEFAULT_PREFERENCES)
        return {key: bool(row[key]) for key in DEFAULT_PREFERENCES}

    def save_preferences(self, user_id, values):
        current = self.preferences(user_id)
        for key in DEFAULT_PREFERENCES:
            if key in values:
                current[key] = bool(values[key])
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO user_notification_preferences "
                "(user_id,order_sound,task_sound,browser_notifications,updated_at) VALUES(?,?,?,?,?)",
                (int(user_id), int(current["order_sound"]), int(current["task_sound"]),
                 int(current["browser_notifications"]), utc_now()),
            )
            connection.commit()
        return current
