"""Versioned SMS database schema compatible with SQLite 3.7.17."""

from __future__ import print_function

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "sms-v1"
MESSAGE_STATUSES = (
    "created", "sending", "accepted", "unknown", "queued",
    "smsc_submit", "delivered", "failed", "cancelled",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sms_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sms_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    message_text TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_by_id TEXT,
    created_by_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_by_id TEXT,
    updated_by_name TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sms_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_message_id TEXT NOT NULL UNIQUE,
    smsc_id TEXT UNIQUE,
    provider TEXT NOT NULL DEFAULT 'smsbliss' CHECK (provider = 'smsbliss'),
    customer_id INTEGER,
    customer_name TEXT,
    order_id TEXT,
    order_number TEXT,
    repair_id TEXT,
    repair_number TEXT,
    source_phone TEXT NOT NULL,
    normalized_phone TEXT NOT NULL,
    message_text TEXT NOT NULL,
    sender TEXT,
    template_id INTEGER REFERENCES sms_templates(id) ON DELETE RESTRICT,
    segments INTEGER,
    cost TEXT,
    currency TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'created','sending','accepted','unknown','queued','smsc_submit',
        'delivered','failed','cancelled'
    )),
    provider_status TEXT,
    error_description TEXT,
    created_at TEXT NOT NULL,
    scheduled_at TEXT,
    sent_at TEXT,
    delivered_at TEXT,
    created_by_id TEXT NOT NULL,
    created_by_name TEXT NOT NULL,
    sent_by_id TEXT,
    sent_by_name TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sms_messages_created
    ON sms_messages(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_sms_messages_status
    ON sms_messages(status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_sms_messages_actor
    ON sms_messages(created_by_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_sms_messages_customer
    ON sms_messages(customer_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_sms_messages_order
    ON sms_messages(order_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_sms_messages_repair
    ON sms_messages(repair_id, created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS sms_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES sms_messages(id) ON DELETE CASCADE,
    previous_status TEXT,
    status TEXT NOT NULL,
    provider_status TEXT,
    description TEXT,
    changed_at TEXT NOT NULL,
    changed_by_id TEXT,
    changed_by_name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sms_status_history_message
    ON sms_status_history(message_id, changed_at, id);
CREATE TABLE IF NOT EXISTS sms_integration_cache (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1 CHECK (success IN (0,1))
);
"""

STARTER_TEMPLATES = (
    ("Заказ принят", "{client_name}, ваш заказ №{order_number} принят."),
    ("Заказ готов к выдаче", "{client_name}, заказ №{order_number} готов к выдаче."),
    ("Заказ передан в доставку", "{client_name}, заказ №{order_number} передан в доставку."),
    ("Ремонт готов", "{client_name}, ремонт №{repair_number} готов."),
    ("Свяжитесь с магазином", "{client_name}, пожалуйста, свяжитесь с магазином Tictactoy."),
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def migrate_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.executescript(SCHEMA_SQL)
        now = utc_now()
        for name, body in STARTER_TEMPLATES:
            connection.execute(
                "INSERT OR IGNORE INTO sms_templates "
                "(name,message_text,active,created_by_name,created_at,updated_by_name,updated_at) "
                "VALUES(?,?,1,'Система',?,'Система',?)",
                (name, body, now, now),
            )
        connection.execute(
            "INSERT OR REPLACE INTO sms_meta(key,value) VALUES('schema_version',?)",
            (SCHEMA_VERSION,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    verify_database(path)


def verify_database(path):
    path = Path(path).resolve()
    if not path.exists():
        raise sqlite3.OperationalError("SMS database migration required")
    connection = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
    try:
        version = connection.execute(
            "SELECT value FROM sms_meta WHERE key='schema_version'"
        ).fetchone()
        if not version or version[0] != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("SMS database schema version differs")
        expected = {"sms_meta", "sms_templates", "sms_messages", "sms_status_history", "sms_integration_cache"}
        actual = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if actual != expected:
            raise sqlite3.DatabaseError("SMS database schema differs")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("SMS database quick_check failed")
    finally:
        connection.close()
