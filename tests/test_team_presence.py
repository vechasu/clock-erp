import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import auth, web
from app.domain_schema_migrations import apply_domain_migrations
from app.services.audit_journal import AuditJournal


class TeamPresenceTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp.name) / "auth.db"
        apply_domain_migrations(self.auth_path, "auth", "team-test")
        web.app.config.update(
            TESTING=True,
            AUTH_TESTING=True,
            AUTH_DATABASE=str(self.auth_path),
            SESSION_COOKIE_SECURE=False,
        )
        self.store = auth.AuthStore(self.auth_path)
        self.now = int(time.time())
        self.user_a = self._insert_user("maxim@example.test", "admin")
        self.user_b = self._insert_user("mrv@example.test", "admin")

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp.cleanup()

    def _insert_user(self, email, role):
        with self.store.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO users(first_name,last_name,email,email_normalized,"
                "password_hash,role,active,created_at,email_verified_at,updated_at,"
                "session_version) VALUES(?,?,?,?,?,?,1,?,?,?,1)",
                (
                    email.split("@", 1)[0], "", email, email,
                    generate_password_hash("presence-password", method=auth.PASSWORD_HASH_METHOD),
                    role, self.now, self.now, self.now,
                ),
            )
        return cursor.lastrowid

    def _insert_session(self, user_id, section, updated_at):
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO auth_sessions(session_hash,user_id,data,expires_at,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (
                    "session-{}".format(user_id), user_id,
                    json.dumps({"current_section": section}),
                    self.now + 3600, self.now - 1000, updated_at,
                ),
            )

    def test_presence_is_per_user_persistent_and_timeout_based(self):
        self._insert_session(self.user_a, "Аналитика", self.now - 30)
        self._insert_session(self.user_b, "Инвентаризация", self.now - 400)
        team = {item["id"]: item for item in self.store.list_team_presence(self.now)}
        self.assertTrue(team[self.user_a]["online"])
        self.assertFalse(team[self.user_b]["online"])
        self.assertEqual(team[self.user_a]["current_section"], "Аналитика")
        self.assertEqual(team[self.user_b]["current_section"], "Инвентаризация")
        self.assertEqual(team[self.user_a]["role"], "owner")

    def test_heartbeat_uses_authenticated_user_not_payload_user_id(self):
        client = web.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = self.user_a
            session_data["session_version"] = 1
            session_data["_csrf_token"] = "presence-csrf"
        response = client.post(
            "/api/v1/presence/heartbeat",
            json={"user_id": self.user_b, "section": "Заказы"},
            headers={"X-CSRF-Token": "presence-csrf"},
        )
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as session_data:
            self.assertEqual(session_data["user_id"], self.user_a)
            self.assertEqual(session_data["current_section"], "Заказы")
        team = {item["id"]: item for item in self.store.list_team_presence()}
        self.assertEqual(team[self.user_a]["current_section"], "Заказы")
        self.assertIsNone(team[self.user_b]["last_activity_at"])

    def test_heartbeat_has_operation_id_but_no_business_audit_event(self):
        client = web.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = self.user_a
            session_data["session_version"] = 1
            session_data["_csrf_token"] = "presence-csrf"
        before = AuditJournal().list_events(limit=100)["events"]
        response = client.post(
            "/api/v1/presence/heartbeat",
            json={"section": "Заказы"},
            headers={"X-CSRF-Token": "presence-csrf", "X-Operation-ID": "presence-test-123"},
        )
        after = AuditJournal().list_events(limit=100)["events"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Operation-ID"], "presence-test-123")
        self.assertEqual(response.get_json()["meta"]["request_id"], "presence-test-123")
        self.assertEqual(len(after), len(before))

    def test_login_audit_uses_stable_user_id(self):
        web.record_login_audit({
            "id": self.user_a, "first_name": "Максим", "last_name": "",
            "email": "maxim@example.test",
        })
        listing = AuditJournal().list_events(
            entity_type="user", entity_id=str(self.user_a), action="logged_in"
        )
        self.assertTrue(listing["events"])
        event = listing["events"][0]
        self.assertEqual(event["actor_id"], str(self.user_a))
        self.assertEqual(event["actor_display_name_snapshot"], "Максим")


if __name__ == "__main__":
    unittest.main()
