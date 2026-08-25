"""Production-safe catalog schema migration tracking and verification.

The production runtime uses SQLite 3.7.17.  Keep this module compatible with
Python 3.6 and avoid SQL features introduced after that SQLite release.
"""

from __future__ import print_function

import ast
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.catalog_migration_steps import (
    apply_order_comment_constraints,
    apply_fresh_catalog_schema,
    migration_source_checksum,
)


LEDGER_TABLE = "erp_migration_ledger"
BASELINE_ID = "2026-08-24-production-schema-baseline-v1"
BASELINE_NAME = "Production schema baseline after SQLite 3.7 compatibility"
BASELINE_DEFINITION = "\n".join((
    BASELINE_ID,
    BASELINE_NAME,
    "ledger-v1",
    "catalog-schema-contract-v1",
    "legacy-initializer-rehearsed-before-worker-start",
))
BASELINE_CHECKSUM = hashlib.sha256(
    BASELINE_DEFINITION.encode("utf-8")
).hexdigest()
COMMENTS_MIGRATION_ID = "2026-08-26-order-comments-baseline-v1"
COMMENTS_MIGRATION_NAME = "Verified order comments schema baseline"
COMMENT_INDEX_STATEMENTS = (
    "CREATE UNIQUE INDEX idx_erp_order_comments_external "
    "ON erp_order_comments(external_system, external_id)",
    "CREATE INDEX idx_erp_order_comments_sync "
    "ON erp_order_comments(sync_status, next_retry_at, id)",
)
COMMENTS_MIGRATION_DEFINITION = "\n".join((
    COMMENTS_MIGRATION_ID,
    COMMENTS_MIGRATION_NAME,
    "order-comments-contract-v1",
    "sqlite-3.7-nullable-composite-unique-index",
    "columns:updated_at,external_system,external_id,external_updated_at,"
    "sync_status,sync_hash,source,sync_attempts,next_retry_at,last_sync_error",
) + COMMENT_INDEX_STATEMENTS)
COMMENTS_MIGRATION_CHECKSUM = hashlib.sha256(
    COMMENTS_MIGRATION_DEFINITION.encode("utf-8")
).hexdigest()
CATALOG_RUNTIME_BASELINE_ID = "2026-08-27-catalog-schema-baseline-v1"
CATALOG_RUNTIME_BASELINE_NAME = "Verified complete catalog schema baseline"
CATALOG_MANIFEST_CHECKSUM = hashlib.sha256(
    Path(__file__).resolve().with_name("catalog_schema_manifest.json").read_bytes()
).hexdigest()
CATALOG_RUNTIME_BASELINE_DEFINITION = "\n".join((
    CATALOG_RUNTIME_BASELINE_ID,
    CATALOG_RUNTIME_BASELINE_NAME,
    "complete-catalog-contract-v1",
    "python-3.6-sqlite-3.7.17",
    migration_source_checksum(),
    CATALOG_MANIFEST_CHECKSUM,
))
CATALOG_RUNTIME_BASELINE_CHECKSUM = hashlib.sha256(
    CATALOG_RUNTIME_BASELINE_DEFINITION.encode("utf-8")
).hexdigest()

MIGRATIONS = (
    {
        "id": BASELINE_ID,
        "name": BASELINE_NAME,
        "checksum": BASELINE_CHECKSUM,
        "transactional": False,
        "recovery": "restore verified database backup while service is stopped",
    },
    {
        "id": COMMENTS_MIGRATION_ID,
        "name": COMMENTS_MIGRATION_NAME,
        "checksum": COMMENTS_MIGRATION_CHECKSUM,
        "transactional": True,
        "recovery": "restore verified catalog database backup while service is stopped",
    },
    {
        "id": CATALOG_RUNTIME_BASELINE_ID,
        "name": CATALOG_RUNTIME_BASELINE_NAME,
        "checksum": CATALOG_RUNTIME_BASELINE_CHECKSUM,
        "transactional": True,
        "recovery": "restore verified catalog database backup while service is stopped",
    },
)

