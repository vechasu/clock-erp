"""Versioned auth and orders schema migrations for production SQLite 3.7.17.

This module is deploy-time only. Runtime stores call the read-only validators
and never attempt to create, alter, or repair a database schema.
"""

from __future__ import print_function

import fcntl
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


LEDGER_TABLE = "erp_migration_ledger"
LEDGER_SQL = """
CREATE TABLE erp_migration_ledger (
    migration_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('applying', 'applied', 'failed')),
    applied_at TEXT,
    app_commit TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
)
"""

AUTH_MIGRATION_ID = "2026-08-26-auth-baseline-v1"
AUTH_PREFERENCES_MIGRATION_ID = "2026-08-28-user-navigation-preferences-v1"
AUTH_NOTIFICATIONS_MIGRATION_ID = "2026-09-01-user-notifications-v1"
ORDERS_MIGRATION_ID = "2026-08-26-orders-customers-baseline-v1"
TASKS_MIGRATION_ID = "2026-08-27-internal-tasks-v1"
TASKS_V2_MIGRATION_ID = "2026-08-27-tasks-center-v2"
TASKS_V3_MIGRATION_ID = "2026-08-28-collaboration-responsibility-v1"
TASKS_V4_MIGRATION_ID = "2026-08-28-task-soft-delete-v1"


class DomainMigrationError(RuntimeError):
    pass


class MigrationRequiredError(DomainMigrationError):
    pass


def _digest(parts):
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


AUTH_TABLE_STATEMENTS = (
    """CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        email TEXT NOT NULL,
        email_normalized TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('employee', 'admin')),
        active INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        email_verified_at INTEGER,
        updated_at INTEGER,
        session_version INTEGER NOT NULL DEFAULT 1,
        last_login_at INTEGER
    )""",
    """CREATE TABLE invitations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE,
        email TEXT,
        email_normalized TEXT,
        role TEXT NOT NULL CHECK (role IN ('employee', 'admin')),
        expires_at INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'active'
            CHECK (state IN ('active', 'used', 'revoked')),
        created_by INTEGER,
        created_at INTEGER NOT NULL,
        used_at INTEGER,
        used_by INTEGER,
        FOREIGN KEY (created_by) REFERENCES users(id),
        FOREIGN KEY (used_by) REFERENCES users(id)
    )""",
    """CREATE TABLE auth_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bucket TEXT NOT NULL,
        attempted_at INTEGER NOT NULL
    )""",
    """CREATE TABLE auth_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        token_type TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        used_at INTEGER,
        created_at INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    """CREATE TABLE auth_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_hash TEXT NOT NULL UNIQUE,
        user_id INTEGER,
        data TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
)

AUTH_INDEX_STATEMENTS = (
    "CREATE INDEX invitations_state_expires ON invitations(state, expires_at)",
    "CREATE INDEX auth_attempts_bucket_time ON auth_attempts(bucket, attempted_at)",
    "CREATE INDEX auth_tokens_user_type ON auth_tokens(user_id, token_type, created_at)",
    "CREATE INDEX auth_sessions_expiry ON auth_sessions(expires_at)",
    "CREATE INDEX auth_sessions_user ON auth_sessions(user_id)",
)

AUTH_PREFERENCES_TABLE_STATEMENTS = (
    """CREATE TABLE user_navigation_preferences (
        user_id INTEGER PRIMARY KEY,
        ordered_keys TEXT NOT NULL,
        hidden_keys TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )""",
)

AUTH_NOTIFICATIONS_TABLE_STATEMENTS = (
    """CREATE TABLE notification_entities (
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        PRIMARY KEY(entity_type, entity_id)
    )""",
    """CREATE TABLE user_notifications (
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
        dedupe_key TEXT NOT NULL UNIQUE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE user_notification_preferences (
        user_id INTEGER PRIMARY KEY,
        order_sound INTEGER NOT NULL DEFAULT 1 CHECK(order_sound IN (0,1)),
        task_sound INTEGER NOT NULL DEFAULT 1 CHECK(task_sound IN (0,1)),
        browser_notifications INTEGER NOT NULL DEFAULT 0 CHECK(browser_notifications IN (0,1)),
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )""",
)

AUTH_NOTIFICATIONS_INDEX_STATEMENTS = (
    "CREATE INDEX idx_user_notifications_feed ON user_notifications(user_id, id)",
    "CREATE INDEX idx_user_notifications_unread ON user_notifications(user_id, read_at, id)",
)

AUTH_LEGACY_USER_COLUMNS = (
    ("id", "INTEGER", 0, None, 1),
    ("first_name", "TEXT", 1, None, 0),
    ("last_name", "TEXT", 1, None, 0),
    ("email", "TEXT", 1, None, 0),
    ("email_normalized", "TEXT", 1, None, 0),
    ("password_hash", "TEXT", 1, None, 0),
    ("role", "TEXT", 1, None, 0),
    ("active", "INTEGER", 1, "1", 0),
    ("created_at", "INTEGER", 1, None, 0),
)

AUTH_UPGRADE_STATEMENTS = (
    "ALTER TABLE users ADD COLUMN email_verified_at INTEGER",
    "ALTER TABLE users ADD COLUMN updated_at INTEGER",
    "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE users ADD COLUMN last_login_at INTEGER",
    "UPDATE users SET email_verified_at = created_at",
    "UPDATE users SET updated_at = COALESCE(updated_at, created_at)",
)

