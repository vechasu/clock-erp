"""Deploy-time-only schema migration for the canonical customer registry."""

import sqlite3
from pathlib import Path


SCHEMA_VERSION = "2026-08-27-customers-registry-v1"

TABLES = (
    "CREATE TABLE registry_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '',
        name_fold TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        first_operation_at TEXT, last_operation_at TEXT,
        operations_count INTEGER NOT NULL DEFAULT 0,
        completed_orders_count INTEGER NOT NULL DEFAULT 0,
        cancelled_orders_count INTEGER NOT NULL DEFAULT 0,
        total_completed_amount REAL NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE customer_external_ids (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
        source TEXT NOT NULL, external_customer_id TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(source, external_customer_id), FOREIGN KEY(customer_id) REFERENCES customers(id)
    )""",
    """CREATE TABLE customer_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('phone','email')), normalized_value TEXT NOT NULL,
        display_value TEXT NOT NULL, source TEXT NOT NULL, masked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(customer_id, kind, normalized_value, source),
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    )""",
    """CREATE TABLE customer_operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
        operation_type TEXT NOT NULL CHECK(operation_type IN ('order','sale','repair')),
        source TEXT NOT NULL, external_id TEXT NOT NULL, external_customer_id TEXT,
        local_ref TEXT, related_order_source TEXT, related_order_id TEXT,
        status TEXT NOT NULL DEFAULT '', occurred_at TEXT NOT NULL DEFAULT '', amount REAL,
        completed INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1, payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(operation_type, source, external_id),
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    )""",
    """CREATE TABLE customer_identity_conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, operation_type TEXT NOT NULL,
        source TEXT NOT NULL, external_id TEXT NOT NULL, reason TEXT NOT NULL,
        phone_hint TEXT NOT NULL DEFAULT '', email_hint TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, UNIQUE(operation_type, source, external_id, reason)
    )""",
)

INDEXES = (
    "CREATE INDEX idx_registry_customers_last ON customers(last_operation_at, id)",
    "CREATE INDEX idx_registry_customers_name ON customers(name_fold)",
    "CREATE INDEX idx_registry_contacts_lookup ON customer_contacts(kind, normalized_value, masked)",
    "CREATE INDEX idx_registry_contacts_customer ON customer_contacts(customer_id, kind)",
    "CREATE INDEX idx_registry_operations_customer ON customer_operations(customer_id, occurred_at)",
    "CREATE INDEX idx_registry_operations_related ON customer_operations(related_order_source, related_order_id)",
    "CREATE INDEX idx_registry_operations_search ON customer_operations(source, external_id)",
)


def migrate_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        existing = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if existing - {"sqlite_sequence"}:
            version = connection.execute(
                "SELECT value FROM registry_meta WHERE key='schema_version'"
            ).fetchone()
            if not version or version[0] != SCHEMA_VERSION:
                raise RuntimeError("unsupported customer registry schema")
        else:
            for statement in TABLES + INDEXES:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO registry_meta(key,value) VALUES('schema_version',?)",
                (SCHEMA_VERSION,),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
