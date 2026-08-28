import base64
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import auth, web
from app.domain_schema_migrations import apply_domain_migrations
from app.mail_migrations import migrate_database
from app.services.mail import MailStore, SecretBox, parse_message

from tests.test_mail import KEY, raw_message


class FakeConnectionTransport:
    connected = True
    checks = 0

    def __init__(self, account, password, timeout=8):
        self.account = account
        self.password = password
        self.timeout = timeout

    def check(self):
        type(self).checks += 1
        if self.connected:
            return {
                "connected": True,
                "imap": {"connected": True, "message": "Подключено"},
                "smtp": {"connected": True, "message": "Подключено"},
                "tls": {"active": True, "message": "Защищённое соединение активно"},
            }
        return {
            "connected": False,
            "imap": {"connected": False, "message": "Неверный логин или пароль приложения."},
            "smtp": {"connected": False, "message": "SMTP-сервер недоступен."},
            "tls": {"active": True, "message": "Защищённое соединение активно"},
        }


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
        FakeConnectionTransport.connected = True
        FakeConnectionTransport.checks = 0

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
                "imap_security": "ssl", "smtp_security": "ssl",
                "security": "ssl", "login": "erp@example.test", "password": "app-password"}

    def verified_payload(self):
        payload = self.account_payload()
        with patch.object(web, "MailTransport", FakeConnectionTransport):
            response = self.client.post(
                "/api/v1/mail/settings/test", json=payload, headers=self.headers
            )
        self.assertEqual(response.status_code, 200)
        payload["connection_proof"] = response.get_json()["data"]["proof"]
        return payload

    def test_mail_requires_login_and_renders_real_workspace(self):
        self.assertEqual(self.client.get("/app/mail").status_code, 302)
        self.login(self.employee_id)
        page = self.client.get("/app/mail")
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn("Подключите рабочую почту", text)
        self.assertIn('data-navigation-key="mail"', text)
        self.assertNotIn('id="mailConnectionWizard"', text)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mail_accounts").fetchone()[0], 0)
        self.assertNotIn("Почта не подключена", text)
        self.assertNotIn("Данные созданы", text)

    def test_only_owner_can_test_and_save_verified_connection(self):
        self.login(self.employee_id)
        denied = self.client.post("/api/v1/mail/settings", json=self.account_payload(), headers=self.headers)
        self.assertEqual(denied.status_code, 403)
        denied_test = self.client.post("/api/v1/mail/settings/test", json=self.account_payload(), headers=self.headers)
        self.assertEqual(denied_test.status_code, 403)
        self.login(self.owner_id)
        owner_page = self.client.get("/app/mail").get_data(as_text=True)
        self.assertIn('id="mailConnectionWizard"', owner_page)
        unverified = self.client.post("/api/v1/mail/settings", json=self.account_payload(), headers=self.headers)
        self.assertEqual(unverified.status_code, 422)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mail_accounts").fetchone()[0], 0)
        saved = self.client.post("/api/v1/mail/settings", json=self.verified_payload(), headers=self.headers)
        self.assertEqual(saved.status_code, 200)
        body = saved.get_data(as_text=True)
        self.assertNotIn("app-password", body)
        self.assertNotIn("encrypted_password", body)
        with self.store.connect() as connection:
            encrypted = connection.execute("SELECT encrypted_password FROM mail_accounts").fetchone()[0]
            enabled = connection.execute("SELECT enabled FROM mail_accounts").fetchone()[0]
            pending = connection.execute("SELECT COUNT(*) FROM mail_sync_requests WHERE state='pending'").fetchone()[0]
        self.assertNotIn("app-password", encrypted)
        self.assertEqual((enabled, pending), (1, 1))

    def test_failed_check_does_not_activate_or_persist_account(self):
        self.login(self.owner_id)
        FakeConnectionTransport.connected = False
        with patch.object(web, "MailTransport", FakeConnectionTransport):
            response = self.client.post(
                "/api/v1/mail/settings/test", json=self.account_payload(), headers=self.headers
            )
        self.assertEqual(response.status_code, 422)
        fields = response.get_json()["fields"]
        self.assertFalse(fields["imap"]["connected"])
        self.assertFalse(fields["smtp"]["connected"])
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mail_accounts").fetchone()[0], 0)

    def test_connection_check_authenticates_both_protocols_without_sending(self):
        self.login(self.owner_id)
        with patch.object(web, "MailTransport", FakeConnectionTransport):
            response = self.client.post(
                "/api/v1/mail/settings/test", json=self.account_payload(), headers=self.headers
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeConnectionTransport.checks, 1)
        data = response.get_json()["data"]
        self.assertTrue(data["imap"]["connected"])
        self.assertTrue(data["smtp"]["connected"])
        self.assertNotIn("app-password", response.get_data(as_text=True))

    def test_repeated_verified_save_updates_single_account(self):
        self.login(self.owner_id)
        payload = self.verified_payload()
        first = self.client.post("/api/v1/mail/settings", json=payload, headers=self.headers)
        second = self.client.post("/api/v1/mail/settings", json=payload, headers=self.headers)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mail_accounts").fetchone()[0], 1)

    def test_saved_password_is_never_returned_to_browser(self):
        self.login(self.owner_id)
        saved = self.client.post("/api/v1/mail/settings", json=self.verified_payload(), headers=self.headers)
        self.assertEqual(saved.status_code, 200)
        page = self.client.get("/app/mail").get_data(as_text=True)
        self.assertIn("Пароль сохранён", page)
        self.assertNotIn("app-password", page)
        self.assertNotIn("encrypted_password", page)

    def test_mail_script_disables_generic_mutation_toasts(self):
        script = (Path(web.PROJECT_ROOT) / "app" / "static" / "js" / "mail.js").read_text()
        self.assertIn('headers.set("X-Vechasu-Notify", "off")', script)
        self.assertNotIn("Данные созданы", script)

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
