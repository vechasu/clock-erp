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


if __name__ == "__main__":
    unittest.main()
