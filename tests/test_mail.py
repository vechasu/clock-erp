import base64
import os
import socket
import sqlite3
import tempfile
import unittest
from email.header import Header
from email.message import EmailMessage
from pathlib import Path

from app.mail_migrations import migrate_database, validate_database
from app.services.mail import (
    MailStore, MailSynchronizer, MailTransport, MailValidationError, SecretBox,
    parse_addresses, parse_message, sanitize_html,
    validated_connection_settings,
)


KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


def raw_message(message_id="<one@test>", reply_to="", subject="Привет", html=False,
                attachment=False, sender="Иван <ivan@example.test>"):
    message = EmailMessage()
    message["Message-ID"] = message_id
    message["From"] = sender
    message["To"] = "erp@example.test"
    message["Subject"] = str(Header(subject, "utf-8"))
    message["Date"] = "Fri, 28 Aug 2026 12:00:00 +0300"
    if reply_to:
        message["In-Reply-To"] = reply_to
        message["References"] = reply_to
    message.set_content("Текст письма")
    if html:
        message.add_alternative(
            '<p>Безопасно</p><script>alert(1)</script><img src="https://tracker.test/pixel">',
            subtype="html",
        )
    if attachment:
        message.add_attachment("данные".encode("utf-8"), maintype="text", subtype="plain",
                               filename="отчёт.txt")
    return message.as_bytes()


class FakeSMTP:
    def __init__(self, fail=None):
        self.fail = fail
        self.messages = []

    def send_message(self, message, to_addrs=None):
        if self.fail:
            raise self.fail
        self.messages.append((message, to_addrs))

    def noop(self):
        return 250, b"OK"

    def quit(self):
        pass


class FakeIMAPAppend:
    def append(self, *args):
        return "OK", []

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)
        return "OK", [b"0"]

    def logout(self):
        pass


class FakeTransport:
    smtp_client = FakeSMTP()

    def __init__(self, account, password, timeout=15):
        self.account = account
        self.password = password

    def smtp(self):
        return self.smtp_client

    def imap(self):
        return FakeIMAPAppend()


class MailServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "mail.db"
        self.attachments = self.root / "attachments"
        migrate_database(self.db)
        self.store = MailStore(self.db, self.attachments)
        self.box = SecretBox(KEY)
        self.account = self.store.save_account({
            "mailbox_name": "Общий", "sender_name": "ERP", "email": "erp@example.test",
            "imap_host": "imap.example.test", "imap_port": 993,
            "smtp_host": "smtp.example.test", "smtp_port": 465,
            "security": "ssl", "login": "erp@example.test", "password": "app-password",
        }, 1, self.box)

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_is_repeatable_and_verified(self):
        migrate_database(self.db)
        self.assertIn("mail-v2", validate_database(self.db))

    def test_database_is_private_after_creation_and_repeat_migration(self):
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        os.chmod(str(self.db), 0o644)
        migrate_database(self.db)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_legacy_security_is_preserved_by_v2_migration(self):
        legacy = self.root / "legacy-mail.db"
        with sqlite3.connect(str(legacy)) as connection:
            connection.execute("CREATE TABLE mail_schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            connection.execute("INSERT INTO mail_schema_meta VALUES('schema_version','mail-v1-2026-08-28')")
            connection.execute("CREATE TABLE mail_accounts(id INTEGER PRIMARY KEY,security TEXT NOT NULL)")
            connection.execute("INSERT INTO mail_accounts(id,security) VALUES(1,'starttls')")
        migrate_database(legacy)
        with sqlite3.connect(str(legacy)) as connection:
            row = connection.execute(
                "SELECT imap_security,smtp_security FROM mail_accounts WHERE id=1"
            ).fetchone()
        self.assertEqual(row, ("starttls", "starttls"))

    def test_secret_is_authenticated_and_not_plaintext(self):
        token = self.box.encrypt("секрет")
        self.assertNotIn("секрет", token)
        self.assertEqual(self.box.decrypt(token), "секрет")
        with self.assertRaises(Exception):
            SecretBox(base64.urlsafe_b64encode(b"x" * 32).decode("ascii")).decrypt(token)

    def test_html_is_sanitized_and_tracking_image_blocked(self):
        parsed = parse_message(raw_message(html=True))
        self.assertNotIn("<script", parsed["html_body"])
        self.assertNotIn("<img src=", parsed["html_body"])
        self.assertIn("data-external-image", parsed["html_body"])
        self.assertTrue(parsed["external_images"])

    def test_show_images_keeps_no_referrer_and_still_removes_script(self):
        value, external = sanitize_html('<script>x</script><img src="https://x.test/a">', True)
        self.assertTrue(external)
        self.assertIn('referrerpolicy="no-referrer"', value)
        self.assertNotIn("script", value)

    def test_russian_header_and_attachment_are_parsed(self):
        parsed = parse_message(raw_message(subject="Русская тема", attachment=True))
        self.assertEqual(parsed["subject"], "Русская тема")
        self.assertEqual(parsed["attachments"][0]["name"], "отчёт.txt")

    def test_header_injection_and_bad_address_are_rejected(self):
        with self.assertRaises(MailValidationError):
            parse_addresses("victim@example.test\r\nBcc: attacker@example.test")
        with self.assertRaises(MailValidationError):
            parse_addresses("invalid")

    def test_connection_settings_require_tls_valid_ports_and_safe_values(self):
        payload = {
            "mailbox_name": "Общий", "email": "erp@example.test",
            "imap_host": "imap.example.test", "imap_port": 993,
            "imap_security": "ssl", "smtp_host": "smtp.example.test",
            "smtp_port": 587, "smtp_security": "starttls",
            "login": "erp@example.test",
        }
        clean = validated_connection_settings(payload)
        self.assertEqual((clean["imap_security"], clean["smtp_security"]), ("ssl", "starttls"))
        for name, value in (("smtp_port", 0), ("imap_security", "plain"), ("imap_host", "imap.test\nHeader")):
            invalid = dict(payload, **{name: value})
            with self.assertRaises(MailValidationError):
                validated_connection_settings(invalid)

    def test_transport_check_reads_inbox_and_authenticates_smtp_without_send(self):
        imap = FakeIMAPAppend()
        smtp = FakeSMTP()
        transport = MailTransport({
            "imap_host": "imap.example.test", "imap_port": 993,
            "imap_security": "ssl", "smtp_host": "smtp.example.test",
            "smtp_port": 465, "smtp_security": "ssl",
            "login": "erp@example.test",
        }, "secret")
        transport.imap = lambda: imap
        transport.smtp = lambda: smtp
        result = transport.check()
        self.assertTrue(result["connected"])
        self.assertEqual(imap.selected, ("INBOX", True))
        self.assertEqual(smtp.messages, [])

    def test_ingest_is_idempotent_and_stores_attachment_outside_public(self):
        parsed = parse_message(raw_message(attachment=True))
        thread_id, created = self.store.ingest(self.account["id"], "inbox", "INBOX", "1", 1, parsed)
        repeated_id, repeated = self.store.ingest(self.account["id"], "inbox", "INBOX", "1", 1, parsed)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(thread_id, repeated_id)
        thread = self.store.get_thread(thread_id)
        self.assertEqual(thread["message_count"], 1)
        self.assertEqual(thread["unread_count"], 1)
        self.assertTrue(next(self.attachments.iterdir()).is_file())

    def test_threads_use_reply_headers_not_subject_only(self):
        first, unused = self.store.ingest(self.account["id"], "inbox", "INBOX", "1", 1,
                                          parse_message(raw_message("<parent@test>", subject="Одинаково")))
        reply, unused = self.store.ingest(self.account["id"], "inbox", "INBOX", "1", 2,
                                          parse_message(raw_message("<reply@test>", "<parent@test>", "Другая тема")))
        separate, unused = self.store.ingest(self.account["id"], "inbox", "INBOX", "1", 3,
                                             parse_message(raw_message("<other@test>", subject="Одинаково")))
        self.assertEqual(first, reply)
        self.assertNotEqual(first, separate)

    def test_uidvalidity_change_accepts_new_remote_message(self):
        first, created = self.store.ingest(self.account["id"], "inbox", "INBOX", "1", 7,
                                           parse_message(raw_message("<uid-one@test>")))
        second, created_again = self.store.ingest(self.account["id"], "inbox", "INBOX", "2", 7,
                                                  parse_message(raw_message("<uid-two@test>")))
        self.assertTrue(created and created_again)
        self.assertNotEqual(first, second)

    def test_queue_is_idempotent_and_attachment_is_private(self):
        payload = {"to": "client@example.test", "subject": "Тема", "text_body": "Текст",
                   "attachments": [{"name": "файл.txt", "content_type": "text/plain",
                                    "data": base64.b64encode(b"data").decode("ascii")}]}
        first, created = self.store.queue_outbox(payload, 2, "once")
        second, repeated = self.store.queue_outbox(payload, 2, "once")
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(list(self.attachments.iterdir())), 1)

    def test_delivery_records_personal_author_and_sent_message(self):
        FakeTransport.smtp_client = FakeSMTP()
        self.store.queue_outbox({"to": "client@example.test", "subject": "Ответ",
                                 "text_body": "Готово"}, 7, "delivery")
        result = MailSynchronizer(self.store, self.box, FakeTransport).deliver()
        self.assertEqual(result["sent"], 1)
        listing = self.store.list_threads({"view": "sent"}, 7)
        thread = self.store.get_thread(listing["rows"][0]["id"])
        self.assertEqual(thread["messages"][0]["erp_author_id"], 7)

    def test_ambiguous_timeout_is_not_retried_or_marked_failed(self):
        FakeTransport.smtp_client = FakeSMTP(socket.timeout())
        item, unused = self.store.queue_outbox({"to": "client@example.test", "subject": "Ответ",
                                                "text_body": "Готово"}, 8, "unknown")
        result = MailSynchronizer(self.store, self.box, FakeTransport).deliver()
        self.assertEqual(result["unknown"], 1)
        with self.store.connect() as connection:
            state = connection.execute("SELECT state FROM mail_outbox WHERE id=?", (item["id"],)).fetchone()[0]
        self.assertEqual(state, "unknown")

    def test_disabled_account_is_not_synchronized(self):
        self.store.disable(1)

        class ForbiddenTransport:
            def __init__(self, *args, **kwargs):
                raise AssertionError("disabled account must not connect")

        result = MailSynchronizer(self.store, self.box, ForbiddenTransport).sync()
        self.assertEqual(result, {"accounts": 0, "messages": 0, "threads": 0})


if __name__ == "__main__":
    unittest.main()
