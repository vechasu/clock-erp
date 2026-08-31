"""Deploy-time-only schema migration for the canonical customer registry."""

import sqlite3
from pathlib import Path


SCHEMA_VERSION = "2026-08-31-customers-order-search-v3"
CRM_V2_VERSION = "2026-08-27-customers-crm-v2"
LEGACY_VERSION = "2026-08-27-customers-registry-v1"

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

V2_COLUMNS = (
    ("orders_count", "INTEGER NOT NULL DEFAULT 0"),
    ("sales_count", "INTEGER NOT NULL DEFAULT 0"),
    ("repairs_count", "INTEGER NOT NULL DEFAULT 0"),
    ("sales_amount", "REAL NOT NULL DEFAULT 0"),
    ("last_sale_at", "TEXT"),
    ("merged_into_id", "INTEGER"),
)

V2_TABLES = (
    """CREATE TABLE customer_duplicate_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, left_customer_id INTEGER NOT NULL,
        right_customer_id INTEGER NOT NULL, score INTEGER NOT NULL DEFAULT 0,
        reasons TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(left_customer_id,right_customer_id),
        FOREIGN KEY(left_customer_id) REFERENCES customers(id),
        FOREIGN KEY(right_customer_id) REFERENCES customers(id)
    )""",
    """CREATE TABLE customer_merge_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
        target_customer_id INTEGER NOT NULL, source_customer_id INTEGER NOT NULL,
        actor_id TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL, idempotency_key TEXT UNIQUE
    )""",
    """CREATE TABLE customer_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
        body TEXT NOT NULL, actor_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    )""",
    """CREATE TABLE customer_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
        event_type TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
        label TEXT NOT NULL, occurred_at TEXT NOT NULL, href TEXT NOT NULL DEFAULT '',
        actor_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        UNIQUE(event_type,source,external_id),
        FOREIGN KEY(customer_id) REFERENCES customers(id)
    )""",
)

V2_INDEXES = (
    "CREATE INDEX idx_customers_merge ON customers(merged_into_id,id)",
    "CREATE INDEX idx_customers_sales ON customers(last_sale_at,sales_count,sales_amount,id)",
    "CREATE INDEX idx_duplicate_status ON customer_duplicate_candidates(status,score,id)",
    "CREATE INDEX idx_customer_events_feed ON customer_events(customer_id,occurred_at,id)",
    "CREATE INDEX idx_customer_notes_customer ON customer_notes(customer_id,created_at,id)",
)

V3_INDEXES = (
    "CREATE INDEX idx_registry_order_number ON customer_operations(external_id,operation_type,customer_id)",
    "CREATE INDEX idx_registry_operations_customer_type ON customer_operations(customer_id,operation_type,active,occurred_at,id)",
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
            if not version or version[0] not in (LEGACY_VERSION, CRM_V2_VERSION, SCHEMA_VERSION):
                raise RuntimeError("unsupported customer registry schema")
        else:
            for statement in TABLES + INDEXES:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO registry_meta(key,value) VALUES('schema_version',?)",
                (LEGACY_VERSION,),
            )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(customers)")}
        for name, definition in V2_COLUMNS:
            if name not in columns:
                connection.execute("ALTER TABLE customers ADD COLUMN {} {}".format(name, definition))
        existing = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        for statement in V2_TABLES:
            table_name = statement.split()[2]
            if table_name not in existing:
                connection.execute(statement)
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        for statement in V2_INDEXES:
            index_name = statement.split()[2]
            if index_name not in indexes:
                connection.execute(statement)
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        for statement in V3_INDEXES:
            index_name = statement.split()[2]
            if index_name not in indexes:
                connection.execute(statement)
        connection.execute(
            "INSERT OR REPLACE INTO registry_meta(key,value) VALUES('schema_version',?)",
            (SCHEMA_VERSION,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
