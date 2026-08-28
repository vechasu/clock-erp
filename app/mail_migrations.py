"""Additive schema for the ERP shared mailbox (SQLite 3.7.17 compatible)."""

from __future__ import print_function

import os
import sqlite3
from pathlib import Path


SCHEMA_VERSION = "mail-v1-2026-08-28"

TABLES = (
    """CREATE TABLE mail_schema_meta (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""",
    """CREATE TABLE mail_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mailbox_name TEXT NOT NULL, sender_name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL, email_normalized TEXT NOT NULL,
        imap_host TEXT NOT NULL, imap_port INTEGER NOT NULL,
        smtp_host TEXT NOT NULL, smtp_port INTEGER NOT NULL,
        security TEXT NOT NULL CHECK (security IN ('ssl','starttls')),
        login TEXT NOT NULL, encrypted_password TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1, created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        last_sync_at TEXT, last_sync_status TEXT NOT NULL DEFAULT 'never',
        last_sync_error TEXT NOT NULL DEFAULT '', initial_sync_complete INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE mail_sync_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
        folder_role TEXT NOT NULL CHECK (folder_role IN ('inbox','sent')),
        folder_name TEXT NOT NULL, uidvalidity TEXT NOT NULL DEFAULT '',
        last_uid INTEGER NOT NULL DEFAULT 0, sync_cursor TEXT NOT NULL DEFAULT '',
        lock_token TEXT NOT NULL DEFAULT '', lock_expires_at TEXT,
        updated_at TEXT NOT NULL, UNIQUE(account_id, folder_role),
        FOREIGN KEY(account_id) REFERENCES mail_accounts(id)
    )""",
    """CREATE TABLE mail_threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
        subject TEXT NOT NULL DEFAULT '', subject_fold TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','in_progress','waiting_customer','answered','closed')),
        assignee_id INTEGER, customer_id INTEGER, due_at TEXT,
        archived INTEGER NOT NULL DEFAULT 0, unread_count INTEGER NOT NULL DEFAULT 0,
        message_count INTEGER NOT NULL DEFAULT 0, has_attachments INTEGER NOT NULL DEFAULT 0,
        last_message_at TEXT NOT NULL, last_snippet TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(account_id) REFERENCES mail_accounts(id)
    )""",
    """CREATE TABLE mail_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
        thread_id INTEGER NOT NULL, folder_role TEXT NOT NULL CHECK (folder_role IN ('inbox','sent','draft')),
        remote_folder TEXT NOT NULL DEFAULT '', remote_uid INTEGER,
        uidvalidity TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL,
        in_reply_to TEXT NOT NULL DEFAULT '', references_json TEXT NOT NULL DEFAULT '[]',
        subject TEXT NOT NULL DEFAULT '', sender_name TEXT NOT NULL DEFAULT '', sender_email TEXT NOT NULL DEFAULT '',
        sent_at TEXT NOT NULL, received_at TEXT NOT NULL, text_body TEXT NOT NULL DEFAULT '',
        html_body TEXT NOT NULL DEFAULT '', snippet TEXT NOT NULL DEFAULT '',
        is_read INTEGER NOT NULL DEFAULT 0, external_images INTEGER NOT NULL DEFAULT 0,
        erp_author_id INTEGER, delivery_status TEXT NOT NULL DEFAULT 'received',
        raw_headers_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
        UNIQUE(account_id, message_id),
        UNIQUE(account_id, remote_folder, uidvalidity, remote_uid),
        FOREIGN KEY(account_id) REFERENCES mail_accounts(id),
        FOREIGN KEY(thread_id) REFERENCES mail_threads(id)
    )""",
    """CREATE TABLE mail_recipients (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('from','to','cc','bcc')),
        display_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL, email_normalized TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(message_id) REFERENCES mail_messages(id)
    )""",
    """CREATE TABLE mail_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL,
        storage_key TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL,
        content_type TEXT NOT NULL DEFAULT 'application/octet-stream', size_bytes INTEGER NOT NULL,
        content_id TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(message_id) REFERENCES mail_messages(id)
    )""",
    """CREATE TABLE mail_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
        thread_id INTEGER, idempotency_key TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK (state IN ('draft','queued','sending','sent','failed','unknown')),
        to_json TEXT NOT NULL, cc_json TEXT NOT NULL DEFAULT '[]', bcc_json TEXT NOT NULL DEFAULT '[]',
        subject TEXT NOT NULL DEFAULT '', text_body TEXT NOT NULL DEFAULT '',
        in_reply_to TEXT NOT NULL DEFAULT '', references_json TEXT NOT NULL DEFAULT '[]',
        author_id INTEGER NOT NULL, error_code TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sent_at TEXT,
        FOREIGN KEY(account_id) REFERENCES mail_accounts(id),
        FOREIGN KEY(thread_id) REFERENCES mail_threads(id)
    )""",
    """CREATE TABLE mail_outbox_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, outbox_id INTEGER NOT NULL,
        storage_key TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL,
        content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
        size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(outbox_id) REFERENCES mail_outbox(id)
    )""",
    """CREATE TABLE mail_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INTEGER NOT NULL,
        entity_type TEXT NOT NULL CHECK (entity_type IN ('customer','order','repair','purchase','task')),
        entity_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(thread_id, entity_type, entity_id),
        FOREIGN KEY(thread_id) REFERENCES mail_threads(id)
    )""",
    """CREATE TABLE mail_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INTEGER,
        account_id INTEGER NOT NULL, actor_id INTEGER, action TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
        FOREIGN KEY(thread_id) REFERENCES mail_threads(id),
        FOREIGN KEY(account_id) REFERENCES mail_accounts(id)
    )""",
    """CREATE TABLE mail_sync_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
        requested_by INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        requested_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
        FOREIGN KEY(account_id) REFERENCES mail_accounts(id)
    )""",
)