REQUIRED_TABLES = {
    "catalog_excel_products",
    "catalog_stock_movements",
    "erp_audit_events",
    "erp_brands",
    "erp_categories",
    "erp_inventory_items",
    "erp_inventory_sessions",
    "erp_order_comments",
    "erp_order_comment_sync_state",
    "erp_order_product_mappings",
    "erp_order_statuses",
    "erp_receipt_items",
    "erp_receipts",
    "erp_sale_items",
    "erp_sales",
    "erp_schema_migrations",
    LEDGER_TABLE,
}

REQUIRED_COMMENT_COLUMNS = {
    "id",
    "order_id",
    "text",
    "author_name",
    "created_at",
    "updated_at",
    "external_system",
    "external_id",
    "sync_status",
    "source",
    "sync_attempts",
    "next_retry_at",
    "last_sync_error",
}

EXPECTED_COMMENT_COLUMNS = (
    ("id", "INTEGER", 0, None, 1),
    ("order_id", "TEXT", 1, None, 0),
    ("text", "TEXT", 1, None, 0),
    ("author_name", "TEXT", 1, None, 0),
    ("author_user_id", "TEXT", 0, None, 0),
    ("created_at", "TEXT", 1, None, 0),
    ("updated_at", "TEXT", 0, None, 0),
    ("external_system", "TEXT", 0, None, 0),
    ("external_id", "TEXT", 0, None, 0),
    ("external_updated_at", "TEXT", 0, None, 0),
    ("sync_status", "TEXT", 1, "'not_applicable'", 0),
    ("sync_hash", "TEXT", 0, None, 0),
    ("source", "TEXT", 1, "'erp'", 0),
    ("sync_attempts", "INTEGER", 1, "0", 0),
    ("next_retry_at", "TEXT", 0, None, 0),
    ("last_sync_error", "TEXT", 0, None, 0),
)
LEGACY_COMMENT_COLUMNS = EXPECTED_COMMENT_COLUMNS[:6]
PARTIAL_COMMENT_COLUMNS = EXPECTED_COMMENT_COLUMNS[:9]
COMMENT_COLUMN_ADDITIONS = (
    ("updated_at", "TEXT"),
    ("external_system", "TEXT"),
    ("external_id", "TEXT"),
    ("external_updated_at", "TEXT"),
    ("sync_status", "TEXT NOT NULL DEFAULT 'not_applicable'"),
    ("sync_hash", "TEXT"),
    ("source", "TEXT NOT NULL DEFAULT 'erp'"),
    ("sync_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("next_retry_at", "TEXT"),
    ("last_sync_error", "TEXT"),
)

LEDGER_COLUMNS = {
    "migration_id",
    "name",
    "checksum",
    "state",
    "applied_at",
    "app_commit",
    "details_json",
}

BUSINESS_TABLES = (
    ("products", "catalog_excel_products", None),
    ("brands", "erp_brands", None),
    ("categories", "erp_categories", None),
    ("models", "erp_models", None),
    ("sales", "erp_sales", None),
    ("sale_items", "erp_sale_items", None),
    ("stock_movements", "catalog_stock_movements", None),
    ("order_statuses", "erp_order_statuses", None),
    ("order_mappings", "erp_order_product_mappings", None),
    ("receipts", "erp_receipts", None),
    ("receipt_items", "erp_receipt_items", None),
    ("comments", "erp_order_comments", None),
    ("audit_events", "erp_audit_events", None),
    ("inventory_documents", "erp_inventory_sessions", None),
    ("inventory_items", "erp_inventory_items", None),
    (
        "inventory_adjustments",
        "catalog_stock_movements",
        "movement_type = 'inventory_adjustment'",
    ),
    (
        "sale_idempotency_keys",
        "erp_sales",
        "idempotency_key IS NOT NULL",
    ),
    (
        "movement_idempotency_keys",
        "catalog_stock_movements",
        "idempotency_key IS NOT NULL",
    ),
    (
        "inventory_idempotency_keys",
        "erp_inventory_sessions",
        "idempotency_key IS NOT NULL",
    ),
    (
        "active_inventories",
        "erp_inventory_sessions",
        "status = 'active'",
    ),
    (
        "active_inventory_items",
        "erp_inventory_items",
        "session_id IN (SELECT id FROM erp_inventory_sessions "
        "WHERE status = 'active')",
    ),
)

LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS erp_migration_ledger (
    migration_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('applying', 'applied', 'failed')),
    applied_at TEXT,
    app_commit TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
)
"""


class MigrationError(RuntimeError):
    pass


class MigrationBusyError(MigrationError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _columns(connection, table):
    return {row[1] for row in connection.execute(
        "PRAGMA table_info({})".format(table)
    ).fetchall()}


def integrity_report(connection):
    quick_rows = connection.execute("PRAGMA quick_check").fetchall()
    quick = [str(row[0]) for row in quick_rows]
    foreign_keys = [tuple(row) for row in connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()]
    return {
        "quick_check": quick,
        "foreign_key_violations": len(foreign_keys),
    }


def require_integrity(connection, stage):
    report = integrity_report(connection)
    if report["quick_check"] != ["ok"]:
        raise MigrationError(
            "{}: PRAGMA quick_check failed: {}".format(
                stage, ", ".join(report["quick_check"])
            )
        )
    if report["foreign_key_violations"]:
        raise MigrationError(
            "{}: PRAGMA foreign_key_check found {} violation(s)".format(
                stage, report["foreign_key_violations"]
            )
        )
    return report


def business_snapshot(connection):
    tables = _table_names(connection)
    result = {}
    for label, table, condition in BUSINESS_TABLES:
        if table not in tables:
            result[label] = None
            continue
        statement = "SELECT COUNT(*) FROM {}".format(table)
        if condition:
            statement += " WHERE " + condition
        result[label] = int(connection.execute(statement).fetchone()[0])
    if "catalog_excel_products" in tables:
        value = connection.execute(
            "SELECT COALESCE(SUM(stock), 0) FROM catalog_excel_products "
            "WHERE active = 1"
        ).fetchone()[0]
        result["active_stock_sum"] = float(value or 0)
    else:
        result["active_stock_sum"] = None
    if "catalog_stock_movements" in tables:
        result["movement_quantity_sum"] = float(connection.execute(
            "SELECT COALESCE(SUM(quantity_delta), 0) "
            "FROM catalog_stock_movements"
        ).fetchone()[0] or 0)
    else:
        result["movement_quantity_sum"] = None
    return result


def _normalized_default(value):
    return None if value is None else " ".join(str(value).split())


def _check_constraints(table_sql):
    """Extract CHECK expressions independent of whitespace and column order."""
    sql = str(table_sql or "")
    result = []
    position = 0
    while True:
        match = re.search(r"\bCHECK\s*\(", sql[position:], re.IGNORECASE)
        if match is None:
            break
        opening = position + match.end() - 1
        depth = 1
        cursor = opening + 1
        quote = None
        while cursor < len(sql) and depth:
            character = sql[cursor]
            if quote:
                if character == quote:
                    if cursor + 1 < len(sql) and sql[cursor + 1] == quote:
                        cursor += 1
                    else:
                        quote = None
            elif character in ("'", '"'):
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            cursor += 1
        if depth:
            break
        expression = sql[opening + 1:cursor - 1]
        result.append("".join(expression.lower().split()))
        position = cursor
    return sorted(result)


def schema_structure(connection):
    tables = sorted(
        name for name in _table_names(connection)
        if not name.startswith("sqlite_")
    )
    structure = {"tables": {}, "indexes": [], "triggers": [], "views": []}
    for table in tables:
        columns = []
        for row in connection.execute(
            "PRAGMA table_info({})".format(table)
        ).fetchall():
            columns.append((
                str(row[1]),
                str(row[2] or "").upper(),
                int(row[3]),
                _normalized_default(row[4]),
                int(row[5]),
            ))
        columns.sort(key=lambda item: item[0])
        foreign_keys = []
        for row in connection.execute(
            "PRAGMA foreign_key_list({})".format(table)
        ).fetchall():
            foreign_keys.append(tuple(str(value) for value in row[2:8]))
        table_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        structure["tables"][table] = {
            "columns": columns,
            "foreign_keys": sorted(foreign_keys),
            "checks": _check_constraints(table_row[0]),
        }
        for row in connection.execute(
            "PRAGMA index_list({})".format(table)
        ).fetchall():
            index_name = str(row[1])
            unique = int(row[2])
            index_columns = tuple(
                str(index_row[2])
                for index_row in connection.execute(
                    "PRAGMA index_info('{}')".format(
                        index_name.replace("'", "''")
                    )
                ).fetchall()
            )
            stable_name = index_name
            structure["indexes"].append(
                (table, stable_name, unique, index_columns)
            )
    structure["indexes"].sort()
    for row in connection.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type = 'trigger' ORDER BY name"
    ).fetchall():
        structure["triggers"].append((
            str(row[0]), str(row[1]), " ".join(str(row[2] or "").split())
        ))
    for row in connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'view' ORDER BY name"
    ).fetchall():
        structure["views"].append((
            str(row[0]), " ".join(str(row[1] or "").split())
        ))
    return structure


def schema_fingerprint(connection):
    payload = json.dumps(
        schema_structure(connection),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def schema_source_checksum():
    return migration_source_checksum()


def _manifest_path():
    return Path(__file__).resolve().with_name("catalog_schema_manifest.json")


def expected_catalog_manifest():
    try:
        return json.loads(_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MigrationError("catalog schema manifest is unavailable: {}".format(error))


def _json_structure(connection):
    return json.loads(json.dumps(
        schema_structure(connection), ensure_ascii=True, sort_keys=True
    ))


def verify_complete_catalog_contract(connection):
    expected = expected_catalog_manifest()
    actual = _json_structure(connection)
    if actual == expected:
        return True
    expected_tables = set(expected.get("tables", {}))
    actual_tables = set(actual.get("tables", {}))
    if expected_tables != actual_tables:
        raise MigrationError(
            "catalog schema mismatch: tables added={} missing={}".format(
                sorted(actual_tables - expected_tables),
                sorted(expected_tables - actual_tables),
            )
        )
    for table in sorted(expected_tables):
        if actual["tables"][table] != expected["tables"][table]:
            raise MigrationError(
                "catalog schema mismatch: table contract differs: {}".format(table)
            )
    for object_type in ("indexes", "triggers", "views"):
        if actual.get(object_type) != expected.get(object_type):
            raise MigrationError(
                "catalog schema mismatch: {} differ".format(object_type)
            )
    raise MigrationError("catalog schema mismatch: unknown manifest difference")


def verify_schema_contract(
    connection,
    require_comment_indexes=True,
    allow_legacy_comments=False,
):
    tables = _table_names(connection)
    missing_tables = sorted(REQUIRED_TABLES - tables)
    if missing_tables:
        raise MigrationError(
            "schema contract: missing tables: {}".format(
                ", ".join(missing_tables)
            )
        )
    actual_comment_columns = tuple(
        (str(row[1]), str(row[2] or "").upper(), int(row[3]), row[4], int(row[5]))
        for row in connection.execute("PRAGMA table_info(erp_order_comments)").fetchall()
    )
    known_legacy = actual_comment_columns in (
        LEGACY_COMMENT_COLUMNS,
        PARTIAL_COMMENT_COLUMNS,
    )
    if (
        sorted(actual_comment_columns) != sorted(EXPECTED_COMMENT_COLUMNS)
        and not (allow_legacy_comments and known_legacy)
    ):
        raise MigrationError(
            "schema contract: erp_order_comments column semantics differ"
        )
    missing_ledger = sorted(
        LEDGER_COLUMNS - _columns(connection, LEDGER_TABLE)
    )
    if missing_ledger:
        raise MigrationError(
            "schema contract: migration ledger missing columns: {}".format(
                ", ".join(missing_ledger)
            )
        )
    if not require_comment_indexes or known_legacy:
        return True
    index = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        ("idx_erp_order_comments_external",),
    ).fetchone()
    if index is None:
        raise MigrationError(
            "schema contract: idx_erp_order_comments_external is missing"
        )
    index_sql = " ".join(str(index[0] or "").upper().split())
    if " WHERE " in index_sql:
        raise MigrationError(
            "schema contract: idx_erp_order_comments_external is a partial index"
        )
    index_rows = connection.execute(
        "PRAGMA index_info(idx_erp_order_comments_external)"
    ).fetchall()
    if [row[2] for row in index_rows] != ["external_system", "external_id"]:
        raise MigrationError(
            "schema contract: external comment index has unexpected columns"
        )
    unique = None
    for row in connection.execute(
        "PRAGMA index_list(erp_order_comments)"
    ).fetchall():
        if row[1] == "idx_erp_order_comments_external":
            unique = int(row[2])
            break
    if unique != 1:
        raise MigrationError(
            "schema contract: external comment index is not unique"
        )
    return True


def apply_order_comments_migration(connection, ddl_observer=None):
    """Verify the production comment schema; never guess a partial state."""
    tables = _table_names(connection)
    if "erp_order_comments" not in tables or "erp_order_comment_sync_state" not in tables:
        raise MigrationError("order comments migration: required tables are missing")
    actual = tuple(
        (str(row[1]), str(row[2] or "").upper(), int(row[3]), row[4], int(row[5]))
        for row in connection.execute("PRAGMA table_info(erp_order_comments)").fetchall()
    )
    if actual not in (
        EXPECTED_COMMENT_COLUMNS,
        LEGACY_COMMENT_COLUMNS,
        PARTIAL_COMMENT_COLUMNS,
    ):
        raise MigrationError(
            "order comments migration: unknown or partial column state"
        )
    existing_columns = {column[0] for column in actual}
    duplicate = connection.execute(
        "SELECT external_system, external_id FROM erp_order_comments "
        "WHERE external_system IS NOT NULL AND external_id IS NOT NULL "
        "GROUP BY external_system, external_id HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone() if {"external_system", "external_id"}.issubset(existing_columns) else None
    if duplicate:
        raise MigrationError("order comments migration: duplicate external identity")
    if actual != EXPECTED_COMMENT_COLUMNS:
        for name, declaration in COMMENT_COLUMN_ADDITIONS:
            if name not in existing_columns:
                statement = "ALTER TABLE erp_order_comments ADD COLUMN {} {}".format(
                    name, declaration
                )
                if ddl_observer is not None:
                    ddl_observer(" ".join(statement.split()))
                connection.execute(statement)
        connection.execute(
            "UPDATE erp_order_comments SET updated_at = created_at "
            "WHERE updated_at IS NULL OR trim(updated_at) = ''"
        )
    apply_order_comment_constraints(connection)
    index_names = {
        str(row[1]) for row in connection.execute(
            "PRAGMA index_list(erp_order_comments)"
        ).fetchall()
    }
    migration_indexes = {
        "idx_erp_order_comments_external",
        "idx_erp_order_comments_sync",
    }
    present = migration_indexes & index_names
    if present and present != migration_indexes:
        raise MigrationError(
            "order comments migration: partial migration index state"
        )
    if not present:
        for statement in COMMENT_INDEX_STATEMENTS:
            if ddl_observer is not None:
                ddl_observer(" ".join(statement.split()))
            connection.execute(statement)
    verify_schema_contract(connection)
    return True


def _migration_by_id(migration_id):
    for migration in MIGRATIONS:
        if migration["id"] == migration_id:
            return migration
    return None


def verify_ledger(connection):
    if LEDGER_TABLE not in _table_names(connection):
        raise MigrationError("migration ledger is missing")
    rows = connection.execute(
        "SELECT migration_id, name, checksum, state FROM " + LEDGER_TABLE
    ).fetchall()
    seen = set()
    for row in rows:
        migration_id = str(row[0])
        migration = _migration_by_id(migration_id)
        if migration is None:
            raise MigrationError(
                "unknown migration in ledger: {}".format(migration_id)
            )
        if str(row[2]) != migration["checksum"]:
            raise MigrationError(
                "migration checksum mismatch: {}".format(migration_id)
            )
        if str(row[3]) != "applied":
            raise MigrationError(
                "migration is not fully applied: {} state={}".format(
                    migration_id, row[3]
                )
            )
        seen.add(migration_id)
    missing = [m["id"] for m in MIGRATIONS if m["id"] not in seen]
    if missing:
        raise MigrationError(
            "migration ledger is incomplete: {}".format(", ".join(missing))
        )
    return True


def _migration_lock_path(database_path):
    path = Path(database_path).resolve()
    return path.parent / ".catalog-migration.lock"


class MigrationLock:
    def __init__(self, database_path):
        self.path = _migration_lock_path(database_path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            self.handle.close()
            self.handle = None
            raise MigrationBusyError(
                "another migration runner holds {}".format(self.path)
            )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def apply_migrations(database_path, app_commit="", ddl_observer=None):
    path = Path(database_path).resolve()
    with MigrationLock(path):
        connection = sqlite3.connect(str(path))
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(LEDGER_SQL)
            connection.commit()
            known_ids = {migration["id"] for migration in MIGRATIONS}
            rows = connection.execute(
                "SELECT migration_id, checksum, state FROM " + LEDGER_TABLE
            ).fetchall()
            for migration_id, checksum, state in rows:
                if migration_id not in known_ids:
                    raise MigrationError(
                        "unknown migration in ledger: {}".format(migration_id)
                    )
                migration = _migration_by_id(migration_id)
                if checksum != migration["checksum"]:
                    raise MigrationError(
                        "migration checksum mismatch: {}".format(migration_id)
                    )
                if state != "applied":
                    raise MigrationError(
                        "partially applied migration detected: {} state={}".format(
                            migration_id, state
                        )
                    )
        finally:
            connection.close()

        for migration in MIGRATIONS:
            connection = sqlite3.connect(str(path))
            try:
                row = connection.execute(
                    "SELECT checksum, state FROM {} WHERE migration_id = ?".format(
                        LEDGER_TABLE
                    ),
                    (migration["id"],),
                ).fetchone()
                if row is not None:
                    continue
                connection.execute(
                    "INSERT INTO {} (migration_id, name, checksum, state, "
                    "applied_at, app_commit, details_json) "
                    "VALUES (?, ?, ?, 'applying', NULL, ?, ?)".format(
                        LEDGER_TABLE
                    ),
                    (
                        migration["id"],
                        migration["name"],
                        migration["checksum"],
                        str(app_commit or "") or None,
                        json.dumps(
                            {
                                "transactional": migration["transactional"],
                                "recovery": migration["recovery"],
                            },
                            sort_keys=True,
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            try:
                if migration["id"] == BASELINE_ID:
                    connection = sqlite3.connect(str(path))
                    connection.row_factory = sqlite3.Row
                    try:
                        connection.execute("PRAGMA foreign_keys = ON")
                        apply_fresh_catalog_schema(connection, ddl_observer)
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                    finally:
                        connection.close()
                elif migration["id"] == COMMENTS_MIGRATION_ID:
                    connection = sqlite3.connect(str(path))
                    try:
                        connection.execute("PRAGMA foreign_keys = ON")
                        connection.execute("BEGIN IMMEDIATE")
                        apply_order_comments_migration(connection, ddl_observer)
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                    finally:
                        connection.close()
                elif migration["id"] == CATALOG_RUNTIME_BASELINE_ID:
                    connection = sqlite3.connect(str(path))
                    try:
                        verify_complete_catalog_contract(connection)
                        require_integrity(connection, migration["id"])
                    finally:
                        connection.close()
                else:
                    raise MigrationError(
                        "no migration operation registered: {}".format(
                            migration["id"]
                        )
                    )
                connection = sqlite3.connect(str(path))
                try:
                    if migration["id"] == CATALOG_RUNTIME_BASELINE_ID:
                        verify_complete_catalog_contract(connection)
                    else:
                        verify_schema_contract(
                            connection,
                            require_comment_indexes=migration["id"] != BASELINE_ID,
                            allow_legacy_comments=migration["id"] == BASELINE_ID,
                        )
                    require_integrity(connection, migration["id"])
                    connection.execute(
                        "UPDATE {} SET state = 'applied', applied_at = ?, "
                        "app_commit = ? WHERE migration_id = ? "
                        "AND state = 'applying'".format(LEDGER_TABLE),
                        (utc_now(), str(app_commit or "") or None, migration["id"]),
                    )
                    if connection.total_changes != 1:
                        raise MigrationError(
                            "migration state changed concurrently: {}".format(
                                migration["id"]
                            )
                        )
                    connection.commit()
                finally:
                    connection.close()
            except Exception:
                connection = sqlite3.connect(str(path))
                try:
                    connection.execute(
                        "UPDATE {} SET state = 'failed' "
                        "WHERE migration_id = ? AND state = 'applying'".format(
                            LEDGER_TABLE
                        ),
                        (migration["id"],),
                    )
                    connection.commit()
                finally:
                    connection.close()
                raise

        connection = sqlite3.connect(str(path))
        try:
            verify_ledger(connection)
            verify_schema_contract(connection)
            verify_complete_catalog_contract(connection)
            integrity = require_integrity(connection, "migration-complete")
            return {
                "latest_migration": MIGRATIONS[-1]["id"],
                "schema_fingerprint": schema_fingerprint(connection),
                "schema_source_checksum": schema_source_checksum(),
                "business": business_snapshot(connection),
                "integrity": integrity,
            }
        finally:
            connection.close()


def guard_paths(database_path):
    parent = Path(database_path).resolve().parent
    return (
        parent / ".catalog-schema-preflight-required",
        parent / ".catalog-schema-state.json",
    )


def runtime_guard_required(database_path):
    """Deprecated diagnostic helper; runtime readiness never depends on it."""
    return False


def validate_catalog_runtime(database_path):
    """Read-only, fail-closed catalog readiness validation for workers."""
    path = Path(database_path).resolve()
    if not path.is_file():
        raise MigrationError(
            "catalog migration required: database is missing: {}".format(path)
        )
    try:
        connection = sqlite3.connect(
            "file:{}?mode=ro".format(path), uri=True
        )
    except sqlite3.Error as error:
        raise MigrationError(
            "catalog migration required: cannot open database: {}".format(error)
        )
    try:
        verify_ledger(connection)
        verify_schema_contract(connection)
        verify_complete_catalog_contract(connection)
    except sqlite3.Error as error:
        raise MigrationError(
            "catalog schema mismatch: {}".format(error)
        )
    finally:
        connection.close()
    return True


def write_runtime_guard(database_path, app_commit):
    path = Path(database_path).resolve()
    connection = sqlite3.connect(str(path))
    try:
        verify_ledger(connection)
        verify_schema_contract(connection)
        require_integrity(connection, "runtime-guard")
        state = {
            "app_commit": str(app_commit or ""),
            "latest_migration": MIGRATIONS[-1]["id"],
            "migration_checksum": MIGRATIONS[-1]["checksum"],
            "schema_fingerprint": schema_fingerprint(connection),
            "schema_source_checksum": schema_source_checksum(),
            "sqlite_version": sqlite3.sqlite_version,
            "written_at": utc_now(),
        }
    finally:
        connection.close()
    sentinel, marker = guard_paths(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".catalog-schema-state-",
        suffix=".tmp",
        dir=str(marker.parent),
    )
    try:
        with os.fdopen(file_descriptor, "w") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, str(marker))
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    sentinel_descriptor, sentinel_temporary = tempfile.mkstemp(
        prefix=".catalog-schema-preflight-required-",
        suffix=".tmp",
        dir=str(sentinel.parent),
    )
    try:
        with os.fdopen(sentinel_descriptor, "w") as handle:
            handle.write(MIGRATIONS[-1]["id"] + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(sentinel_temporary, 0o600)
        os.replace(sentinel_temporary, str(sentinel))
    except Exception:
        try:
            os.unlink(sentinel_temporary)
        except OSError:
            pass
        raise
    return state


def verify_runtime_guard(database_path):
    path = Path(database_path).resolve()
    sentinel, marker = guard_paths(path)
    if not sentinel.exists():
        return False
    if not marker.exists():
        raise MigrationError("schema preflight marker is missing")
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MigrationError("schema preflight marker is invalid: {}".format(error))
    expected = {
        "latest_migration": MIGRATIONS[-1]["id"],
        "migration_checksum": MIGRATIONS[-1]["checksum"],
        "schema_source_checksum": schema_source_checksum(),
    }
    for key, value in expected.items():
        if state.get(key) != value:
            raise MigrationError(
                "schema preflight marker mismatch: {}".format(key)
            )
    connection = sqlite3.connect(
        "file:{}?mode=ro".format(path), uri=True
    )
    try:
        verify_ledger(connection)
        verify_schema_contract(connection)
        fingerprint = schema_fingerprint(connection)
    finally:
        connection.close()
    if fingerprint != state.get("schema_fingerprint"):
        raise MigrationError("production schema changed after migration preflight")
    return True


def sqlite_backup(source, target, sqlite_binary="sqlite3"):
    source = Path(source).resolve()
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(target).replace("'", "''")
    completed = subprocess.run(
        [sqlite_binary, str(source), ".backup '{}'".format(escaped)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if completed.returncode != 0:
        raise MigrationError(
            "SQLite backup failed: {}".format(
                completed.stderr.strip() or "unknown sqlite3 error"
            )
        )
    os.chmod(str(target), 0o600)
    return target


def validate_known_sql_compatibility(source_root):
    source_root = Path(source_root)
    catalog_steps = source_root / "app" / "catalog_migration_steps.py"
    legacy_catalog = source_root / "app" / "catalog_db.py"
    paths = [catalog_steps if catalog_steps.exists() else legacy_catalog]
    domain_migrations = source_root / "app" / "domain_schema_migrations.py"
    if domain_migrations.exists():
        paths.append(domain_migrations)
    paths.extend(sorted((source_root / "scripts").glob("migrate_*.py")))
    forbidden = (
        (re.compile(r"\bON\s+CONFLICT\b", re.I), "modern UPSERT"),
        (re.compile(r"\bRETURNING\b", re.I), "RETURNING"),
        (re.compile(r"\bWITHOUT\s+ROWID\b", re.I), "WITHOUT ROWID"),
        (re.compile(r"\bGENERATED\s+ALWAYS\b", re.I), "generated column"),
        (re.compile(r"\bDROP\s+COLUMN\b", re.I), "ALTER TABLE DROP COLUMN"),
        (re.compile(r"\bRENAME\s+COLUMN\b", re.I), "ALTER TABLE RENAME COLUMN"),
        (re.compile(r"\bOVER\s*\(", re.I), "window function"),
        (re.compile(r"\bCREATE\s+TABLE\b[^;]*\bSTRICT\b", re.I | re.S), "STRICT table"),
        (
            re.compile(
                r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\b[^;]*\bWHERE\b",
                re.I | re.S,
            ),
            "partial index",
        ),
    )
    violations = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            value = None
            if isinstance(node, ast.Str):
                value = node.s
            elif hasattr(ast, "Constant") and isinstance(node, ast.Constant):
                value = node.value if isinstance(node.value, str) else None
            if not value:
                continue
            for pattern, label in forbidden:
                if pattern.search(value):
                    violations.append(
                        "{}:{}: {}".format(path, getattr(node, "lineno", 0), label)
                    )
    if violations:
        raise MigrationError(
            "SQL incompatible with production SQLite 3.7.17:\n{}".format(
                "\n".join(violations)
            )
        )
    return [str(path) for path in paths]
