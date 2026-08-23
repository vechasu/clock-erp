import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

        self.assertEqual([row["id"] for row in comments], [second, first])
        self.assertEqual([row["author_name"] for row in comments], ["Анна", "Максим"])
        self.assertEqual(comments[1]["author_user_id"], "user-1")
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

    def test_same_immediate_submit_is_idempotent_and_history_is_append_only(self):
        first = web.add_order_comment(
            "21114", "Повтор сети", "Максим", "user-1", self.database
        )
        repeated = web.add_order_comment(
            "21114", "Повтор сети", "Максим", "user-1", self.database
        )

        self.assertEqual(first, repeated)
        self.assertEqual(len(web.load_order_comments("21114", self.database)), 1)
        rules = {rule.rule for rule in web.app.url_map.iter_rules()}
        self.assertNotIn("/order/<int:order_id>/comments/<int:comment_id>", rules)

    def test_multiline_text_is_preserved(self):
        text = "Первая строка\nВторая строка"
        web.add_order_comment(
            "21114", text, "Максим", "user-1", self.database
        )
        self.assertEqual(
            web.load_order_comments("21114", self.database)[0]["text"], text
        )

    def test_route_uses_backend_identity_and_rejects_missing_access(self):
        original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        client = web.app.test_client()
        try:
            with (
                mock.patch.object(web, "can_view_orders", return_value=True),
                mock.patch.object(web, "get_order", return_value={"id": "21114"}),
                mock.patch.object(web, "current_auth_user", return_value={"id": "user-1"}),
                mock.patch.object(web, "current_sales_user_name", return_value="Максим"),
                mock.patch.object(web, "add_order_comment") as add_comment,
            ):
                response = client.post(
                    "/order/21114/comments",
                    data={"text": "Текст", "author_name": "Подмена"},
                )
            self.assertEqual(response.status_code, 302)
            add_comment.assert_called_once_with(
                21114, "Текст", "Максим", "user-1"
            )

            with mock.patch.object(web, "can_view_orders", return_value=False):
                forbidden = client.post(
                    "/order/21114/comments", data={"text": "Текст"}
                )
            self.assertEqual(forbidden.status_code, 403)
        finally:
            web.app.config.clear()
            web.app.config.update(original_config)


if __name__ == "__main__":
    unittest.main()
