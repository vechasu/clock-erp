"""Deploy-time schema migration for purchases.db (SQLite 3.7.17 compatible)."""

from __future__ import print_function

import hashlib
import sqlite3
from pathlib import Path


SCHEMA_VERSION = "2026-08-27-purchases-v2"
LEGACY_SCHEMA_VERSION = "2026-08-27-purchases-v1"
LEGACY_SCHEMA_CHECKSUM = "ff842f7c19ae173e5cce59df3737e8cd261ae869412711bff52287e6ad7e031b"

TABLES = (
    "CREATE TABLE purchase_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE purchase_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        product_id INTEGER,
        product_name TEXT,
        brand TEXT,
        model TEXT,
        article TEXT,
        product_url TEXT,
        image_url TEXT,
        description TEXT,
        quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
        target_price REAL CHECK(target_price IS NULL OR target_price >= 0),
        channel TEXT NOT NULL CHECK(channel IN
            ('whatsapp','telegram','email','call','website','personal','other')),
        requested_at TEXT NOT NULL,
        valid_until TEXT,
        customer_comment TEXT NOT NULL DEFAULT '',
        internal_note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new' CHECK(status IN
            ('new','review','planned','ordered','arrived','notified','sold','closed')),
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        updated_by INTEGER NOT NULL,
        request_key TEXT,
        CHECK(product_id IS NOT NULL OR length(trim(
            COALESCE(product_name,'') || COALESCE(brand,'') || COALESCE(model,'') ||
            COALESCE(article,'') || COALESCE(product_url,'') || COALESCE(description,'') ||
            COALESCE(customer_comment,'')
        )) > 0)
    )""",
    """CREATE TABLE purchase_request_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        old_status TEXT,
        new_status TEXT,
        comment TEXT NOT NULL DEFAULT '',
        actor_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(request_id) REFERENCES purchase_requests(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE purchase_plan_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grouping_key TEXT NOT NULL UNIQUE,
        product_id INTEGER,
        product_name TEXT NOT NULL DEFAULT '',
        brand TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        article TEXT NOT NULL DEFAULT '',
        actual_quantity INTEGER,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','removed','ordered')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by INTEGER NOT NULL,
        CHECK(actual_quantity IS NULL OR actual_quantity >= 0)
    )""",
    """CREATE TABLE purchase_plan_requests (
        plan_item_id INTEGER NOT NULL,
        request_id INTEGER NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        PRIMARY KEY(plan_item_id, request_id),
        FOREIGN KEY(plan_item_id) REFERENCES purchase_plan_items(id) ON DELETE RESTRICT,
        FOREIGN KEY(request_id) REFERENCES purchase_requests(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE supplier_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        internal_number TEXT NOT NULL UNIQUE,
        supplier_name TEXT NOT NULL,
        created_date TEXT NOT NULL,
        ordered_date TEXT,
        expected_date TEXT,
        currency TEXT NOT NULL DEFAULT 'RUB',
        comment TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN
            ('draft','ordered','partially_received','received','cancelled')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        updated_by INTEGER NOT NULL
    )""",
    """CREATE TABLE supplier_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        plan_item_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        received_quantity INTEGER NOT NULL DEFAULT 0 CHECK(received_quantity >= 0),
        purchase_price REAL NOT NULL DEFAULT 0 CHECK(purchase_price >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(order_id, plan_item_id),
        CHECK(received_quantity <= quantity),
        FOREIGN KEY(order_id) REFERENCES supplier_orders(id) ON DELETE RESTRICT,
        FOREIGN KEY(plan_item_id) REFERENCES purchase_plan_items(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE supplier_order_requests (
        order_item_id INTEGER NOT NULL,
        request_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(order_item_id, request_id),
        FOREIGN KEY(order_item_id) REFERENCES supplier_order_items(id) ON DELETE RESTRICT,
        FOREIGN KEY(request_id) REFERENCES purchase_requests(id) ON DELETE RESTRICT
    )""",
)

INDEXES = (
    "CREATE INDEX idx_purchase_requests_status_date ON purchase_requests(status,requested_at,id)",
    "CREATE INDEX idx_purchase_requests_customer ON purchase_requests(customer_id,requested_at,id)",
    "CREATE INDEX idx_purchase_requests_product ON purchase_requests(product_id,status)",
    "CREATE INDEX idx_purchase_requests_brand ON purchase_requests(brand,status)",
    "CREATE INDEX idx_purchase_requests_channel ON purchase_requests(channel,status)",
    "CREATE INDEX idx_purchase_requests_valid ON purchase_requests(valid_until,status)",
    "CREATE UNIQUE INDEX idx_purchase_requests_request_key ON purchase_requests(request_key)",
    "CREATE INDEX idx_purchase_history_request ON purchase_request_history(request_id,created_at,id)",
    "CREATE INDEX idx_purchase_plan_status ON purchase_plan_items(status,updated_at,id)",
    "CREATE INDEX idx_supplier_orders_status ON supplier_orders(status,created_at,id)",
    "CREATE INDEX idx_supplier_items_order ON supplier_order_items(order_id,id)",
    "CREATE INDEX idx_supplier_request_request ON supplier_order_requests(request_id,order_item_id)",
)

SCHEMA_CHECKSUM = hashlib.sha256(
    "\n".join((SCHEMA_VERSION,) + TABLES + INDEXES).encode("utf-8")
).hexdigest()


def _tables(connection):
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def verify_database(path):
    path = Path(path)
    if not path.exists():
        raise RuntimeError("migration required: purchases database is missing")
    connection = sqlite3.connect(str(path))
    try:
        expected = {"purchase_meta"} | {
            statement.split("CREATE TABLE ", 1)[1].split(" ", 1)[0]
            for statement in TABLES[1:]
        }
        if _tables(connection) != expected:
            raise RuntimeError("purchases schema contract mismatch")
        meta = dict(connection.execute("SELECT key,value FROM purchase_meta"))
        if meta.get("schema_version") != SCHEMA_VERSION or meta.get("schema_checksum") != SCHEMA_CHECKSUM:
            raise RuntimeError("purchases schema version mismatch")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("purchases foreign key check failed")
        if [row[0] for row in connection.execute("PRAGMA quick_check")] != ["ok"]:
            raise RuntimeError("purchases quick check failed")
    finally:
        connection.close()
    return True


def migrate_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        existing = _tables(connection)
        if not existing:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            for statement in TABLES + INDEXES:
                connection.execute(statement)
            connection.executemany(
                "INSERT INTO purchase_meta(key,value) VALUES(?,?)",
                (("schema_version", SCHEMA_VERSION), ("schema_checksum", SCHEMA_CHECKSUM)),
            )
            connection.commit()
        else:
            meta = dict(connection.execute("SELECT key,value FROM purchase_meta"))
            if (meta.get("schema_version"), meta.get("schema_checksum")) == (
                    SCHEMA_VERSION, SCHEMA_CHECKSUM):
                return verify_database(path)
            if (meta.get("schema_version"), meta.get("schema_checksum")) != (
                    LEGACY_SCHEMA_VERSION, LEGACY_SCHEMA_CHECKSUM):
                raise RuntimeError("purchases schema version mismatch")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("purchases foreign key check failed before migration")
            connection.execute("PRAGMA foreign_keys=OFF")
            # On modern SQLite this preserves child-table references to the final
            # purchase_requests name; SQLite 3.7 safely ignores the unknown pragma.
            connection.execute("PRAGMA legacy_alter_table=ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ALTER TABLE purchase_requests RENAME TO purchase_requests_v1")
            connection.execute(TABLES[1])
            columns = (
                "id,customer_id,product_id,product_name,brand,model,article,product_url,"
                "image_url,description,quantity,target_price,channel,requested_at,valid_until,"
                "customer_comment,internal_note,status,archived,created_at,updated_at,created_by,updated_by"
            )
            connection.execute(
                "INSERT INTO purchase_requests({0}) SELECT {0} FROM purchase_requests_v1".format(columns)
            )
            connection.execute("DROP TABLE purchase_requests_v1")
            for statement in INDEXES:
                if "purchase_requests" in statement:
                    connection.execute(statement)
            connection.execute(
                "UPDATE purchase_meta SET value=? WHERE key='schema_version'", (SCHEMA_VERSION,)
            )
            connection.execute(
                "UPDATE purchase_meta SET value=? WHERE key='schema_checksum'", (SCHEMA_CHECKSUM,)
            )
            connection.commit()
            connection.execute("PRAGMA legacy_alter_table=OFF")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return verify_database(path)
