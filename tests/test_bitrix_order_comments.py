import unittest
from pathlib import Path
from unittest import mock

from app.clients.bitrix_order_comments import (
    BitrixOrderCommentError,
    BitrixOrderCommentsClient,
)


class Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class BitrixOrderCommentsClientTest(unittest.TestCase):
    def client(self, session):
        return BitrixOrderCommentsClient(
            "https://example.test/order-comments.php", "token",
            session=session, max_retries=0,
        )

    def test_read_and_update_use_bearer_auth_and_optimistic_hash(self):
        session = mock.Mock()
        session.request.side_effect = [
            Response(200, {"comment": {"text": "До", "hash": "old"}}),
            Response(200, {"comment": {"text": "После", "hash": "new"}}),
        ]
        client = self.client(session)
        self.assertEqual(client.get("21119")["text"], "До")
        self.assertEqual(client.update("21119", "После", "old")["hash"], "new")
        update = session.request.call_args_list[1]
        self.assertEqual(update.kwargs["json"]["expected_hash"], "old")
        self.assertEqual(update.kwargs["headers"]["Authorization"], "Bearer token")

    def test_conflict_returns_current_snapshot_without_overwrite(self):
        session = mock.Mock()
        current = {"text": "Bitrix", "hash": "current"}
        session.request.return_value = Response(409, {
            "error": "comment_conflict", "current": current,
        })
        with self.assertRaises(BitrixOrderCommentError) as raised:
            self.client(session).update("21119", "ERP", "old")
        self.assertEqual(raised.exception.code, "conflict")
        self.assertEqual(raised.exception.current, current)

    def test_endpoint_uses_actual_single_bitrix_comments_field(self):
        source = (
            Path(__file__).resolve().parents[1] / "bitrix/order-comments.php"
        ).read_text(encoding="utf-8")
        self.assertIn("getField('COMMENTS')", source)
        self.assertIn("setField('COMMENTS', $text)", source)
        self.assertIn("'history_supported' => false", source)
        self.assertIn("'entity_id_supported' => false", source)
        self.assertIn("expected_hash", source)
        self.assertIn("hash_equals", source)
        self.assertNotIn("USER_DESCRIPTION", source)


if __name__ == "__main__":
    unittest.main()