INDEXES = (
    "CREATE INDEX idx_mail_threads_list ON mail_threads(account_id,archived,last_message_at,id)",
    "CREATE INDEX idx_mail_threads_status ON mail_threads(account_id,status,last_message_at,id)",
    "CREATE INDEX idx_mail_threads_assignee ON mail_threads(account_id,assignee_id,last_message_at,id)",
    "CREATE INDEX idx_mail_messages_thread ON mail_messages(thread_id,sent_at,id)",
    "CREATE INDEX idx_mail_messages_remote ON mail_messages(account_id,remote_folder,remote_uid)",
    "CREATE INDEX idx_mail_recipients_email ON mail_recipients(email_normalized,message_id)",
    "CREATE INDEX idx_mail_links_entity ON mail_links(entity_type,entity_id,thread_id)",
    "CREATE INDEX idx_mail_outbox_state ON mail_outbox(state,created_at,id)",
    "CREATE INDEX idx_mail_outbox_attachments ON mail_outbox_attachments(outbox_id,id)",
    "CREATE INDEX idx_mail_sync_requests ON mail_sync_requests(state,requested_at,id)",
)


class MailMigrationRequiredError(RuntimeError):
    pass


def migrate_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    os.chmod(str(path), 0o600)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        existing = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        for statement in TABLES:
            name = statement.split("CREATE TABLE ", 1)[1].split(" ", 1)[0]
            if name not in existing:
                connection.execute(statement)
        for statement in INDEXES:
            name = statement.split("CREATE INDEX ", 1)[1].split(" ", 1)[0]
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
            ).fetchone()
            if not exists:
                connection.execute(statement)
        connection.execute(
            "INSERT OR REPLACE INTO mail_schema_meta(key,value) VALUES('schema_version',?)",
            (SCHEMA_VERSION,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    validate_database(path)


def validate_database(path):
    path = Path(path)
    if not path.is_file():
        raise MailMigrationRequiredError("Mail database is not initialized")
    connection = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
    try:
        row = connection.execute(
            "SELECT value FROM mail_schema_meta WHERE key='schema_version'"
        ).fetchone()
        if not row or row[0] != SCHEMA_VERSION:
            raise MailMigrationRequiredError("Mail database migration is required")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise MailMigrationRequiredError("Mail database integrity check failed")
    except sqlite3.Error as error:
        raise MailMigrationRequiredError(str(error))
    finally:
        connection.close()
    return SCHEMA_VERSION
