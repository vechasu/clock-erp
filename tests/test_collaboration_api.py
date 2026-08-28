import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.domain_schema_migrations import apply_domain_migrations


class CollaborationApiMultiUserTest(unittest.TestCase):
    def setUp(self):
        self.original = dict(web.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.auth = root / "auth.db"
        self.tasks = root / "tasks.db"
        apply_domain_migrations(self.auth, "auth", "test")
        apply_domain_migrations(self.tasks, "tasks", "test")
        now = int(time.time())
        with sqlite3.connect(str(self.auth)) as connection:
            for user_id, name in ((1, "Максим"), (2, "MRV")):
                connection.execute(
                    "INSERT INTO users(id,first_name,last_name,email,email_normalized,password_hash,role,active,created_at,email_verified_at,updated_at,session_version) "
                    "VALUES(?,?, '', ?, ?, 'hash', 'employee', 1, ?, ?, ?, 1)",
                    (user_id, name, "{}@example.test".format(user_id),
                     "{}@example.test".format(user_id), now, now, now),
                )
        web.app.config.update(TESTING=True, AUTH_TESTING=True, AUTH_DATABASE=str(self.auth),
                              TASKS_DATABASE=str(self.tasks), SESSION_COOKIE_SECURE=False)
        self.client_a = web.app.test_client()
        self.client_b = web.app.test_client()
        self._login(self.client_a, 1, "csrf-a")
        self._login(self.client_b, 2, "csrf-b")

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original)
        self.temporary.cleanup()

    @staticmethod
    def _login(client, user_id, csrf):
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["session_version"] = 1
            session["_csrf_token"] = csrf

    @mock.patch("app.web._collaboration_entity")
    def test_backend_actor_inbox_recipient_and_session_isolation(self, entity):
        entity.return_value = {"id": "21123", "label": "Заказ №21123", "href": "/order/21123"}
        response = self.client_a.post(
            "/api/v1/responsibility/order/21123",
            json={"responsible_user_id": 2, "actor_user_id": 2},
            headers={"X-CSRF-Token": "csrf-a", "Idempotency-Key": "api-assign-21123"},
        )
        self.assertEqual(response.status_code, 200)
        inbox_b = self.client_b.get("/api/v1/inbox").get_json()["data"]
        inbox_a = self.client_a.get("/api/v1/inbox").get_json()["data"]
        self.assertEqual((inbox_b["total"], inbox_a["total"]), (1, 0))
        event = inbox_b["rows"][0]
        self.assertEqual((event["actor_user_id"], event["recipient_user_id"]), (1, 2))
        forbidden = self.client_a.post(
            "/api/v1/inbox/{}/read".format(event["id"]),
            headers={"X-CSRF-Token": "csrf-a"},
        )
        self.assertEqual(forbidden.status_code, 404)
        allowed = self.client_b.post(
            "/api/v1/inbox/{}/read".format(event["id"]),
            headers={"X-CSRF-Token": "csrf-b"},
        )
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
