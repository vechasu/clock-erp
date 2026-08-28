import base64
import tempfile
import time
import unittest
from pathlib import Path

from app import auth, web
from app.domain_schema_migrations import apply_domain_migrations
from app.mail_migrations import migrate_database
from app.services.mail import MailStore, SecretBox, parse_message

from tests.test_mail import KEY, raw_message


class MailWebTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.previous_key = __import__("os").environ.get("ERP_MAIL_SECRET_KEY")
        __import__("os").environ["ERP_MAIL_SECRET_KEY"] = KEY
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.auth_path = root / "auth.db"
        self.tasks_path = root / "tasks.db"
        self.mail_path = root / "mail.db"
        apply_domain_migrations(self.auth_path, "auth", "test")
        apply_domain_migrations(self.tasks_path, "tasks", "test")
        migrate_database(self.mail_path)
        store = auth.AuthStore(self.auth_path)
        now = int(time.time())
        with store.connect() as connection:
            self.owner_id = connection.execute(
                "INSERT INTO users(first_name,last_name,email,email_normalized,password_hash,role,active,created_at,email_verified_at,updated_at,session_version) VALUES('Максим','Owner','owner@example.test','owner@example.test','hash','admin',1,?,?,?,1)",
                (now, now, now),
            ).lastrowid
            self.employee_id = connection.execute(
                "INSERT INTO users(first_name,last_name,email,email_normalized,password_hash,role,active,created_at,email_verified_at,updated_at,session_version) VALUES('Анна','Employee','employee@example.test','employee@example.test','hash','employee',1,?,?,?,1)",
                (now, now, now),
            ).lastrowid
        web.app.config.update(
            TESTING=True, AUTH_TESTING=True, AUTH_DATABASE=str(self.auth_path),
            TASKS_DATABASE=str(self.tasks_path), MAIL_DATABASE=str(self.mail_path),
            MAIL_ATTACHMENT_ROOT=str(root / "attachments"), SESSION_COOKIE_SECURE=False,
        )
        self.client = web.app.test_client()
        self.store = MailStore(self.mail_path, root / "attachments")

    def tearDown(self):
        web.app.config.clear(); web.app.config.update(self.original_config)
        if self.previous_key is None:
            __import__("os").environ.pop("ERP_MAIL_SECRET_KEY", None)
        else:
            __import__("os").environ["ERP_MAIL_SECRET_KEY"] = self.previous_key
        self.temporary.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["session_version"] = 1
            session["_csrf_token"] = "mail-csrf"

    @property
    def headers(self):
        return {"X-CSRF-Token": "mail-csrf"}

    def account_payload(self):
        return {"mailbox_name": "Общий", "sender_name": "ERP", "email": "erp@example.test",
                "imap_host": "imap.example.test", "imap_port": 993,
                "smtp_host": "smtp.example.test", "smtp_port": 465,
                "security": "ssl", "login": "erp@example.test", "password": "app-password"}

    def test_mail_requires_login_and_renders_real_workspace(self):
        self.assertEqual(self.client.get("/app/mail").status_code, 302)
        self.login(self.employee_id)
        page = self.client.get("/app/mail")
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn("Почта не подключена", text)
        self.assertIn('data-navigation-key="mail"', text)

    def test_only_owner_can_save_connection_and_secret_is_not_returned(self):
        self.login(self.employee_id)
        denied = self.client.post("/api/v1/mail/settings", json=self.account_payload(), headers=self.headers)
        self.assertEqual(denied.status_code, 403)
        self.login(self.owner_id)
        saved = self.client.post("/api/v1/mail/settings", json=self.account_payload(), headers=self.headers)
        self.assertEqual(saved.status_code, 200)
        self.assertNotIn("password", saved.get_data(as_text=True).casefold())
        with self.store.connect() as connection:
            encrypted = connection.execute("SELECT encrypted_password FROM mail_accounts").fetchone()[0]
        self.assertNotIn("app-password", encrypted)

    def test_csrf_and_idempotent_outbox(self):
        self.store.save_account(self.account_payload(), self.owner_id, SecretBox(KEY))
        self.login(self.employee_id)
        payload = {"to": "client@example.test", "subject": "Ответ", "text_body": "Текст"}
        self.assertEqual(self.client.post("/api/v1/mail/outbox", json=payload).status_code, 403)
        headers = dict(self.headers, **{"Idempotency-Key": "web-once"})
        first = self.client.post("/api/v1/mail/outbox", json=payload, headers=headers)
        repeated = self.client.post("/api/v1/mail/outbox", json=payload, headers=headers)
        self.assertEqual((first.status_code, repeated.status_code), (201, 200))
        self.assertTrue(repeated.get_json()["meta"]["duplicate"])

    def test_thread_api_marks_read_and_never_returns_active_html(self):
        account = self.store.save_account(self.account_payload(), self.owner_id, SecretBox(KEY))
        thread_id, unused = self.store.ingest(account["id"], "inbox", "INBOX", "1", 1,
                                              parse_message(raw_message(html=True)))
        self.login(self.employee_id)
        response = self.client.get("/api/v1/mail/threads/{}".format(thread_id))
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn("<script", body)
        self.assertNotIn('<img src=\\"https://tracker.test', body)
        self.assertEqual(self.store.get_thread(thread_id)["unread_count"], 0)

    def test_manual_sync_is_rate_limited_and_background_only(self):
        self.store.save_account(self.account_payload(), self.owner_id, SecretBox(KEY))
        self.login(self.employee_id)
        first = self.client.post("/api/v1/mail/sync", json={}, headers=self.headers)
        second = self.client.post("/api/v1/mail/sync", json={}, headers=self.headers)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 422)


if __name__ == "__main__":
    unittest.main()