ORDERS_TABLE_STATEMENTS = (
    """CREATE TABLE orders_snapshot (
        order_id TEXT PRIMARY KEY,
        source_position INTEGER NOT NULL,
        number_fold TEXT NOT NULL,
        customer_fold TEXT NOT NULL,
        phone_digits TEXT NOT NULL,
        amount_search TEXT NOT NULL,
        date_search TEXT NOT NULL,
        created_sort TEXT NOT NULL,
        status TEXT NOT NULL,
        item_units REAL,
        payload_json TEXT NOT NULL,
        loaded_at REAL NOT NULL,
        detail_loaded INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'tictactoy',
        external_order_id TEXT,
        extra_fold TEXT NOT NULL DEFAULT '',
        customer_id INTEGER
    )""",
    """CREATE TABLE customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL DEFAULT '',
        name_fold TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        normalized_phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        normalized_email TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        region TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE orders_snapshot_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
)

ORDERS_INDEX_STATEMENTS = (
    "CREATE INDEX idx_customers_normalized_phone ON customers(normalized_phone)",
    "CREATE INDEX idx_customers_normalized_email ON customers(normalized_email)",
    "CREATE INDEX idx_customers_name ON customers(name_fold)",
    "CREATE INDEX idx_orders_snapshot_ordering ON orders_snapshot(source_position, order_id)",
    "CREATE INDEX idx_orders_snapshot_status_ordering ON orders_snapshot(status, source_position, order_id)",
    "CREATE INDEX idx_orders_snapshot_created ON orders_snapshot(created_sort, source_position, order_id)",
    "CREATE UNIQUE INDEX idx_orders_snapshot_source_external ON orders_snapshot(source, external_order_id)",
    "CREATE INDEX idx_orders_snapshot_source_ordering ON orders_snapshot(source, source_position, order_id)",
    "CREATE INDEX idx_orders_snapshot_customer_created ON orders_snapshot(customer_id, created_sort)",
)

TASKS_TABLE_STATEMENTS = (
    """CREATE TABLE tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL CHECK (length(trim(title)) > 0),
        description TEXT NOT NULL DEFAULT '',
        section TEXT NOT NULL DEFAULT 'inbox'
            CHECK (section IN ('inbox', 'anytime', 'someday')),
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'completed')),
        priority TEXT NOT NULL DEFAULT 'other'
            CHECK (priority IN ('urgent', 'important', 'other')),
        due_date TEXT,
        due_time TEXT,
        author_id INTEGER NOT NULL,
        assignee_id INTEGER NOT NULL,
        entity_type TEXT CHECK (entity_type IS NULL OR entity_type IN
            ('customer', 'order', 'sale', 'repair', 'product')),
        entity_id TEXT,
        entity_label TEXT,
        entity_href TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by INTEGER,
        completed_at TEXT,
        completed_by INTEGER,
        idempotency_key TEXT UNIQUE,
        CHECK ((entity_type IS NULL AND entity_id IS NULL) OR
               (entity_type IS NOT NULL AND entity_id IS NOT NULL)),
        CHECK (due_time IS NULL OR due_date IS NOT NULL)
    )""",
)

TASKS_INDEX_STATEMENTS = (
    "CREATE INDEX idx_tasks_status_due ON tasks(status, due_date, due_time, id)",
    "CREATE INDEX idx_tasks_assignee_status_due ON tasks(assignee_id, status, due_date, id)",
    "CREATE INDEX idx_tasks_section_status ON tasks(section, status, id)",
    "CREATE INDEX idx_tasks_entity ON tasks(entity_type, entity_id, status)",
    "CREATE INDEX idx_tasks_completed ON tasks(status, completed_at, id)",
)

TASKS_V2_TABLE_STATEMENTS = (
    """CREATE TABLE tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL CHECK (length(trim(title)) > 0),
        description TEXT NOT NULL DEFAULT '',
        section TEXT NOT NULL DEFAULT 'inbox'
            CHECK (section IN ('inbox', 'anytime', 'someday')),
        status TEXT NOT NULL DEFAULT 'new'
            CHECK (status IN ('new','in_progress','waiting','completed','cancelled')),
        priority TEXT NOT NULL DEFAULT 'other'
            CHECK (priority IN ('urgent', 'important', 'other')),
        due_date TEXT,
        due_time TEXT,
        reminder_at TEXT,
        author_id INTEGER NOT NULL,
        assignee_id INTEGER NOT NULL,
        source_comment TEXT NOT NULL DEFAULT '',
        contact_name TEXT NOT NULL DEFAULT '',
        contact_phone TEXT NOT NULL DEFAULT '',
        contact_email TEXT NOT NULL DEFAULT '',
        contact_channel TEXT NOT NULL DEFAULT '',
        waiting_for TEXT NOT NULL DEFAULT '',
        check_date TEXT,
        waiting_comment TEXT NOT NULL DEFAULT '',
        repeat_type TEXT NOT NULL DEFAULT 'none'
            CHECK (repeat_type IN ('none','daily','weekdays','weekly','monthly','custom')),
        repeat_interval INTEGER NOT NULL DEFAULT 1 CHECK (repeat_interval > 0),
        series_id TEXT,
        parent_task_id INTEGER,
        completion_result TEXT NOT NULL DEFAULT '',
        previous_status TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by INTEGER,
        completed_at TEXT,
        completed_by INTEGER,
        cancelled_at TEXT,
        cancelled_by INTEGER,
        idempotency_key TEXT UNIQUE,
        next_occurrence_key TEXT UNIQUE,
        CHECK (due_time IS NULL OR due_date IS NOT NULL)
    )""",
    """CREATE TABLE task_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        entity_type TEXT NOT NULL CHECK (entity_type IN
            ('customer','order','sale','repair','product','purchase')),
        entity_id TEXT NOT NULL,
        entity_label TEXT NOT NULL DEFAULT '',
        entity_href TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        UNIQUE(task_id, entity_type, entity_id)
    )""",
    """CREATE TABLE task_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        actor_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE task_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        notification_type TEXT NOT NULL,
        notification_key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        seen_at TEXT,
        UNIQUE(task_id, user_id, notification_type, notification_key)
    )""",
)

TASKS_V2_INDEX_STATEMENTS = (
    "CREATE INDEX idx_tasks_status_due ON tasks(status, due_date, due_time, id)",
    "CREATE INDEX idx_tasks_assignee_status_due ON tasks(assignee_id, status, due_date, id)",
    "CREATE INDEX idx_tasks_section_status ON tasks(section, status, id)",
    "CREATE INDEX idx_tasks_check_date ON tasks(status, check_date, id)",
    "CREATE INDEX idx_tasks_completed ON tasks(status, completed_at, id)",
    "CREATE INDEX idx_tasks_series ON tasks(series_id, id)",
    "CREATE INDEX idx_task_links_task ON task_links(task_id, id)",
    "CREATE INDEX idx_task_links_entity ON task_links(entity_type, entity_id, task_id)",
    "CREATE INDEX idx_task_history_task ON task_history(task_id, id)",
    "CREATE INDEX idx_task_notifications_user ON task_notifications(user_id, seen_at, id)",
)

TASKS_V3_TABLE_STATEMENTS = (
    "ALTER TABLE tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
    """CREATE TABLE entity_assignments (
        entity_type TEXT NOT NULL CHECK (entity_type IN
            ('order','customer','purchase','repair','task')),
        entity_id TEXT NOT NULL,
        responsible_user_id INTEGER,
        updated_at TEXT NOT NULL,
        updated_by INTEGER NOT NULL,
        PRIMARY KEY(entity_type, entity_id)
    )""",
    """CREATE TABLE assignment_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL CHECK (entity_type IN
            ('order','customer','purchase','repair','task')),
        entity_id TEXT NOT NULL,
        previous_user_id INTEGER,
        new_user_id INTEGER,
        actor_user_id INTEGER NOT NULL,
        comment TEXT NOT NULL DEFAULT '',
        operation_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE inbox_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_user_id INTEGER NOT NULL,
        actor_user_id INTEGER NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN
            ('assigned','reassigned','task_assigned','task_reassigned','mentioned')),
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        read_at TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        operation_key TEXT NOT NULL,
        UNIQUE(recipient_user_id, event_type, operation_key)
    )""",
)

TASKS_V3_INDEX_STATEMENTS = (
    "CREATE INDEX idx_entity_assignments_responsible ON entity_assignments(responsible_user_id, entity_type, entity_id)",
    "CREATE INDEX idx_assignment_history_entity ON assignment_history(entity_type, entity_id, id)",
    "CREATE INDEX idx_inbox_recipient_unread_created ON inbox_events(recipient_user_id, read_at, created_at, id)",
    "CREATE INDEX idx_inbox_recipient_created ON inbox_events(recipient_user_id, created_at, id)",
)

TASKS_V4_TABLE_STATEMENTS = (
    "ALTER TABLE tasks ADD COLUMN deleted_at TEXT",
    "ALTER TABLE tasks ADD COLUMN deleted_by INTEGER",
)

TASKS_V4_INDEX_STATEMENTS = (
    "CREATE INDEX idx_tasks_deleted ON tasks(deleted_at, id)",
)

ORDERS_LEGACY_COLUMNS = (
    ("order_id", "TEXT", 0, None, 1),
    ("source_position", "INTEGER", 1, None, 0),
    ("number_fold", "TEXT", 1, None, 0),
    ("customer_fold", "TEXT", 1, None, 0),
    ("phone_digits", "TEXT", 1, None, 0),
    ("amount_search", "TEXT", 1, None, 0),
    ("date_search", "TEXT", 1, None, 0),
    ("created_sort", "TEXT", 1, None, 0),
    ("status", "TEXT", 1, None, 0),
    ("item_units", "REAL", 0, None, 0),
    ("payload_json", "TEXT", 1, None, 0),
    ("loaded_at", "REAL", 1, None, 0),
)

ORDERS_UPGRADE_STATEMENTS = (
    "ALTER TABLE orders_snapshot ADD COLUMN detail_loaded INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE orders_snapshot ADD COLUMN source TEXT NOT NULL DEFAULT 'tictactoy'",
    "ALTER TABLE orders_snapshot ADD COLUMN external_order_id TEXT",
    "ALTER TABLE orders_snapshot ADD COLUMN extra_fold TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE orders_snapshot ADD COLUMN customer_id INTEGER",
    "UPDATE orders_snapshot SET source = 'tictactoy' WHERE source IS NULL OR trim(source) = ''",
    "UPDATE orders_snapshot SET external_order_id = order_id WHERE external_order_id IS NULL OR trim(external_order_id) = ''",
)

AUTH_MIGRATION = {
    "id": AUTH_MIGRATION_ID,
    "name": "Verified auth schema baseline",
    "checksum": _digest(
        (AUTH_MIGRATION_ID, "auth-contract-v1", LEDGER_SQL)
        + AUTH_TABLE_STATEMENTS + AUTH_INDEX_STATEMENTS + AUTH_UPGRADE_STATEMENTS
    ),
}
AUTH_PREFERENCES_MIGRATION = {
    "id": AUTH_PREFERENCES_MIGRATION_ID,
    "name": "Per-user navigation preferences",
    "checksum": _digest(
        (AUTH_PREFERENCES_MIGRATION_ID, "user-navigation-preferences-v1")
        + AUTH_PREFERENCES_TABLE_STATEMENTS
    ),
}
AUTH_NOTIFICATIONS_MIGRATION = {
    "id": AUTH_NOTIFICATIONS_MIGRATION_ID,
    "name": "Per-user order and task notifications",
    "checksum": _digest(
        (AUTH_NOTIFICATIONS_MIGRATION_ID, "user-notifications-v1")
        + AUTH_NOTIFICATIONS_TABLE_STATEMENTS
        + AUTH_NOTIFICATIONS_INDEX_STATEMENTS
    ),
}
ORDERS_MIGRATION = {
    "id": ORDERS_MIGRATION_ID,
    "name": "Verified orders and customers schema baseline",
    "checksum": _digest(
        (ORDERS_MIGRATION_ID, "orders-customers-contract-v1", LEDGER_SQL)
        + ORDERS_TABLE_STATEMENTS + ORDERS_INDEX_STATEMENTS
        + ORDERS_UPGRADE_STATEMENTS
    ),
}
TASKS_MIGRATION = {
    "id": TASKS_MIGRATION_ID,
    "name": "Internal tasks schema",
    "checksum": _digest(
        (TASKS_MIGRATION_ID, "tasks-contract-v1", LEDGER_SQL)
        + TASKS_TABLE_STATEMENTS + TASKS_INDEX_STATEMENTS
    ),
}
TASKS_V2_MIGRATION = {
    "id": TASKS_V2_MIGRATION_ID,
    "name": "Unified tasks center schema",
    "checksum": _digest(
        (TASKS_V2_MIGRATION_ID, "tasks-contract-v2")
        + TASKS_V2_TABLE_STATEMENTS + TASKS_V2_INDEX_STATEMENTS
    ),
}
TASKS_V3_MIGRATION = {
    "id": TASKS_V3_MIGRATION_ID,
    "name": "Collaboration responsibility and inbox",
    "checksum": _digest(
        (TASKS_V3_MIGRATION_ID, "collaboration-responsibility-v1")
        + TASKS_V3_TABLE_STATEMENTS + TASKS_V3_INDEX_STATEMENTS
    ),
}
TASKS_V4_MIGRATION = {
    "id": TASKS_V4_MIGRATION_ID,
    "name": "Soft deletion for tasks",
    "checksum": _digest(
        (TASKS_V4_MIGRATION_ID, "task-soft-delete-v1")
        + TASKS_V4_TABLE_STATEMENTS + TASKS_V4_INDEX_STATEMENTS
    ),
}
DOMAIN_MIGRATIONS = {
    "auth": AUTH_NOTIFICATIONS_MIGRATION,
    "orders": ORDERS_MIGRATION,
    "tasks": TASKS_V4_MIGRATION,
}


AUTH_V1_EXPECTED_COLUMNS = {
    "users": AUTH_LEGACY_USER_COLUMNS + (
        ("email_verified_at", "INTEGER", 0, None, 0),
        ("updated_at", "INTEGER", 0, None, 0),
        ("session_version", "INTEGER", 1, "1", 0),
        ("last_login_at", "INTEGER", 0, None, 0),
    ),
    "invitations": (
        ("id", "INTEGER", 0, None, 1), ("token_hash", "TEXT", 1, None, 0),
        ("email", "TEXT", 0, None, 0), ("email_normalized", "TEXT", 0, None, 0),
        ("role", "TEXT", 1, None, 0), ("expires_at", "INTEGER", 1, None, 0),
        ("state", "TEXT", 1, "'active'", 0), ("created_by", "INTEGER", 0, None, 0),
        ("created_at", "INTEGER", 1, None, 0), ("used_at", "INTEGER", 0, None, 0),
        ("used_by", "INTEGER", 0, None, 0),
    ),
    "auth_attempts": (
        ("id", "INTEGER", 0, None, 1), ("bucket", "TEXT", 1, None, 0),
        ("attempted_at", "INTEGER", 1, None, 0),
    ),
    "auth_tokens": (
        ("id", "INTEGER", 0, None, 1), ("user_id", "INTEGER", 1, None, 0),
        ("token_hash", "TEXT", 1, None, 0), ("token_type", "TEXT", 1, None, 0),
        ("expires_at", "INTEGER", 1, None, 0), ("used_at", "INTEGER", 0, None, 0),
        ("created_at", "INTEGER", 1, None, 0),
    ),
    "auth_sessions": (
        ("id", "INTEGER", 0, None, 1), ("session_hash", "TEXT", 1, None, 0),
        ("user_id", "INTEGER", 0, None, 0), ("data", "TEXT", 1, None, 0),
        ("expires_at", "INTEGER", 1, None, 0), ("created_at", "INTEGER", 1, None, 0),
        ("updated_at", "INTEGER", 1, None, 0),
    ),
}
AUTH_EXPECTED_COLUMNS = dict(AUTH_V1_EXPECTED_COLUMNS)
AUTH_EXPECTED_COLUMNS["user_navigation_preferences"] = (
    ("user_id", "INTEGER", 0, None, 1),
    ("ordered_keys", "TEXT", 1, None, 0),
    ("hidden_keys", "TEXT", 1, None, 0),
    ("updated_at", "INTEGER", 1, None, 0),
)
AUTH_V2_EXPECTED_COLUMNS = dict(AUTH_EXPECTED_COLUMNS)
AUTH_EXPECTED_COLUMNS["notification_entities"] = (
    ("entity_type", "TEXT", 1, None, 1),
    ("entity_id", "TEXT", 1, None, 2),
    ("first_seen_at", "TEXT", 1, None, 0),
)
AUTH_EXPECTED_COLUMNS["user_notifications"] = (
    ("id", "INTEGER", 0, None, 1), ("user_id", "INTEGER", 1, None, 0),
    ("type", "TEXT", 1, None, 0), ("entity_type", "TEXT", 1, None, 0),
    ("entity_id", "TEXT", 1, None, 0), ("title", "TEXT", 1, None, 0),
    ("message", "TEXT", 1, "''", 0), ("metadata_json", "TEXT", 1, "'{}'", 0),
    ("target_url", "TEXT", 1, None, 0), ("created_at", "TEXT", 1, None, 0),
    ("read_at", "TEXT", 0, None, 0), ("delivered_at", "TEXT", 0, None, 0),
    ("dedupe_key", "TEXT", 1, None, 0),
)
AUTH_EXPECTED_COLUMNS["user_notification_preferences"] = (
    ("user_id", "INTEGER", 0, None, 1),
    ("order_sound", "INTEGER", 1, "1", 0),
    ("task_sound", "INTEGER", 1, "1", 0),
    ("browser_notifications", "INTEGER", 1, "0", 0),
    ("updated_at", "TEXT", 1, None, 0),
)

ORDERS_EXPECTED_COLUMNS = {
    "orders_snapshot": ORDERS_LEGACY_COLUMNS + (
        ("detail_loaded", "INTEGER", 1, "0", 0),
        ("source", "TEXT", 1, "'tictactoy'", 0),
        ("external_order_id", "TEXT", 0, None, 0),
        ("extra_fold", "TEXT", 1, "''", 0),
        ("customer_id", "INTEGER", 0, None, 0),
    ),
    "customers": (
        ("id", "INTEGER", 0, None, 1), ("name", "TEXT", 1, "''", 0),
        ("name_fold", "TEXT", 1, "''", 0), ("phone", "TEXT", 1, "''", 0),
        ("normalized_phone", "TEXT", 1, "''", 0), ("email", "TEXT", 1, "''", 0),
        ("normalized_email", "TEXT", 1, "''", 0), ("country", "TEXT", 1, "''", 0),
        ("region", "TEXT", 1, "''", 0), ("city", "TEXT", 1, "''", 0),
        ("created_at", "TEXT", 1, None, 0), ("updated_at", "TEXT", 1, None, 0),
    ),
    "orders_snapshot_meta": (
        ("key", "TEXT", 0, None, 1), ("value", "TEXT", 1, None, 0),
    ),
}

TASKS_V1_EXPECTED_COLUMNS = {
    "tasks": (
        ("id", "INTEGER", 0, None, 1), ("title", "TEXT", 1, None, 0),
        ("description", "TEXT", 1, "''", 0), ("section", "TEXT", 1, "'inbox'", 0),
        ("status", "TEXT", 1, "'active'", 0), ("priority", "TEXT", 1, "'other'", 0),
        ("due_date", "TEXT", 0, None, 0), ("due_time", "TEXT", 0, None, 0),
        ("author_id", "INTEGER", 1, None, 0), ("assignee_id", "INTEGER", 1, None, 0),
        ("entity_type", "TEXT", 0, None, 0), ("entity_id", "TEXT", 0, None, 0),
        ("entity_label", "TEXT", 0, None, 0), ("entity_href", "TEXT", 0, None, 0),
        ("created_at", "TEXT", 1, None, 0), ("updated_at", "TEXT", 1, None, 0),
        ("updated_by", "INTEGER", 0, None, 0), ("completed_at", "TEXT", 0, None, 0),
        ("completed_by", "INTEGER", 0, None, 0), ("idempotency_key", "TEXT", 0, None, 0),
    ),
}

TASKS_EXPECTED_COLUMNS = {
    "tasks": (
        ("id", "INTEGER", 0, None, 1), ("title", "TEXT", 1, None, 0),
        ("description", "TEXT", 1, "''", 0), ("section", "TEXT", 1, "'inbox'", 0),
        ("status", "TEXT", 1, "'new'", 0), ("priority", "TEXT", 1, "'other'", 0),
        ("due_date", "TEXT", 0, None, 0), ("due_time", "TEXT", 0, None, 0),
        ("reminder_at", "TEXT", 0, None, 0), ("author_id", "INTEGER", 1, None, 0),
        ("assignee_id", "INTEGER", 1, None, 0), ("source_comment", "TEXT", 1, "''", 0),
        ("contact_name", "TEXT", 1, "''", 0), ("contact_phone", "TEXT", 1, "''", 0),
        ("contact_email", "TEXT", 1, "''", 0), ("contact_channel", "TEXT", 1, "''", 0),
        ("waiting_for", "TEXT", 1, "''", 0), ("check_date", "TEXT", 0, None, 0),
        ("waiting_comment", "TEXT", 1, "''", 0), ("repeat_type", "TEXT", 1, "'none'", 0),
        ("repeat_interval", "INTEGER", 1, "1", 0), ("series_id", "TEXT", 0, None, 0),
        ("parent_task_id", "INTEGER", 0, None, 0), ("completion_result", "TEXT", 1, "''", 0),
        ("previous_status", "TEXT", 0, None, 0), ("created_at", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0), ("updated_by", "INTEGER", 0, None, 0),
        ("completed_at", "TEXT", 0, None, 0), ("completed_by", "INTEGER", 0, None, 0),
        ("cancelled_at", "TEXT", 0, None, 0), ("cancelled_by", "INTEGER", 0, None, 0),
        ("idempotency_key", "TEXT", 0, None, 0), ("next_occurrence_key", "TEXT", 0, None, 0),
        ("version", "INTEGER", 1, "1", 0),
        ("deleted_at", "TEXT", 0, None, 0), ("deleted_by", "INTEGER", 0, None, 0),
    ),
    "task_links": (
        ("id", "INTEGER", 0, None, 1), ("task_id", "INTEGER", 1, None, 0),
        ("entity_type", "TEXT", 1, None, 0), ("entity_id", "TEXT", 1, None, 0),
        ("entity_label", "TEXT", 1, "''", 0), ("entity_href", "TEXT", 1, "''", 0),
        ("created_at", "TEXT", 1, None, 0), ("created_by", "INTEGER", 1, None, 0),
    ),
    "task_history": (
        ("id", "INTEGER", 0, None, 1), ("task_id", "INTEGER", 1, None, 0),
        ("event_type", "TEXT", 1, None, 0), ("actor_id", "INTEGER", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0), ("details_json", "TEXT", 1, "'{}'", 0),
    ),
    "task_notifications": (
        ("id", "INTEGER", 0, None, 1), ("task_id", "INTEGER", 1, None, 0),
        ("user_id", "INTEGER", 1, None, 0), ("notification_type", "TEXT", 1, None, 0),
        ("notification_key", "TEXT", 1, None, 0), ("created_at", "TEXT", 1, None, 0),
        ("seen_at", "TEXT", 0, None, 0),
    ),
    "entity_assignments": (
        ("entity_type", "TEXT", 1, None, 1), ("entity_id", "TEXT", 1, None, 2),
        ("responsible_user_id", "INTEGER", 0, None, 0),
        ("updated_at", "TEXT", 1, None, 0), ("updated_by", "INTEGER", 1, None, 0),
    ),
    "assignment_history": (
        ("id", "INTEGER", 0, None, 1), ("entity_type", "TEXT", 1, None, 0),
        ("entity_id", "TEXT", 1, None, 0), ("previous_user_id", "INTEGER", 0, None, 0),
        ("new_user_id", "INTEGER", 0, None, 0), ("actor_user_id", "INTEGER", 1, None, 0),
        ("comment", "TEXT", 1, "''", 0), ("operation_key", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
    ),
    "inbox_events": (
        ("id", "INTEGER", 0, None, 1), ("recipient_user_id", "INTEGER", 1, None, 0),
        ("actor_user_id", "INTEGER", 1, None, 0), ("event_type", "TEXT", 1, None, 0),
        ("entity_type", "TEXT", 1, None, 0), ("entity_id", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0), ("read_at", "TEXT", 0, None, 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0), ("operation_key", "TEXT", 1, None, 0),
    ),
}

AUTH_V1_INDEXES = {
    "invitations_state_expires": (0, ("state", "expires_at")),
    "auth_attempts_bucket_time": (0, ("bucket", "attempted_at")),
    "auth_tokens_user_type": (0, ("user_id", "token_type", "created_at")),
    "auth_sessions_expiry": (0, ("expires_at",)),
    "auth_sessions_user": (0, ("user_id",)),
}
AUTH_INDEXES = dict(AUTH_V1_INDEXES)
AUTH_INDEXES.update({
    "idx_user_notifications_feed": (0, ("user_id", "id")),
    "idx_user_notifications_unread": (0, ("user_id", "read_at", "id")),
})
ORDERS_INDEXES = {
    "idx_customers_normalized_phone": (0, ("normalized_phone",)),
    "idx_customers_normalized_email": (0, ("normalized_email",)),
    "idx_customers_name": (0, ("name_fold",)),
    "idx_orders_snapshot_ordering": (0, ("source_position", "order_id")),
    "idx_orders_snapshot_status_ordering": (0, ("status", "source_position", "order_id")),
    "idx_orders_snapshot_created": (0, ("created_sort", "source_position", "order_id")),
    "idx_orders_snapshot_source_external": (1, ("source", "external_order_id")),
    "idx_orders_snapshot_source_ordering": (0, ("source", "source_position", "order_id")),
    "idx_orders_snapshot_customer_created": (0, ("customer_id", "created_sort")),
}
TASKS_V1_INDEXES = {
    "idx_tasks_status_due": (0, ("status", "due_date", "due_time", "id")),
    "idx_tasks_assignee_status_due": (0, ("assignee_id", "status", "due_date", "id")),
    "idx_tasks_section_status": (0, ("section", "status", "id")),
    "idx_tasks_entity": (0, ("entity_type", "entity_id", "status")),
    "idx_tasks_completed": (0, ("status", "completed_at", "id")),
}
TASKS_INDEXES = {
    "idx_tasks_status_due": (0, ("status", "due_date", "due_time", "id")),
    "idx_tasks_assignee_status_due": (0, ("assignee_id", "status", "due_date", "id")),
    "idx_tasks_section_status": (0, ("section", "status", "id")),
    "idx_tasks_check_date": (0, ("status", "check_date", "id")),
    "idx_tasks_completed": (0, ("status", "completed_at", "id")),
    "idx_tasks_series": (0, ("series_id", "id")),
    "idx_task_links_task": (0, ("task_id", "id")),
    "idx_task_links_entity": (0, ("entity_type", "entity_id", "task_id")),
    "idx_task_history_task": (0, ("task_id", "id")),
    "idx_task_notifications_user": (0, ("user_id", "seen_at", "id")),
    "idx_entity_assignments_responsible": (0, ("responsible_user_id", "entity_type", "entity_id")),
    "idx_assignment_history_entity": (0, ("entity_type", "entity_id", "id")),
    "idx_inbox_recipient_unread_created": (0, ("recipient_user_id", "read_at", "created_at", "id")),
    "idx_inbox_recipient_created": (0, ("recipient_user_id", "created_at", "id")),
    "idx_tasks_deleted": (0, ("deleted_at", "id")),
}


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tables(connection):
    return {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _columns(connection, table):
    return tuple(
        (str(row[1]), str(row[2] or "").upper(), int(row[3]), row[4], int(row[5]))
        for row in connection.execute("PRAGMA table_info({})".format(table)).fetchall()
    )


def _index_contract(connection, table):
    result = {}
    for row in connection.execute("PRAGMA index_list({})".format(table)).fetchall():
        name = str(row[1])
        if name.startswith("sqlite_autoindex_"):
            continue
        columns = tuple(
            str(item[2]) for item in connection.execute(
                "PRAGMA index_info('{}')".format(name.replace("'", "''"))
            ).fetchall()
        )
        result[name] = (int(row[2]), columns)
    return result


def _unique_columns(connection, table):
    return {
        tuple(
            str(item[2]) for item in connection.execute(
                "PRAGMA index_info('{}')".format(str(row[1]).replace("'", "''"))
            ).fetchall()
        )
        for row in connection.execute("PRAGMA index_list({})".format(table)).fetchall()
        if int(row[2]) == 1
    }


def _verify_ledger_columns(connection):
    expected = {
        "migration_id", "name", "checksum", "state", "applied_at",
        "app_commit", "details_json",
    }
    actual = {row[1] for row in connection.execute(
        "PRAGMA table_info({})".format(LEDGER_TABLE)
    ).fetchall()}
    if actual != expected:
        raise DomainMigrationError("domain migration ledger columns differ")


def _verify_ledger(connection, migration):
    if LEDGER_TABLE not in _tables(connection):
        raise MigrationRequiredError(
            "migration required: {} ledger is missing".format(migration["id"])
        )
    rows = connection.execute(
        "SELECT migration_id, name, checksum, state FROM " + LEDGER_TABLE
    ).fetchall()
    if len(rows) != 1:
        raise DomainMigrationError("unexpected domain migration ledger length")
    row = rows[0]
    if str(row[0]) != migration["id"]:
        raise DomainMigrationError("unknown migration in domain ledger: {}".format(row[0]))
    if str(row[1]) != migration["name"] or str(row[2]) != migration["checksum"]:
        raise DomainMigrationError("migration checksum mismatch: {}".format(row[0]))
    if str(row[3]) != "applied":
        raise DomainMigrationError(
            "migration is not fully applied: {} state={}".format(row[0], row[3])
        )


def _verify_auth_ledger(connection, require_latest=True):
    if LEDGER_TABLE not in _tables(connection):
        raise MigrationRequiredError(
            "migration required: {} ledger is missing".format(
                AUTH_PREFERENCES_MIGRATION_ID
            )
        )
    rows = connection.execute(
        "SELECT migration_id, name, checksum, state FROM "
        + LEDGER_TABLE
        + " ORDER BY migration_id"
    ).fetchall()
    expected = {
        migration["id"]: migration
        for migration in (
            AUTH_MIGRATION, AUTH_PREFERENCES_MIGRATION,
            AUTH_NOTIFICATIONS_MIGRATION,
        )
    }
    for row in rows:
        migration = expected.get(str(row[0]))
        if migration is None:
            raise DomainMigrationError(
                "unknown migration in auth ledger: {}".format(row[0])
            )
        if str(row[1]) != migration["name"]:
            raise DomainMigrationError(
                "auth migration ledger mismatch: {}".format(row[0])
            )
        if str(row[2]) != migration["checksum"]:
            raise DomainMigrationError(
                "migration checksum mismatch: {}".format(row[0])
            )
        if str(row[3]) != "applied":
            raise DomainMigrationError(
                "auth migration is not fully applied: {}".format(row[0])
            )
    ids = {str(row[0]) for row in rows}
    if AUTH_MIGRATION_ID not in ids:
        raise MigrationRequiredError("migration required: auth baseline is missing")
    if require_latest and AUTH_NOTIFICATIONS_MIGRATION_ID not in ids:
        raise MigrationRequiredError(
            "migration required: user notifications"
        )
    expected_lengths = {3} if require_latest else {1, 2}
    if len(rows) not in expected_lengths:
        raise DomainMigrationError("unexpected auth migration ledger length")


def _require_integrity(connection, label):
    quick = [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()]
    if quick != ["ok"]:
        raise DomainMigrationError("{} quick_check failed: {}".format(label, quick))
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise DomainMigrationError(
            "{} foreign_key_check found {} violation(s)".format(label, len(foreign_keys))
        )


def _verify_columns(connection, expected):
    for table, columns in expected.items():
        actual = _columns(connection, table)
        if sorted(actual) != sorted(columns):
            raise DomainMigrationError(
                "schema contract mismatch for {}: columns differ".format(table)
            )


def _verify_indexes(connection, expected, table_by_prefix):
    actual = {}
    for table in table_by_prefix:
        actual.update(_index_contract(connection, table))
    if actual != expected:
        raise DomainMigrationError("schema contract mismatch: named indexes differ")


def verify_auth_schema(connection, require_ledger=True, include_preferences=True,
                       include_notifications=True):
    if include_notifications:
        expected_columns = AUTH_EXPECTED_COLUMNS
    elif include_preferences:
        expected_columns = AUTH_V2_EXPECTED_COLUMNS
    else:
        expected_columns = AUTH_V1_EXPECTED_COLUMNS
    expected_tables = set(expected_columns)
    if require_ledger:
        expected_tables.add(LEDGER_TABLE)
    if _tables(connection) != expected_tables:
        raise DomainMigrationError("auth schema contract: unexpected table set")
    if require_ledger:
        _verify_ledger_columns(connection)
    _verify_columns(connection, expected_columns)
    _verify_indexes(
        connection,
        AUTH_INDEXES if include_notifications else AUTH_V1_INDEXES,
        expected_columns,
    )
    foreign_keys = {
        table: sorted(tuple(str(value) for value in row[2:8]) for row in connection.execute(
            "PRAGMA foreign_key_list({})".format(table)
        ).fetchall())
        for table in expected_columns
    }
    expected_foreign_keys = {
        "users": [],
        "auth_attempts": [],
        "invitations": sorted((
            ("users", "created_by", "id", "NO ACTION", "NO ACTION", "NONE"),
            ("users", "used_by", "id", "NO ACTION", "NO ACTION", "NONE"),
        )),
        "auth_tokens": [
            ("users", "user_id", "id", "NO ACTION", "NO ACTION", "NONE")
        ],
        "auth_sessions": [
            ("users", "user_id", "id", "NO ACTION", "NO ACTION", "NONE")
        ],
    }
    if include_preferences:
        expected_foreign_keys["user_navigation_preferences"] = [
            ("users", "user_id", "id", "NO ACTION", "CASCADE", "NONE")
        ]
    if include_notifications:
        expected_foreign_keys["notification_entities"] = []
        expected_foreign_keys["user_notifications"] = [
            ("users", "user_id", "id", "NO ACTION", "CASCADE", "NONE")
        ]
        expected_foreign_keys["user_notification_preferences"] = [
            ("users", "user_id", "id", "NO ACTION", "CASCADE", "NONE")
        ]
    if foreign_keys != expected_foreign_keys:
        raise DomainMigrationError("auth schema contract: foreign keys differ")
    expected_unique = {
        "users": {("email_normalized",)},
        "invitations": {("token_hash",)},
        "auth_tokens": {("token_hash",)},
        "auth_sessions": {("session_hash",)},
    }
    for table, required in expected_unique.items():
        if not required.issubset(_unique_columns(connection, table)):
            raise DomainMigrationError(
                "auth schema contract: unique constraint differs for {}".format(table)
            )
    if include_notifications and ("dedupe_key",) not in _unique_columns(
        connection, "user_notifications"
    ):
        raise DomainMigrationError(
            "auth schema contract: notification dedupe key is not unique"
        )
    if connection.execute(
        "SELECT 1 FROM users WHERE role NOT IN ('employee','admin') OR active NOT IN (0,1) LIMIT 1"
    ).fetchone():
        raise DomainMigrationError("auth data contract: invalid user role or active flag")
    if connection.execute(
        "SELECT 1 FROM invitations WHERE state NOT IN ('active','used','revoked') LIMIT 1"
    ).fetchone():
        raise DomainMigrationError("auth data contract: invalid invitation state")
    return True


def verify_orders_schema(connection, require_ledger=True):
    expected_tables = set(ORDERS_EXPECTED_COLUMNS)
    if require_ledger:
        expected_tables.add(LEDGER_TABLE)
    if _tables(connection) != expected_tables:
        raise DomainMigrationError("orders schema contract: unexpected table set")
    if require_ledger:
        _verify_ledger_columns(connection)
    _verify_columns(connection, ORDERS_EXPECTED_COLUMNS)
    _verify_indexes(connection, ORDERS_INDEXES, ORDERS_EXPECTED_COLUMNS)
    if any(connection.execute(
        "PRAGMA foreign_key_list({})".format(table)
    ).fetchall() for table in ORDERS_EXPECTED_COLUMNS):
        raise DomainMigrationError("orders schema contract: unexpected foreign key")
    if ("order_id",) not in _unique_columns(connection, "orders_snapshot"):
        raise DomainMigrationError("orders schema contract: order_id is not unique")
    if ("key",) not in _unique_columns(connection, "orders_snapshot_meta"):
        raise DomainMigrationError("orders schema contract: meta key is not unique")
    if connection.execute(
        "SELECT 1 FROM orders_snapshot WHERE trim(source) = '' OR source IS NULL "
        "OR external_order_id IS NULL OR trim(external_order_id) = '' LIMIT 1"
    ).fetchone():
        raise DomainMigrationError("orders data contract: source identity is incomplete")
    duplicates = connection.execute(
        "SELECT source, external_order_id FROM orders_snapshot "
        "GROUP BY source, external_order_id HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if duplicates:
        raise DomainMigrationError("orders data contract: duplicate source identity")
    return True


def verify_tasks_schema(connection, require_ledger=True):
    expected_tables = set(TASKS_EXPECTED_COLUMNS)
    if require_ledger:
        expected_tables.add(LEDGER_TABLE)
    if _tables(connection) != expected_tables:
        raise DomainMigrationError("tasks schema contract: unexpected table set")
    if require_ledger:
        _verify_ledger_columns(connection)
        _verify_tasks_ledger(connection)
    _verify_columns(connection, TASKS_EXPECTED_COLUMNS)
    _verify_indexes(connection, TASKS_INDEXES, TASKS_EXPECTED_COLUMNS)
    if any(connection.execute(
        "PRAGMA foreign_key_list({})".format(table)
    ).fetchall() for table in TASKS_EXPECTED_COLUMNS):
        raise DomainMigrationError("tasks schema contract: unexpected foreign key")
    if ("idempotency_key",) not in _unique_columns(connection, "tasks"):
        raise DomainMigrationError("tasks schema contract: idempotency key is not unique")
    invalid = connection.execute(
        "SELECT 1 FROM tasks WHERE status NOT IN ('new','in_progress','waiting','completed','cancelled') "
        "OR section NOT IN ('inbox','anytime','someday') "
        "OR priority NOT IN ('urgent','important','other') "
        "OR repeat_type NOT IN ('none','daily','weekdays','weekly','monthly','custom') LIMIT 1"
    ).fetchone()
    if invalid:
        raise DomainMigrationError("tasks data contract: invalid enum value")
    return True


def _verify_tasks_ledger(connection):
    rows = connection.execute(
        "SELECT migration_id,name,checksum,state FROM " + LEDGER_TABLE + " ORDER BY migration_id"
    ).fetchall()
    expected = {
        migration["id"]: migration for migration in (
            TASKS_MIGRATION, TASKS_V2_MIGRATION, TASKS_V3_MIGRATION,
            TASKS_V4_MIGRATION,
        )
    }
    if len(rows) != len(expected):
        raise MigrationRequiredError("migration required: task soft deletion v1")
    for row in rows:
        migration = expected.get(str(row[0]))
        if not migration:
            raise DomainMigrationError("unknown migration in tasks ledger: {}".format(row[0]))
        if (str(row[1]), str(row[2]), str(row[3])) != (
            migration["name"], migration["checksum"], "applied"
        ):
            raise DomainMigrationError("tasks migration ledger mismatch: {}".format(row[0]))


def _verify_tasks_v1_schema(connection):
    if _tables(connection) != {LEDGER_TABLE, "tasks"}:
        raise DomainMigrationError("tasks v1 schema contract: unexpected table set")
    _verify_columns(connection, TASKS_V1_EXPECTED_COLUMNS)
    _verify_indexes(connection, TASKS_V1_INDEXES, TASKS_V1_EXPECTED_COLUMNS)
    _verify_ledger(connection, TASKS_MIGRATION)


def _create_tasks_v2(connection, observer):
    for statement in TASKS_V2_TABLE_STATEMENTS + TASKS_V2_INDEX_STATEMENTS:
        _execute(connection, statement, observer)


def _create_tasks_v3(connection, observer):
    for statement in TASKS_V3_TABLE_STATEMENTS + TASKS_V3_INDEX_STATEMENTS:
        _execute(connection, statement, observer)


def _create_tasks_v4(connection, observer):
    for statement in TASKS_V4_TABLE_STATEMENTS + TASKS_V4_INDEX_STATEMENTS:
        _execute(connection, statement, observer)


def _apply_tasks_v2_migrations(path, app_commit, observer):
    with DomainMigrationLock(path, "tasks"):
        connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            tables = _tables(connection)
            if not tables:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    _execute(connection, LEDGER_SQL, observer)
                    _create_tasks_v2(connection, observer)
                    now = _utc_now()
                    _create_tasks_v3(connection, observer)
                    _create_tasks_v4(connection, observer)
                    for migration in (
                        TASKS_MIGRATION, TASKS_V2_MIGRATION,
                        TASKS_V3_MIGRATION, TASKS_V4_MIGRATION,
                    ):
                        connection.execute(
                            "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                            "VALUES (?,?,?,'applied',?,?,?)".format(LEDGER_TABLE),
                            (migration["id"], migration["name"], migration["checksum"], now,
                             str(app_commit or "") or None,
                             json.dumps({"source_state": "fresh", "transactional": True}, sort_keys=True)),
                        )
                    verify_tasks_schema(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            else:
                ledger_ids = set()
                if LEDGER_TABLE in tables:
                    ledger_ids = {str(row[0]) for row in connection.execute(
                        "SELECT migration_id FROM " + LEDGER_TABLE
                    ).fetchall()}
                if TASKS_V4_MIGRATION_ID in ledger_ids:
                    verify_tasks_schema(connection)
                elif TASKS_V3_MIGRATION_ID in ledger_ids:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        _create_tasks_v4(connection, observer)
                        migration = TASKS_V4_MIGRATION
                        connection.execute(
                            "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                            "VALUES (?,?,?,'applied',?,?,?)".format(LEDGER_TABLE),
                            (migration["id"], migration["name"], migration["checksum"], _utc_now(),
                             str(app_commit or "") or None,
                             json.dumps({"source_state": "tasks-v3", "transactional": True}, sort_keys=True)),
                        )
                        verify_tasks_schema(connection)
                        _require_integrity(connection, "tasks-v4-migration")
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                elif TASKS_V2_MIGRATION_ID in ledger_ids:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        _create_tasks_v3(connection, observer)
                        _create_tasks_v4(connection, observer)
                        migration = TASKS_V3_MIGRATION
                        connection.execute(
                            "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                            "VALUES (?,?,?,'applied',?,?,?)".format(LEDGER_TABLE),
                            (migration["id"], migration["name"], migration["checksum"], _utc_now(),
                             str(app_commit or "") or None,
                             json.dumps({"source_state": "tasks-v2", "transactional": True}, sort_keys=True)),
                        )
                        migration = TASKS_V4_MIGRATION
                        connection.execute(
                            "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                            "VALUES (?,?,?,'applied',?,?,?)".format(LEDGER_TABLE),
                            (migration["id"], migration["name"], migration["checksum"], _utc_now(),
                             str(app_commit or "") or None,
                             json.dumps({"source_state": "tasks-v2", "transactional": True}, sort_keys=True)),
                        )
                        verify_tasks_schema(connection)
                        _require_integrity(connection, "tasks-v3-migration")
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                else:
                    _verify_tasks_v1_schema(connection)
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute("ALTER TABLE tasks RENAME TO tasks_v1")
                        for index_name in TASKS_V1_INDEXES:
                            connection.execute("DROP INDEX {}".format(index_name))
                        _create_tasks_v2(connection, observer)
                        connection.execute(
                            "INSERT INTO tasks(id,title,description,section,status,priority,due_date,due_time,"
                            "author_id,assignee_id,created_at,updated_at,updated_by,completed_at,completed_by,"
                            "idempotency_key,previous_status) "
                            "SELECT id,title,description,section,CASE WHEN status='completed' THEN 'completed' ELSE 'new' END,"
                            "priority,due_date,due_time,author_id,assignee_id,created_at,updated_at,updated_by,"
                            "completed_at,completed_by,idempotency_key,CASE WHEN status='completed' THEN 'new' ELSE NULL END "
                            "FROM tasks_v1"
                        )
                        _create_tasks_v3(connection, observer)
                        _create_tasks_v4(connection, observer)
                        collaboration = TASKS_V3_MIGRATION
                        connection.execute(
                            "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                            "VALUES (?,?,?,'applied',?,?,?)".format(LEDGER_TABLE),
                            (collaboration["id"], collaboration["name"], collaboration["checksum"], _utc_now(),
                             str(app_commit or "") or None,
                            json.dumps({"source_state": "tasks-v1", "transactional": True}, sort_keys=True)),
                        )
                        soft_delete = TASKS_V4_MIGRATION
                        connection.execute(
                            "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                            "VALUES (?,?,?,'applied',?,?,?)".format(LEDGER_TABLE),
                            (soft_delete["id"], soft_delete["name"], soft_delete["checksum"], _utc_now(),
                             str(app_commit or "") or None,
                             json.dumps({"source_state": "tasks-v1", "transactional": True}, sort_keys=True)),
                        )
                        connection.execute(
                            "INSERT INTO task_links(task_id,entity_type,entity_id,entity_label,entity_href,created_at,created_by) "
                            "SELECT id,entity_type,entity_id,COALESCE(entity_label,''),COALESCE(entity_href,''),created_at,author_id "
                            "FROM tasks_v1 WHERE entity_type IS NOT NULL AND entity_id IS NOT NULL"
                        )
                        connection.execute(
                            "INSERT INTO task_history(task_id,event_type,actor_id,created_at,details_json) "
                            "SELECT id,'migrated',author_id,?,? FROM tasks_v1",
                            (_utc_now(), '{"from":"tasks-v1"}'),
                        )
                        connection.execute("DROP TABLE tasks_v1")
                        migration = TASKS_V2_MIGRATION
                        connection.execute(
                            "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                            "VALUES (?,?,?,'applied',?,?,?)".format(LEDGER_TABLE),
                            (migration["id"], migration["name"], migration["checksum"], _utc_now(),
                             str(app_commit or "") or None,
                             json.dumps({"source_state": "tasks-v1", "transactional": True}, sort_keys=True)),
                        )
                        verify_tasks_schema(connection)
                        _require_integrity(connection, "tasks-v2-migration")
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
            return migration_report(connection, "tasks")
        finally:
            connection.close()


def _insert_applied_migration(connection, migration, app_commit, source_state):
    connection.execute(
        "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
        "VALUES (?,?,?,'applied',?,?,?)".format(LEDGER_TABLE),
        (
            migration["id"],
            migration["name"],
            migration["checksum"],
            _utc_now(),
            str(app_commit or "") or None,
            json.dumps(
                {"source_state": source_state, "transactional": True},
                sort_keys=True,
            ),
        ),
    )


def _apply_auth_preferences_migration(path, app_commit, observer):
    with DomainMigrationLock(path, "auth"):
        connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            tables = _tables(connection)
            if LEDGER_TABLE in tables:
                ledger_ids = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT migration_id FROM " + LEDGER_TABLE
                    ).fetchall()
                }
                if AUTH_NOTIFICATIONS_MIGRATION_ID in ledger_ids:
                    _verify_auth_ledger(connection)
                    verify_auth_schema(connection)
                    _require_integrity(connection, "auth")
                    return migration_report(connection, "auth")

                if AUTH_PREFERENCES_MIGRATION_ID in ledger_ids:
                    _verify_auth_ledger(connection, require_latest=False)
                    verify_auth_schema(
                        connection, include_notifications=False
                    )
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        for statement in (
                            AUTH_NOTIFICATIONS_TABLE_STATEMENTS
                            + AUTH_NOTIFICATIONS_INDEX_STATEMENTS
                        ):
                            _execute(connection, statement, observer)
                        _insert_applied_migration(
                            connection,
                            AUTH_NOTIFICATIONS_MIGRATION,
                            app_commit,
                            "auth-v2",
                        )
                        verify_auth_schema(connection)
                        _verify_auth_ledger(connection)
                        _require_integrity(connection, "auth-notifications-migration")
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                    return migration_report(connection, "auth")

                _verify_auth_ledger(connection, require_latest=False)
                verify_auth_schema(
                    connection, include_preferences=False,
                    include_notifications=False,
                )
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in AUTH_PREFERENCES_TABLE_STATEMENTS:
                        _execute(connection, statement, observer)
                    _insert_applied_migration(
                        connection,
                        AUTH_PREFERENCES_MIGRATION,
                        app_commit,
                        "auth-v1",
                    )
                    for statement in (
                        AUTH_NOTIFICATIONS_TABLE_STATEMENTS
                        + AUTH_NOTIFICATIONS_INDEX_STATEMENTS
                    ):
                        _execute(connection, statement, observer)
                    _insert_applied_migration(
                        connection,
                        AUTH_NOTIFICATIONS_MIGRATION,
                        app_commit,
                        "auth-v1",
                    )
                    verify_auth_schema(connection)
                    _verify_auth_ledger(connection)
                    _require_integrity(connection, "auth-preferences-migration")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                return migration_report(connection, "auth")

            state = _legacy_state(connection, "auth")
            if state == "current":
                verify_auth_schema(
                    connection,
                    require_ledger=False,
                    include_preferences=False,
                    include_notifications=False,
                )
            connection.execute("BEGIN IMMEDIATE")
            try:
                _execute(connection, LEDGER_SQL, observer)
                _apply_auth(connection, state, observer)
                _insert_applied_migration(
                    connection, AUTH_MIGRATION, app_commit, state
                )
                for statement in AUTH_PREFERENCES_TABLE_STATEMENTS:
                    _execute(connection, statement, observer)
                _insert_applied_migration(
                    connection,
                    AUTH_PREFERENCES_MIGRATION,
                    app_commit,
                    state,
                )
                for statement in (
                    AUTH_NOTIFICATIONS_TABLE_STATEMENTS
                    + AUTH_NOTIFICATIONS_INDEX_STATEMENTS
                ):
                    _execute(connection, statement, observer)
                _insert_applied_migration(
                    connection,
                    AUTH_NOTIFICATIONS_MIGRATION,
                    app_commit,
                    state,
                )
                verify_auth_schema(connection)
                _verify_auth_ledger(connection)
                _require_integrity(connection, "auth-preferences-migration")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return migration_report(connection, "auth")
        finally:
            connection.close()


def _legacy_state(connection, kind):
    tables = _tables(connection) - {LEDGER_TABLE}
    if not tables:
        return "fresh"
    if kind == "auth":
        if tables == {"users"} and _columns(connection, "users") == AUTH_LEGACY_USER_COLUMNS:
            return "legacy"
    elif kind == "orders":
        if tables == {"orders_snapshot"} and _columns(connection, "orders_snapshot") == ORDERS_LEGACY_COLUMNS:
            return "legacy"
    return "current"


class DomainMigrationLock:
    def __init__(self, path, kind):
        self.path = Path(path).resolve().parent / ".{}-schema-migration.lock".format(kind)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def _execute(connection, statement, observer):
    if observer is not None:
        observer(" ".join(statement.split()))
    connection.execute(statement)


def _apply_auth(connection, state, observer):
    if state == "fresh":
        for statement in AUTH_TABLE_STATEMENTS + AUTH_INDEX_STATEMENTS:
            _execute(connection, statement, observer)
        return
    if state == "legacy":
        for statement in AUTH_UPGRADE_STATEMENTS:
            _execute(connection, statement, observer)
        for statement in AUTH_TABLE_STATEMENTS[1:] + AUTH_INDEX_STATEMENTS:
            _execute(connection, statement, observer)
        return
    verify_auth_schema(
        connection, include_preferences=False, include_notifications=False
    )


def _apply_orders(connection, state, observer):
    if state == "fresh":
        for statement in ORDERS_TABLE_STATEMENTS + ORDERS_INDEX_STATEMENTS:
            _execute(connection, statement, observer)
        return
    if state == "legacy":
        for statement in ORDERS_UPGRADE_STATEMENTS:
            _execute(connection, statement, observer)
        for statement in ORDERS_TABLE_STATEMENTS[1:] + ORDERS_INDEX_STATEMENTS:
            _execute(connection, statement, observer)
        return
    verify_orders_schema(connection)


def _apply_tasks(connection, state, observer):
    if state == "fresh":
        for statement in TASKS_TABLE_STATEMENTS + TASKS_INDEX_STATEMENTS:
            _execute(connection, statement, observer)
        return
    verify_tasks_schema(connection)


def apply_domain_migrations(database_path, kind, app_commit="", observer=None):
    if kind not in DOMAIN_MIGRATIONS:
        raise DomainMigrationError("unknown domain database: {}".format(kind))
    path = Path(database_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "auth":
        return _apply_auth_preferences_migration(
            path, app_commit, observer
        )
    if kind == "tasks":
        return _apply_tasks_v2_migrations(path, app_commit, observer)
    migration = DOMAIN_MIGRATIONS[kind]
    with DomainMigrationLock(path, kind):
        connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            tables = _tables(connection)
            if LEDGER_TABLE in tables:
                _verify_ledger(connection, migration)
                if kind == "auth":
                    verify_auth_schema(connection)
                elif kind == "orders":
                    verify_orders_schema(connection)
                else:
                    verify_tasks_schema(connection)
                _require_integrity(connection, kind)
                return migration_report(connection, kind)

            state = _legacy_state(connection, kind)
            if state == "current":
                # Prove the full schema before adding a baseline ledger.
                if kind == "auth":
                    verify_auth_schema(connection, require_ledger=False)
                elif kind == "orders":
                    verify_orders_schema(connection, require_ledger=False)
                else:
                    verify_tasks_schema(connection, require_ledger=False)

            connection.execute("BEGIN IMMEDIATE")
            try:
                _execute(connection, LEDGER_SQL, observer)
                connection.execute(
                    "INSERT INTO {} (migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                    "VALUES (?,?,?,'applying',NULL,?,?)".format(LEDGER_TABLE),
                    (migration["id"], migration["name"], migration["checksum"],
                     str(app_commit or "") or None,
                     json.dumps({"source_state": state, "transactional": True}, sort_keys=True)),
                )
                if kind == "auth":
                    _apply_auth(connection, state, observer)
                    verify_auth_schema(connection)
                elif kind == "orders":
                    _apply_orders(connection, state, observer)
                    verify_orders_schema(connection)
                else:
                    _apply_tasks(connection, state, observer)
                    verify_tasks_schema(connection)
                _require_integrity(connection, kind + "-migration")
                connection.execute(
                    "UPDATE {} SET state='applied', applied_at=?, app_commit=? "
                    "WHERE migration_id=? AND state='applying'".format(LEDGER_TABLE),
                    (_utc_now(), str(app_commit or "") or None, migration["id"]),
                )
                if connection.total_changes < 2:
                    raise DomainMigrationError("migration ledger state update failed")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return migration_report(connection, kind)
        finally:
            connection.close()


def _runtime_connection(database_path):
    path = Path(database_path).resolve()
    if not path.exists():
        raise MigrationRequiredError("migration required: database is missing: {}".format(path))
    try:
        connection = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
    except sqlite3.Error as error:
        raise MigrationRequiredError("migration required: cannot open {}: {}".format(path, error))
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def validate_auth_database(database_path):
    connection = _runtime_connection(database_path)
    try:
        _verify_auth_ledger(connection)
        verify_auth_schema(connection)
    finally:
        connection.close()
    return True


def validate_orders_database(database_path):
    connection = _runtime_connection(database_path)
    try:
        _verify_ledger(connection, ORDERS_MIGRATION)
        verify_orders_schema(connection)
    finally:
        connection.close()
    return True


def validate_tasks_database(database_path):
    connection = _runtime_connection(database_path)
    try:
        verify_tasks_schema(connection)
    finally:
        connection.close()
    return True


def _semantic_schema(connection, tables):
    return {
        table: {
            "columns": sorted(_columns(connection, table)),
            "indexes": sorted(_index_contract(connection, table).items()),
            "foreign_keys": sorted(
                tuple(str(value) for value in row[2:8])
                for row in connection.execute(
                    "PRAGMA foreign_key_list({})".format(table)
                ).fetchall()
            ),
        }
        for table in sorted(tables)
    }


def migration_report(connection, kind):
    migration = DOMAIN_MIGRATIONS[kind]
    tables = (
        AUTH_EXPECTED_COLUMNS if kind == "auth" else
        ORDERS_EXPECTED_COLUMNS if kind == "orders" else TASKS_EXPECTED_COLUMNS
    )
    payload = json.dumps(
        _semantic_schema(connection, tables), sort_keys=True, separators=(",", ":")
    )
    count_tables = (
        ("tasks",)
        if kind == "tasks"
        else sorted(AUTH_V1_EXPECTED_COLUMNS)
        if kind == "auth"
        else sorted(tables)
    )
    counts = {
        table: int(connection.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0])
        for table in count_tables
    }
    return {
        "kind": kind,
        "latest_migration": migration["id"],
        "checksum": migration["checksum"],
        "schema_fingerprint": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "business_counts": counts,
    }


def domain_snapshot(database_path, kind):
    connection = _runtime_connection(database_path)
    try:
        if kind == "auth":
            table_names = _tables(connection)
            if "user_navigation_preferences" not in table_names:
                _verify_auth_ledger(connection, require_latest=False)
                verify_auth_schema(
                    connection, include_preferences=False,
                    include_notifications=False,
                )
                payload = json.dumps(
                    _semantic_schema(connection, AUTH_V1_EXPECTED_COLUMNS),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                return {
                    "kind": "auth",
                    "latest_migration": AUTH_MIGRATION["id"],
                    "checksum": AUTH_MIGRATION["checksum"],
                    "schema_fingerprint": hashlib.sha256(
                        payload.encode("utf-8")
                    ).hexdigest(),
                    "business_counts": {
                        table: int(connection.execute(
                            "SELECT COUNT(*) FROM {}".format(table)
                        ).fetchone()[0])
                        for table in sorted(AUTH_V1_EXPECTED_COLUMNS)
                    },
                }
            if "notification_entities" not in table_names:
                _verify_auth_ledger(connection, require_latest=False)
                verify_auth_schema(
                    connection, include_notifications=False
                )
                payload = json.dumps(
                    _semantic_schema(connection, AUTH_V2_EXPECTED_COLUMNS),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                return {
                    "kind": "auth",
                    "latest_migration": AUTH_PREFERENCES_MIGRATION["id"],
                    "checksum": AUTH_PREFERENCES_MIGRATION["checksum"],
                    "schema_fingerprint": hashlib.sha256(
                        payload.encode("utf-8")
                    ).hexdigest(),
                    "business_counts": {
                        table: int(connection.execute(
                            "SELECT COUNT(*) FROM {}".format(table)
                        ).fetchone()[0])
                        for table in sorted(AUTH_V1_EXPECTED_COLUMNS)
                    },
                }
        if kind == "tasks" and _tables(connection) == {LEDGER_TABLE, "tasks"}:
            payload = json.dumps(
                _semantic_schema(connection, TASKS_V1_EXPECTED_COLUMNS),
                sort_keys=True, separators=(",", ":"),
            )
            return {
                "kind": "tasks", "latest_migration": TASKS_MIGRATION["id"],
                "checksum": TASKS_MIGRATION["checksum"],
                "schema_fingerprint": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "business_counts": {"tasks": int(connection.execute(
                    "SELECT COUNT(*) FROM tasks"
                ).fetchone()[0])},
            }
        return migration_report(connection, kind)
    finally:
        connection.close()
