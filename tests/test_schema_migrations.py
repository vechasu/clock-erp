import hashlib
import json
import socket
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.catalog_migration_steps import (
    apply_audit_identity_constraints,
    apply_fresh_catalog_schema,
)
from app.schema_migrations import (
    BASELINE_CHECKSUM,
    BASELINE_ID,
    LEDGER_SQL,
    LEDGER_TABLE,
    MIGRATIONS,
    MigrationBusyError,
    MigrationError,
    MigrationLock,
    apply_migrations,
    require_integrity,
    schema_fingerprint,
    validate_known_sql_compatibility,
    verify_runtime_guard,
    write_runtime_guard,
)
from scripts import migration_preflight


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SchemaMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "catalog.db"

    def tearDown(self):
        CatalogDatabase._schema_cache.clear()
        self.temporary.cleanup()

    def initialize(self, path=None):
        path = path or self.database_path
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        try:
            apply_fresh_catalog_schema(connection)
            connection.commit()
        finally:
            connection.close()
        return path

    def preflight_arguments(self, database=None, report=None):
        return Namespace(
            database=str(database or self.database_path),
            source_root=str(PROJECT_ROOT),
            app_commit="test-commit",
            expected_sqlite_version=sqlite3.sqlite_version,
            sqlite_binary="sqlite3",
            rehearsal_root=str(self.root / "rehearsals"),
            retention_days=7,
            report=str(report) if report else None,
            service_stopped=False,
        )

    def test_fresh_database_gets_current_schema_and_ledger(self):
        result = apply_migrations(self.database_path, app_commit="fresh")
        with sqlite3.connect(str(self.database_path)) as connection:
            row = connection.execute(
                "SELECT name, checksum, state, app_commit FROM {} "
                "WHERE migration_id=?".format(LEDGER_TABLE),
                (BASELINE_ID,),
            ).fetchone()
            self.assertEqual(connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0], "ok")
        self.assertEqual(row[1], BASELINE_CHECKSUM)
        self.assertEqual(row[2:], ("applied", "fresh"))
        self.assertEqual(result["latest_migration"], MIGRATIONS[-1]["id"])

    def test_previous_untracked_schema_upgrades_without_losing_data(self):
        self.initialize()
        with sqlite3.connect(str(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO erp_order_comments "
                "(order_id,text,author_name,created_at) VALUES (?,?,?,?)",
                ("legacy-order", "keep", "employee", "2026-08-24T10:00:00Z"),
            )
        before_hash = self._comment_hash()
        apply_migrations(self.database_path, app_commit="upgrade")
        self.assertEqual(self._comment_hash(), before_hash)

    def test_audit_constraint_upgrade_does_not_confuse_actor_user_value(self):
        connection = sqlite3.connect(str(self.database_path))
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                "CREATE TABLE erp_audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "entity_type TEXT NOT NULL CHECK (entity_type IN ('product','repair')) ,"
                "entity_id TEXT NOT NULL,action TEXT NOT NULL,actor_id TEXT,"
                "actor_type TEXT NOT NULL DEFAULT 'user' CHECK (actor_type IN "
                "('user','system','external')),actor_display_name_snapshot TEXT NOT NULL,"
                "occurred_at TEXT NOT NULL,object_label_snapshot TEXT NOT NULL,"
                "object_secondary_snapshot TEXT NOT NULL DEFAULT '',"
                "changes_json TEXT NOT NULL DEFAULT '{}',metadata_json TEXT NOT NULL DEFAULT '{}',"
                "search_text TEXT NOT NULL DEFAULT '',status_snapshot TEXT NOT NULL DEFAULT '',"
                "source_snapshot TEXT NOT NULL DEFAULT '')"
            )
            connection.execute(
                "INSERT INTO erp_audit_events(entity_type,entity_id,action,actor_id,"
                "actor_display_name_snapshot,occurred_at,object_label_snapshot) "
                "VALUES('product','1','updated','7','Максим','2026-08-28T00:00:00Z','Товар')"
            )
            apply_audit_identity_constraints(connection)
            table_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='erp_audit_events'"
            ).fetchone()[0]
            self.assertIn("'settings'", table_sql)
            self.assertEqual(
                connection.execute("SELECT actor_id FROM erp_audit_events").fetchone()[0], "7"
            )
        finally:
            connection.close()

    def _comment_hash(self):
        with sqlite3.connect(str(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT order_id,text,author_name,created_at "
                "FROM erp_order_comments ORDER BY id"
            ).fetchall()
        return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()

    def test_partially_applied_migration_is_detected(self):
        self.initialize()
        with sqlite3.connect(str(self.database_path)) as connection:
            connection.execute(LEDGER_SQL)
            connection.execute(
                "INSERT INTO {} (migration_id,name,checksum,state,details_json) "
                "VALUES (?,?,?,'applying','{{}}')".format(LEDGER_TABLE),
                (BASELINE_ID, "partial", BASELINE_CHECKSUM),
            )
        with self.assertRaisesRegex(MigrationError, "partially applied"):
            apply_migrations(self.database_path)

    def test_repeated_runner_is_idempotent(self):
        first = apply_migrations(self.database_path, app_commit="one")
        second = apply_migrations(self.database_path, app_commit="two")
        self.assertEqual(first["schema_fingerprint"], second["schema_fingerprint"])
        with sqlite3.connect(str(self.database_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM {}".format(LEDGER_TABLE)
            ).fetchone()[0]
        self.assertEqual(count, len(MIGRATIONS))

    def test_interruption_leaves_explicit_failed_state_and_stops_retry(self):
        with mock.patch(
            "app.schema_migrations.apply_fresh_catalog_schema",
            side_effect=RuntimeError("interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                apply_migrations(self.database_path)
        with sqlite3.connect(str(self.database_path)) as connection:
            state = connection.execute(
                "SELECT state FROM {} WHERE migration_id=?".format(LEDGER_TABLE),
                (BASELINE_ID,),
            ).fetchone()[0]
        self.assertEqual(state, "failed")
        with self.assertRaisesRegex(MigrationError, "partially applied"):
            apply_migrations(self.database_path)

    def test_unknown_migration_is_rejected(self):
        self.initialize()
        with sqlite3.connect(str(self.database_path)) as connection:
            connection.execute(LEDGER_SQL)
            connection.execute(
                "INSERT INTO {} (migration_id,name,checksum,state,details_json) "
                "VALUES ('unknown','unknown','x','applied','{{}}')".format(
                    LEDGER_TABLE
                )
            )
        with self.assertRaisesRegex(MigrationError, "unknown migration"):
            apply_migrations(self.database_path)

    def test_changed_checksum_is_rejected(self):
        apply_migrations(self.database_path)
        with sqlite3.connect(str(self.database_path)) as connection:
            connection.execute(
                "UPDATE {} SET checksum='changed' WHERE migration_id=?".format(
                    LEDGER_TABLE
                ),
                (BASELINE_ID,),
            )
        with self.assertRaisesRegex(MigrationError, "checksum mismatch"):
            apply_migrations(self.database_path)

    def test_insufficient_space_failure_does_not_modify_source_database(self):
        self.initialize()
        before = self.database_path.read_bytes()
        with mock.patch.object(
            migration_preflight,
            "sqlite_backup",
            side_effect=MigrationError("insufficient disk space"),
        ):
            result = migration_preflight.preflight(self.preflight_arguments())
        self.assertEqual(result, 1)
        self.assertEqual(self.database_path.read_bytes(), before)

    def test_corrupt_database_fails_preflight_without_rewriting_source(self):
        self.database_path.write_bytes(b"not a sqlite database")
        before = self.database_path.read_bytes()
        result = migration_preflight.preflight(self.preflight_arguments())
        self.assertEqual(result, 1)
        self.assertEqual(self.database_path.read_bytes(), before)

    def test_foreign_key_violation_fails_integrity_check(self):
        with sqlite3.connect(str(self.database_path)) as connection:
            connection.executescript(
                "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
                "CREATE TABLE child(parent_id INTEGER REFERENCES parent(id));"
                "PRAGMA foreign_keys=OFF;"
                "INSERT INTO child(parent_id) VALUES (99);"
            )
        with sqlite3.connect(str(self.database_path)) as connection:
            with self.assertRaisesRegex(MigrationError, "foreign_key_check"):
                require_integrity(connection, "test")

    def test_incompatible_partial_index_is_rejected_statically(self):
        source = self.root / "source"
        (source / "app").mkdir(parents=True)
        (source / "scripts").mkdir()
        (source / "app" / "catalog_db.py").write_text(
            "SQL = '''CREATE UNIQUE INDEX bad ON comments(id) WHERE id IS NOT NULL'''\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MigrationError, "partial index"):
            validate_known_sql_compatibility(source)

    def test_two_migration_runners_cannot_hold_the_same_lock(self):
        with MigrationLock(self.database_path):
            with self.assertRaises(MigrationBusyError):
                with MigrationLock(self.database_path):
                    pass

    def test_production_apply_requires_stopped_service_acknowledgement(self):
        self.initialize()
        arguments = self.preflight_arguments()
        with self.assertRaisesRegex(MigrationError, "service-stopped"):
            migration_preflight.apply(arguments)

    def test_fresh_and_upgraded_schema_have_equal_fingerprint(self):
        upgraded = self.initialize(self.root / "upgraded.db")
        fresh = self.root / "fresh.db"
        apply_migrations(upgraded)
        apply_migrations(fresh)
        with sqlite3.connect(str(upgraded)) as first, sqlite3.connect(str(fresh)) as second:
            self.assertEqual(schema_fingerprint(first), schema_fingerprint(second))

    def test_preflight_repeats_and_preserves_source_file(self):
        self.initialize()
        report_path = self.root / "report.json"
        before = hashlib.sha256(self.database_path.read_bytes()).hexdigest()
        original_connect = socket.socket.connect
        original_getaddrinfo = socket.getaddrinfo
        result = migration_preflight.preflight(
            self.preflight_arguments(report=report_path)
        )
        after = hashlib.sha256(self.database_path.read_bytes()).hexdigest()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(before, after)
        self.assertTrue(report["idempotent"])
        self.assertTrue(report["schema_parity"])
        self.assertEqual(report["business_before"], report["business_after"])
        self.assertIs(socket.socket.connect, original_connect)
        self.assertIs(socket.getaddrinfo, original_getaddrinfo)

    def test_runtime_guard_prevents_worker_schema_repair(self):
        apply_migrations(self.database_path)
        write_runtime_guard(self.database_path, "guarded")
        with sqlite3.connect(str(self.database_path)) as connection:
            connection.execute("DROP INDEX idx_erp_order_comments_external")
        with self.assertRaises(MigrationError):
            CatalogDatabase(
                self.database_path,
                cache_initialization=False,
            ).initialize()
        with sqlite3.connect(str(self.database_path)) as connection:
            index = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                ("idx_erp_order_comments_external",),
            ).fetchone()
        self.assertIsNone(index)

    def test_comment_index_null_uniqueness_and_integrity_regression(self):
        apply_migrations(self.database_path)
        insert = (
            "INSERT INTO erp_order_comments "
            "(order_id,text,author_name,created_at,external_system,external_id) "
            "VALUES (?,?,?,?,?,?)"
        )
        with sqlite3.connect(str(self.database_path)) as connection:
            values = ("order", "text", "employee", "2026-08-24T10:00:00Z")
            connection.execute(insert, values + (None, None))
            connection.execute(insert, values + (None, None))
            connection.execute(insert, values + ("bitrix", "external-1"))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(insert, values + ("bitrix", "external-1"))
            index_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                ("idx_erp_order_comments_external",),
            ).fetchone()[0]
            self.assertNotIn(" WHERE ", index_sql.upper())
            require_integrity(connection, "comment-index")
        apply_migrations(self.database_path)

    def test_runtime_guard_verifies_intact_database(self):
        apply_migrations(self.database_path)
        write_runtime_guard(self.database_path, "guarded")
        self.assertTrue(verify_runtime_guard(self.database_path))


if __name__ == "__main__":
    unittest.main()
