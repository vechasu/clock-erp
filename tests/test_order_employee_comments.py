import tempfile
import unittest
from pathlib import Path

from app import web
from app.catalog_db import CatalogDatabase


class OrderEmployeeCommentsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_comments_preserve_author_timestamp_and_order_isolation(self):
        first = web.add_order_comment(
            "21114", "Позвонить после 18:00", "Максим", "user-1", self.database
        )
        second = web.add_order_comment(
            "21114", "Клиент подтвердил адрес", "Анна", "user-2", self.database
        )
        web.add_order_comment(
            "21115", "Другой заказ", "Максим", "user-1", self.database
        )

        comments = web.load_order_comments("21114", self.database)

        self.assertEqual([row["id"] for row in comments], [first, second])
        self.assertEqual([row["author_name"] for row in comments], ["Максим", "Анна"])
        self.assertEqual(comments[0]["author_user_id"], "user-1")
        self.assertIn("T", comments[0]["created_at"])
        self.assertNotIn("Другой заказ", [row["text"] for row in comments])

    def test_empty_and_oversized_comments_are_rejected_without_writes(self):
        for text in ("   ", "x" * 2001):
            with self.subTest(length=len(text)):
                with self.assertRaises(ValueError):
                    web.add_order_comment(
                        "21114", text, "Максим", "user-1", self.database
                    )
        self.assertEqual(web.load_order_comments("21114", self.database), [])


if __name__ == "__main__":
    unittest.main()
