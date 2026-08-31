import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.schema_migrations import (
    CATALOG_RUNTIME_BASELINE_ID,
    LEDGER_TABLE,
    MigrationError,
    apply_migrations,
    business_snapshot,
    schema_fingerprint,
    validate_catalog_runtime,
    verify_complete_catalog_contract,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CatalogRuntimeMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "catalog.db"

    def tearDown(self):
        CatalogDatabase._schema_cache.clear()
        self.temporary.cleanup()

    def migrate(self):
        return apply_migrations(self.database, app_commit="catalog-runtime-test")

    def schema_hash(self):
        with sqlite3.connect(str(self.database)) as connection:
            return schema_fingerprint(connection)

    def test_missing_corrupt_outdated_and_wrong_sentinel_never_control_runtime(self):
        self.migrate()
        sentinel = self.root / ".catalog-schema-preflight-required"
        marker = self.root / ".catalog-schema-state.json"
        variants = (
            (False, False, ""),
            (True, True, "not-json"),
            (True, True, json.dumps({"latest_migration": "outdated"})),
            (True, True, json.dumps({"migration_checksum": "wrong"})),
        )
        expected = self.schema_hash()
        for has_sentinel, has_marker, content in variants:
            for path in (sentinel, marker):
                if path.exists():
                    path.unlink()
            if has_sentinel:
                sentinel.write_text("outdated\n", encoding="utf-8")
            if has_marker:
                marker.write_text(content, encoding="utf-8")
            CatalogDatabase._schema_cache.clear()
            CatalogDatabase(self.database).initialize()
            self.assertEqual(self.schema_hash(), expected)

    def test_missing_database_fails_closed_without_creating_file_or_ledger(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.catalog_db import CatalogDatabase; "
                "CatalogDatabase(r'{}').initialize()".format(self.database),
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("catalog migration required", completed.stderr)
        self.assertFalse(self.database.exists())

    def test_partial_unknown_schema_fails_without_creating_history(self):
        with sqlite3.connect(str(self.database)) as connection:
            connection.execute("CREATE TABLE unknown_table(id INTEGER PRIMARY KEY)")
        before = self.database.read_bytes()
        with self.assertRaisesRegex(MigrationError, "migration ledger is missing"):
            validate_catalog_runtime(self.database)
        self.assertEqual(self.database.read_bytes(), before)
        with sqlite3.connect(str(self.database)) as connection:
            ledger = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (LEDGER_TABLE,),
            ).fetchone()
        self.assertIsNone(ledger)

    def test_missing_table_and_wrong_index_fail_without_runtime_repair(self):
        self.migrate()
        cases = (
            "DROP TABLE catalog_prices",
            "DROP INDEX idx_erp_order_comments_external",
        )
        for index, statement in enumerate(cases):
            path = self.root / "drift-{}.db".format(index)
            shutil.copy2(str(self.database), str(path))
            with sqlite3.connect(str(path)) as connection:
                connection.execute(statement)
            before = path.read_bytes()
            with self.assertRaisesRegex(MigrationError, "mismatch|missing"):
                validate_catalog_runtime(path)
            self.assertEqual(path.read_bytes(), before)

    def test_missing_column_and_wrong_unique_constraint_fail_closed(self):
        self.migrate()
        missing_column = self.root / "missing-column.db"
        wrong_unique = self.root / "wrong-unique.db"
        for target in (missing_column, wrong_unique):
            shutil.copy2(str(self.database), str(target))
        with sqlite3.connect(str(missing_column)) as connection:
            connection.executescript(
                "ALTER TABLE erp_order_comment_sync_state RENAME TO old_sync_state;"
                "CREATE TABLE erp_order_comment_sync_state ("
                "order_id TEXT PRIMARY KEY, external_system TEXT NOT NULL, "
                "external_ref TEXT, last_external_hash TEXT, last_external_at TEXT, "
                "last_local_comment_id INTEGER, last_local_hash TEXT, "
                "last_local_at TEXT, sync_status TEXT NOT NULL DEFAULT 'idle', "
                "retry_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT, "
                "updated_at TEXT NOT NULL);"
                "DROP TABLE old_sync_state;"
            )
        with sqlite3.connect(str(wrong_unique)) as connection:
            connection.execute("DROP INDEX idx_erp_order_comments_external")
            connection.execute(
                "CREATE INDEX idx_erp_order_comments_external "
                "ON erp_order_comments(external_system, external_id)"
            )
        for path in (missing_column, wrong_unique):
            with self.assertRaises(MigrationError):
                validate_catalog_runtime(path)

    def test_changed_catalog_baseline_checksum_fails_closed(self):
        self.migrate()
        with sqlite3.connect(str(self.database)) as connection:
            connection.execute(
                "UPDATE {} SET checksum='changed' WHERE migration_id=?".format(
                    LEDGER_TABLE
                ),
                (CATALOG_RUNTIME_BASELINE_ID,),
            )
        with self.assertRaisesRegex(MigrationError, "checksum mismatch"):
            validate_catalog_runtime(self.database)

    def test_runtime_reads_and_noop_mutation_emit_zero_schema_ddl(self):
        self.migrate()
        statements = []
        catalog = CatalogDatabase(self.database, ddl_observer=statements.append)
        catalog.initialize()
        catalog.table_names()
        with catalog.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET updated_at=updated_at WHERE 1=0"
            )
        self.assertEqual(statements, [])

    def test_repeated_validation_and_worker_restart_preserve_schema_and_data(self):
        self.migrate()
        with sqlite3.connect(str(self.database)) as connection:
            before_business = business_snapshot(connection)
            before_schema = schema_fingerprint(connection)
        for unused in range(3):
            CatalogDatabase._schema_cache.clear()
            self.assertTrue(validate_catalog_runtime(self.database))
            CatalogDatabase(self.database).initialize()
        with sqlite3.connect(str(self.database)) as connection:
            self.assertEqual(schema_fingerprint(connection), before_schema)
            self.assertEqual(business_snapshot(connection), before_business)

    def test_complete_manifest_verifies_all_tables_indexes_checks_and_triggers(self):
        self.migrate()
        with sqlite3.connect(str(self.database)) as connection:
            self.assertTrue(verify_complete_catalog_contract(connection))
            objects = connection.execute(
                "SELECT type, COUNT(*) FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' GROUP BY type"
            ).fetchall()
        self.assertIn(("table", 54), objects)
        self.assertIn(("trigger", 4), objects)

    def test_runtime_validator_accepts_read_only_database(self):
        self.migrate()
        original_mode = self.database.stat().st_mode
        try:
            os.chmod(str(self.database), 0o400)
            self.assertTrue(validate_catalog_runtime(self.database))
        finally:
            os.chmod(str(self.database), original_mode)


if __name__ == "__main__":
    unittest.main()
