import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.clients.bitrix_order_comments import BitrixOrderCommentError
from app.services.order_comments import OrderCommentsService, text_hash


class FakeBitrixComments:
    def __init__(self, text="", updated_at="2026-08-24T12:00:00+03:00"):
        self.text = text
        self.updated_at = updated_at
        self.get_calls = 0
        self.update_calls = 0
        self.failure = None

    def snapshot(self):
        return {
            "order_id": "21119",
            "field": "COMMENTS",
            "text": self.text,
            "hash": text_hash(self.text),
            "updated_at": self.updated_at,
            "author": None,
            "history_supported": False,
            "entity_id_supported": False,
        }

    def get(self, order_id):
        self.get_calls += 1
        return self.snapshot()

    def update(self, order_id, text, expected_hash):
        self.update_calls += 1
        if self.failure:
            raise self.failure
        if expected_hash and expected_hash != text_hash(self.text):
            raise BitrixOrderCommentError(
                "conflict", "conflict", self.snapshot()
            )
        self.text = text
        self.updated_at = "2026-08-24T12:01:00+03:00"
        return self.snapshot()


class OrderCommentSyncTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(
            Path(self.temporary.name) / "catalog.db",
            cache_initialization=False,
        )
        self.client = FakeBitrixComments()
        self.service = OrderCommentsService(
            self.database, client_factory=lambda: self.client
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_erp_to_bitrix_is_local_first_and_loop_safe(self):
        comment = self.service.create(
            "21119", "Позвонить после 18:00", "Максим", "user-1",
            external_order_id="21119",
        )
        self.assertEqual(comment["sync_status"], "pending")
        self.assertEqual(self.client.update_calls, 0)

        result = self.service.push(comment["id"])
        self.assertEqual(result["status"], "synced")
        self.assertEqual(self.client.text, "Позвонить после 18:00")
        self.assertFalse(self.service.pull("21119", "21119")["imported"])
        self.assertEqual(len(self.service.list("21119")), 1)

    def test_legacy_import_and_repeated_pull_are_idempotent(self):
        self.client.text = "Комментарий менеджера из Bitrix"
        first = self.service.pull("21119", "21119")
        second = self.service.pull("21119", "21119")
        comments = self.service.list("21119")

        self.assertTrue(first["imported"])
        self.assertFalse(second["imported"])
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["source"], "bitrix_legacy")
        self.assertEqual(comments[0]["author_name"], "Bitrix")
        self.assertEqual(comments[0]["external_updated_at"], self.client.updated_at)

    def test_temporary_failure_keeps_text_and_retry_succeeds(self):
        comment = self.service.create(
            "21119", "Локально сохранено", "Максим", "user-1",
            external_order_id="21119",
        )
        self.client.failure = BitrixOrderCommentError("timeout")
        with self.assertRaises(BitrixOrderCommentError):
            self.service.push(comment["id"])
        failed = self.service.get("21119", comment["id"])
        self.assertEqual(failed["text"], "Локально сохранено")
        self.assertEqual(failed["sync_status"], "error")

        self.client.failure = None
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE erp_order_comments SET next_retry_at = NULL WHERE id = ?",
                (comment["id"],),
            )
        retry = self.service.retry_pending()
        self.assertEqual(retry["synced"], 1)
        self.assertEqual(self.client.text, "Локально сохранено")

    def test_conflict_preserves_both_versions_without_overwrite(self):
        self.client.text = "Первая версия Bitrix"
        self.service.pull("21119", "21119")
        comment = self.service.create(
            "21119", "Версия ERP", "Максим", "user-1",
            external_order_id="21119",
        )
        self.client.text = "Параллельная версия Bitrix"

        result = self.service.push(comment["id"])
        comments = self.service.list("21119")
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.client.text, "Параллельная версия Bitrix")
        self.assertEqual(
            {row["text"] for row in comments},
            {"Первая версия Bitrix", "Параллельная версия Bitrix", "Версия ERP"},
        )
        self.assertEqual(
            self.service.get("21119", comment["id"])["sync_status"],
            "conflict",
        )

    def test_edit_keeps_created_at_and_uses_current_backend_identity(self):
        comment = self.service.create(
            "21119", "До", "Максим", "user-1", external_order_id="21119"
        )
        self.service.push(comment["id"])
        with self.assertRaises(PermissionError):
            self.service.edit("21119", comment["id"], "Подмена", "user-2")

        edited = self.service.edit("21119", comment["id"], "После", "user-1")
        self.assertEqual(edited["created_at"], comment["created_at"])
        self.assertEqual(edited["text"], "После")
        self.assertEqual(edited["sync_status"], "pending")
        self.service.push(comment["id"])
        self.assertEqual(self.client.text, "После")

    def test_order_without_bitrix_link_never_calls_external_service(self):
        comment = self.service.create(
            "wb:123", "Работает локально", "Анна", "user-2"
        )
        self.assertEqual(comment["sync_status"], "not_applicable")
        self.assertEqual(self.service.retry_pending()["attempted"], 0)
        self.assertEqual(self.client.get_calls + self.client.update_calls, 0)

    def test_additive_schema_upgrade_preserves_legacy_comments(self):
        legacy_path = Path(self.temporary.name) / "legacy.db"
        with sqlite3.connect(str(legacy_path)) as connection:
            connection.execute(
                "CREATE TABLE erp_order_comments (id INTEGER PRIMARY KEY, "
                "order_id TEXT NOT NULL, text TEXT NOT NULL, author_name TEXT NOT NULL, "
                "author_user_id TEXT, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO erp_order_comments VALUES "
                "(1, '21119', 'Не потерять', 'Максим', 'user-1', "
                "'2026-08-20T10:00:00+00:00')"
            )
        database = CatalogDatabase(legacy_path, cache_initialization=False)
        database.initialize()
        comment = OrderCommentsService(database).list("21119")[0]
        self.assertEqual(comment["text"], "Не потерять")
        self.assertEqual(comment["updated_at"], comment["created_at"])
        self.assertEqual(comment["source"], "erp")

    def test_external_mapping_index_uses_legacy_sqlite_compatible_sql(self):
        source = (Path(__file__).resolve().parents[1] / "app/catalog_db.py").read_text(
            encoding="utf-8"
        )
        statement = source.split(
            '"CREATE UNIQUE INDEX IF NOT EXISTS idx_erp_order_comments_external "',
            1,
        )[1].split(")\n", 1)[0]
        self.assertNotIn("WHERE", statement)

    def test_comment_sync_sql_avoids_modern_upsert_syntax(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app/services/order_comments.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ON CONFLICT", source)
        self.assertNotIn("excluded.", source)
        self.assertNotIn("RETURNING", source)

    def test_sync_state_update_then_insert_is_repeatable_and_preserves_state(self):
        first = self.service.create(
            "21119", "Первый", "Максим", "user-1",
            external_order_id="21119",
        )
        self.service.push(first["id"])
        second = self.service.create(
            "21119", "Второй", "Максим", "user-1",
            external_order_id="21119",
        )

        with self.database.connect() as connection:
            state = connection.execute(
                "SELECT * FROM erp_order_comment_sync_state WHERE order_id = ?",
                ("21119",),
            ).fetchone()
        self.assertIsNotNone(state["last_external_hash"])
        self.assertEqual(state["external_order_id"], "21119")
        self.assertEqual(state["last_outbound_comment_id"], first["id"])
        self.assertNotEqual(first["id"], second["id"])

    def test_schema_upgrade_repeats_after_partial_previous_attempt(self):
        partial_path = Path(self.temporary.name) / "partial.db"
        with sqlite3.connect(str(partial_path)) as connection:
            connection.execute(
                "CREATE TABLE erp_order_comments (id INTEGER PRIMARY KEY, "
                "order_id TEXT NOT NULL, text TEXT NOT NULL, author_name TEXT NOT NULL, "
                "author_user_id TEXT, created_at TEXT NOT NULL, updated_at TEXT, "
                "external_system TEXT, external_id TEXT)"
            )
            connection.execute(
                "INSERT INTO erp_order_comments VALUES "
                "(1, 'legacy', 'Сохранить', 'Максим', NULL, "
                "'2026-08-20T10:00:00+00:00', NULL, NULL, NULL)"
            )
            connection.execute(
                "CREATE TABLE erp_order_comment_sync_state ("
                "order_id TEXT PRIMARY KEY, external_order_id TEXT NOT NULL, "
                "last_external_hash TEXT, last_external_updated_at TEXT, "
                "last_outbound_comment_id INTEGER, updated_at TEXT NOT NULL)"
            )
        database = CatalogDatabase(partial_path, cache_initialization=False)

        database.initialize()
        database.initialize()

        with database.connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(erp_order_comments)"
                ).fetchall()
            }
            index_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                ("idx_erp_order_comments_external",),
            ).fetchone()["sql"]
            comment = connection.execute(
                "SELECT text, updated_at, source FROM erp_order_comments WHERE id=1"
            ).fetchone()
        self.assertIn("last_sync_error", columns)
        self.assertNotIn(" WHERE ", index_sql.upper())
        self.assertEqual(comment["text"], "Сохранить")
        self.assertEqual(comment["updated_at"], "2026-08-20T10:00:00+00:00")
        self.assertEqual(comment["source"], "erp")

    def test_external_unique_index_keeps_null_and_source_semantics(self):
        self.database.initialize()
        values = (
            "order-a", "Текст", "Автор", "2026-08-24T10:00:00+00:00",
        )
        insert = (
            "INSERT INTO erp_order_comments "
            "(order_id, text, author_name, created_at, external_system, external_id) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        with self.database.transaction() as connection:
            connection.execute(insert, values + (None, None))
            connection.execute(insert, values + (None, None))
            connection.execute(insert, values + ("bitrix", "external-1"))
            connection.execute(insert, values + ("other", "external-1"))
            connection.execute(insert, values + ("bitrix", ""))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    insert,
                    ("order-b",) + values[1:] + ("bitrix", "external-1"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(insert, values + ("bitrix", ""))

    def test_duplicate_external_mapping_stops_upgrade_without_data_loss(self):
        duplicate_path = Path(self.temporary.name) / "duplicates.db"
        with sqlite3.connect(str(duplicate_path)) as connection:
            connection.execute(
                "CREATE TABLE erp_order_comments (id INTEGER PRIMARY KEY, "
                "order_id TEXT NOT NULL, text TEXT NOT NULL, author_name TEXT NOT NULL, "
                "author_user_id TEXT, created_at TEXT NOT NULL, updated_at TEXT, "
                "external_system TEXT, external_id TEXT)"
            )
            connection.executemany(
                "INSERT INTO erp_order_comments VALUES "
                "(?, ?, ?, 'Bitrix', NULL, '2026-08-20T10:00:00+00:00', "
                "'2026-08-20T10:00:00+00:00', 'bitrix', 'duplicate')",
                ((1, "order-a", "Первый"), (2, "order-b", "Второй")),
            )
        database = CatalogDatabase(duplicate_path, cache_initialization=False)

        with self.assertRaises(sqlite3.IntegrityError):
            database.initialize()

        with sqlite3.connect(str(duplicate_path)) as connection:
            rows = connection.execute(
                "SELECT order_id, text FROM erp_order_comments ORDER BY id"
            ).fetchall()
        self.assertEqual(
            rows,
            [("order-a", "Первый"), ("order-b", "Второй")],
        )

    def test_same_snapshot_for_different_external_orders_does_not_conflict(self):
        self.client.text = "Одинаковый текст"
        first = self.service.pull("erp-order-a", "bitrix-order-a")
        second = self.service.pull("erp-order-b", "bitrix-order-b")

        self.assertTrue(first["imported"])
        self.assertTrue(second["imported"])
        self.assertEqual(len(self.service.list("erp-order-a")), 1)
        self.assertEqual(len(self.service.list("erp-order-b")), 1)


if __name__ == "__main__":
    unittest.main()
