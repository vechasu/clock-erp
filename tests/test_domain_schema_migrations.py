import multiprocessing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.auth import AuthStore
from app.domain_schema_migrations import (
    AUTH_INDEX_STATEMENTS,
    AUTH_MIGRATION,
    AUTH_MIGRATION_ID,
    AUTH_TABLE_STATEMENTS,
    DOMAIN_MIGRATIONS,
    LEDGER_SQL,
    LEDGER_TABLE,
    ORDERS_MIGRATION,
    DomainMigrationError,
    MigrationRequiredError,
    apply_domain_migrations,
    domain_snapshot,
    validate_auth_database,
    validate_orders_database,
)
from app.services.orders_snapshot import OrdersSnapshotStore


def _parallel_apply(path, kind, queue):
    try:
        queue.put((True, apply_domain_migrations(path, kind, "parallel")))
    except Exception as error:
        queue.put((False, "{}: {}".format(type(error).__name__, error)))


class DomainSchemaMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_fresh_auth_and_orders_are_versioned_and_repeatable(self):
        for kind in ("auth", "orders"):
            with self.subTest(kind=kind):
                path = self.root / "{}.db".format(kind)
                first = apply_domain_migrations(path, kind, "first")
                second = apply_domain_migrations(path, kind, "second")
                self.assertEqual(first["schema_fingerprint"], second["schema_fingerprint"])
                self.assertEqual(first["business_counts"], second["business_counts"])
                with sqlite3.connect(str(path)) as connection:
                    row = connection.execute(
                        "SELECT migration_id,checksum,state,app_commit FROM " + LEDGER_TABLE
                        + " WHERE migration_id = ?",
                        (DOMAIN_MIGRATIONS[kind]["id"],),
                    ).fetchone()
                migration = DOMAIN_MIGRATIONS[kind]
                self.assertEqual(row[:3], (
                    migration["id"], migration["checksum"], "applied"
                ))
                self.assertEqual(row[3], "first")

    def test_known_legacy_auth_upgrade_preserves_user(self):
        path = self.root / "auth-legacy.db"
        with sqlite3.connect(str(path)) as connection:
            connection.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "first_name TEXT NOT NULL,last_name TEXT NOT NULL,email TEXT NOT NULL,"
                "email_normalized TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,"
                "role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO users(first_name,last_name,email,email_normalized,password_hash,role,active,created_at) "
                "VALUES('A','B','a@example.test','a@example.test','hash','employee',1,123)"
            )
        apply_domain_migrations(path, "auth", "upgrade")
        validate_auth_database(path)
        with sqlite3.connect(str(path)) as connection:
            row = connection.execute(
                "SELECT email_normalized,email_verified_at,updated_at,session_version FROM users"
            ).fetchone()
        self.assertEqual(row, ("a@example.test", 123, 123, 1))

    def test_auth_v1_ledger_upgrades_to_user_preferences(self):
        path = self.root / "auth-v1.db"
        with sqlite3.connect(str(path)) as connection:
            connection.execute(LEDGER_SQL)
            for statement in AUTH_TABLE_STATEMENTS + AUTH_INDEX_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO {} "
                "(migration_id,name,checksum,state,applied_at,app_commit,details_json) "
                "VALUES (?,?,?,'applied','2026-08-28','old','{{}}')".format(
                    LEDGER_TABLE
                ),
                (
                    AUTH_MIGRATION["id"],
                    AUTH_MIGRATION["name"],
                    AUTH_MIGRATION["checksum"],
                ),
            )
            connection.execute(
                "INSERT INTO users "
                "(first_name,last_name,email,email_normalized,password_hash,role,active,created_at,"
                "email_verified_at,updated_at,session_version) "
                "VALUES ('A','B','a@example.test','a@example.test','hash','admin',1,1,1,1,1)"
            )
        before = domain_snapshot(path, "auth")
        after = apply_domain_migrations(path, "auth", "upgrade")
        validate_auth_database(path)
        self.assertEqual(before["business_counts"], after["business_counts"])
        with sqlite3.connect(str(path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM user_navigation_preferences"
                ).fetchone()[0],
                0,
            )

    def test_known_legacy_orders_upgrade_preserves_snapshot(self):
        path = self.root / "orders-legacy.db"
        with sqlite3.connect(str(path)) as connection:
            connection.execute(
                "CREATE TABLE orders_snapshot (order_id TEXT PRIMARY KEY,"
                "source_position INTEGER NOT NULL,number_fold TEXT NOT NULL,"
                "customer_fold TEXT NOT NULL,phone_digits TEXT NOT NULL,"
                "amount_search TEXT NOT NULL,date_search TEXT NOT NULL,"
                "created_sort TEXT NOT NULL,status TEXT NOT NULL,item_units REAL,"
                "payload_json TEXT NOT NULL,loaded_at REAL NOT NULL)"
            )
            connection.execute(
                "INSERT INTO orders_snapshot VALUES "
                "('42',0,'42','customer','','','','2026-08-25','N',2,'{\"id\":\"42\"}',1)"
            )
        apply_domain_migrations(path, "orders", "upgrade")
        validate_orders_database(path)
        with sqlite3.connect(str(path)) as connection:
            row = connection.execute(
                "SELECT order_id,payload_json,item_units,source,external_order_id FROM orders_snapshot"
            ).fetchone()
        self.assertEqual(row, ("42", '{"id":"42"}', 2.0, "tictactoy", "42"))

    def test_unknown_and_partial_schemas_fail_without_repair(self):
        for kind, statement in (
            ("auth", "CREATE TABLE users(id INTEGER PRIMARY KEY)"),
            ("orders", "CREATE TABLE orders_snapshot(order_id TEXT PRIMARY KEY)"),
        ):
            with self.subTest(kind=kind):
                path = self.root / "{}-partial.db".format(kind)
                with sqlite3.connect(str(path)) as connection:
                    connection.execute(statement)
                before = path.read_bytes()
                with self.assertRaisesRegex(DomainMigrationError, "schema contract"):
                    apply_domain_migrations(path, kind)
                self.assertEqual(path.read_bytes(), before)
                with sqlite3.connect(str(path)) as connection:
                    self.assertNotIn(LEDGER_TABLE, {
                        row[0] for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    })

    def test_changed_checksum_and_unknown_migration_fail_closed(self):
        for kind in ("auth", "orders"):
            with self.subTest(kind=kind):
                path = self.root / "{}-checksum.db".format(kind)
                apply_domain_migrations(path, kind)
                with sqlite3.connect(str(path)) as connection:
                    connection.execute(
                        "UPDATE {} SET checksum='changed'".format(LEDGER_TABLE)
                    )
                with self.assertRaisesRegex(DomainMigrationError, "checksum mismatch"):
                    apply_domain_migrations(path, kind)

    def test_interrupted_transaction_rolls_back_and_retry_succeeds(self):
        path = self.root / "interrupted-auth.db"
        calls = []

        def interrupt(statement):
            calls.append(statement)
            if len(calls) == 3:
                raise RuntimeError("interrupted")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            apply_domain_migrations(path, "auth", observer=interrupt)
        with sqlite3.connect(str(path)) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
        self.assertEqual(tables, set())
        apply_domain_migrations(path, "auth")
        self.assertTrue(validate_auth_database(path))

    def test_two_parallel_runners_serialize_and_both_succeed(self):
        for kind in ("auth", "orders"):
            with self.subTest(kind=kind):
                path = self.root / "{}-parallel.db".format(kind)
                queue = multiprocessing.Queue()
                processes = [
                    multiprocessing.Process(
                        target=_parallel_apply, args=(str(path), kind, queue)
                    )
                    for unused in range(2)
                ]
                for process in processes:
                    process.start()
                results = [queue.get(timeout=20) for unused in processes]
                for process in processes:
                    process.join(20)
                self.assertTrue(all(item[0] for item in results), results)

    def test_runtime_stores_fail_closed_and_emit_zero_ddl(self):
        auth = self.root / "runtime-auth.db"
        orders = self.root / "runtime-orders.db"
        with self.assertRaises(MigrationRequiredError):
            AuthStore(auth)
        with self.assertRaises(MigrationRequiredError):
            OrdersSnapshotStore(orders).initialize()
        self.assertFalse(auth.exists())
        self.assertFalse(orders.exists())
        apply_domain_migrations(auth, "auth")
        apply_domain_migrations(orders, "orders")
        statements = []
        original_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(lambda sql: statements.append(sql))
            return connection

        try:
            sqlite3.connect = traced_connect
            AuthStore(auth).get_user(None)
            OrdersSnapshotStore(orders).initialize().count()
        finally:
            sqlite3.connect = original_connect
        modifying = [
            sql for sql in statements
            if sql.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP "))
        ]
        self.assertEqual(modifying, [])

    def test_semantic_orders_fingerprint_does_not_depend_on_column_order(self):
        canonical = self.root / "canonical.db"
        apply_domain_migrations(canonical, "orders")
        first = domain_snapshot(canonical, "orders")["schema_fingerprint"]
        with sqlite3.connect(str(canonical)) as connection:
            connection.execute("ALTER TABLE orders_snapshot RENAME TO orders_snapshot_old")
            connection.execute(
                "CREATE TABLE orders_snapshot (order_id TEXT PRIMARY KEY,source TEXT NOT NULL DEFAULT 'tictactoy',"
                "external_order_id TEXT,source_position INTEGER NOT NULL,number_fold TEXT NOT NULL,"
                "customer_fold TEXT NOT NULL,extra_fold TEXT NOT NULL DEFAULT '',phone_digits TEXT NOT NULL,"
                "amount_search TEXT NOT NULL,date_search TEXT NOT NULL,created_sort TEXT NOT NULL,status TEXT NOT NULL,"
                "item_units REAL,detail_loaded INTEGER NOT NULL DEFAULT 0,payload_json TEXT NOT NULL,loaded_at REAL NOT NULL,"
                "customer_id INTEGER)"
            )
            connection.execute("DROP TABLE orders_snapshot_old")
            for name, columns, unique in (
                ("idx_orders_snapshot_ordering", "source_position,order_id", ""),
                ("idx_orders_snapshot_status_ordering", "status,source_position,order_id", ""),
                ("idx_orders_snapshot_created", "created_sort,source_position,order_id", ""),
                ("idx_orders_snapshot_source_external", "source,external_order_id", "UNIQUE "),
                ("idx_orders_snapshot_source_ordering", "source,source_position,order_id", ""),
                ("idx_orders_snapshot_customer_created", "customer_id,created_sort", ""),
            ):
                connection.execute("CREATE {}INDEX {} ON orders_snapshot({})".format(unique, name, columns))
        self.assertTrue(validate_orders_database(canonical))
        second = domain_snapshot(canonical, "orders")["schema_fingerprint"]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
