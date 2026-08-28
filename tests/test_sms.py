import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from app.clients.smsbliss import (
    SmsBlissClient,
    SmsBlissInvalidResponse,
    SmsBlissNotConfigured,
    SmsBlissSecurityError,
    SmsBlissUnavailable,
    SmsBlissUnknownDelivery,
)
from app.services.sms import (
    SmsService,
    SmsStore,
    SmsValidationError,
    internal_status,
    normalize_phone,
    render_template_text,
    sms_segments,
)
from app.sms_migrations import migrate_database, verify_database


class FakeProvider:
    configured = True
    masked_login = "u***r"
    queue_name = "erpQueue"

    def __init__(self, send_status="accepted", error=None):
        self.send_status = send_status
        self.error = error
        self.calls = 0

    def send(self, client_message_id, phone, text, sender="", scheduled_at=""):
        self.calls += 1
        if self.error:
            raise self.error("provider failure")
        return {"status": "ok", "messages": [{
            "clientId": client_message_id,
            "smscId": "ABC" + client_message_id[-24:],
            "status": self.send_status,
            "smsCount": 2,
            "msgCost": "5.40",
        }]}

    def statuses(self, messages):
        return {"status": "ok", "messages": [{
            "clientId": messages[0]["clientId"],
            "smscId": messages[0]["smscId"],
            "status": "delivered",
        }]}


class FakeResponse:
    def __init__(self, payload=None, status=200, json_error=False):
        self.payload = payload
        self.status_code = status
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("rejected")

    def json(self):
        if self.json_error:
            raise ValueError("invalid")
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.last_url = ""
        self.last_json = None

    def post(self, url, json=None, timeout=None):
        self.last_url, self.last_json = url, json
        if self.error:
            raise self.error("network")
        return self.response


class SmsDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sms.db"
        migrate_database(self.path)
        self.store = SmsStore(self.path)
        self.actor = {"id": "17", "name": "Мария Иванова"}

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, client_id="msg-1", **values):
        result = {
            "client_message_id": client_id,
            "phone": "8 (999) 123-45-67",
            "text": "Ваш заказ готов",
            "sender": "Tictactoy",
            "customer_id": 12,
            "customer_name": "Клиент",
            "order_id": "551",
            "order_number": "551",
        }
        result.update(values)
        return result

    def test_migration_is_repeatable_and_quick_check_passes(self):
        migrate_database(self.path)
        verify_database(self.path)
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sms_templates").fetchone()[0], 5)

    def test_schema_avoids_post_sqlite_3717_features(self):
        source = (Path(__file__).parents[1] / "app" / "sms_migrations.py").read_text(encoding="utf-8").upper()
        for forbidden in (" ON CONFLICT ", " RETURNING ", " WITHOUT ROWID ", " STRICT "):
            self.assertNotIn(forbidden, source)
        self.assertNotRegex(source, r"CREATE\s+(UNIQUE\s+)?INDEX[^;]+\sWHERE\s")

    def test_phone_normalization_e164(self):
        self.assertEqual(normalize_phone("8 (999) 123-45-67"), "+79991234567")
        self.assertEqual(normalize_phone("9991234567"), "+79991234567")
        self.assertEqual(normalize_phone("+44 20 7946 0958"), "+442079460958")
        with self.assertRaises(SmsValidationError):
            normalize_phone("123")

    def test_gsm_and_unicode_segmentation(self):
        self.assertEqual(sms_segments("A" * 160)["segments"], 1)
        self.assertEqual(sms_segments("A" * 161)["segments"], 2)
        self.assertEqual(sms_segments("{" * 81)["segments"], 2)
        self.assertEqual(sms_segments("Я" * 70)["segments"], 1)
        self.assertEqual(sms_segments("Я" * 71)["segments"], 2)
        self.assertEqual(sms_segments("line\nline")["encoding"], "GSM-7")

    def test_template_variables_and_unknown_variable(self):
        text = render_template_text(
            "{client_name}: заказ {order_number}, {amount}; ремонт {repair_number}",
            {"client_name": "Анна", "order_number": "7", "amount": "100 ₽", "repair_number": "R-1"},
        )
        self.assertEqual(text, "Анна: заказ 7, 100 ₽; ремонт R-1")
        with self.assertRaises(SmsValidationError):
            render_template_text("{password}", {})

    def test_accepted_response_persists_provider_truth_and_author(self):
        message, submitted = SmsService(self.store, FakeProvider()).send(self.payload(), self.actor)
        self.assertTrue(submitted)
        self.assertEqual(message["status"], "accepted")
        self.assertEqual(message["smsc_id"], "ABCmsg-1")
        self.assertEqual(message["segments"], 2)
        self.assertEqual(message["cost"], "5.40")
        self.assertEqual(message["created_by_id"], "17")
        self.assertEqual(message["sent_by_name"], "Мария Иванова")

    def test_provider_rejections_have_failed_status(self):
        for index, status in enumerate(("not enough balance", "invalid mobile phone", "sender address invalid"), 1):
            message, _ = SmsService(self.store, FakeProvider(status)).send(self.payload("reject-{}".format(index)), self.actor)
            self.assertEqual(message["status"], "failed")
            self.assertTrue(message["error_description"])

    def test_timeout_invalid_json_and_unavailable_become_unknown_without_retry(self):
        for index, error in enumerate((SmsBlissUnknownDelivery, SmsBlissInvalidResponse, SmsBlissUnavailable), 1):
            provider = FakeProvider(error=error)
            message, _ = SmsService(self.store, provider).send(self.payload("unknown-{}".format(index)), self.actor)
            self.assertEqual(message["status"], "unknown")
            duplicate, submitted = SmsService(self.store, provider).send(self.payload("unknown-{}".format(index)), self.actor)
            self.assertFalse(submitted)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(duplicate["id"], message["id"])

    def test_double_post_and_double_click_are_idempotent(self):
        provider = FakeProvider()
        service = SmsService(self.store, provider)
        first, first_submitted = service.send(self.payload(), self.actor)
        second, second_submitted = service.send(self.payload(), self.actor)
        self.assertTrue(first_submitted)
        self.assertFalse(second_submitted)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(provider.calls, 1)

    def test_delivered_status_sync_is_terminal(self):
        provider = FakeProvider()
        message, _ = SmsService(self.store, provider).send(self.payload(), self.actor)
        result = SmsService(self.store, provider).sync_statuses()
        self.assertEqual(result, {"checked": 1, "updated": 1})
        self.assertEqual(self.store.get(message_id=message["id"])["status"], "delivered")
        self.assertEqual(self.store.pending(), [])

    def test_filters_pagination_and_links(self):
        for index in range(25):
            SmsService(self.store, FakeProvider()).send(self.payload(
                "filter-{}".format(index), text="Заказ готов {}".format(index),
                customer_id=99, order_id="ORDER-9", order_number="ORDER-9",
            ), self.actor)
        listing = self.store.list({"q": "ORDER-9", "status": "accepted", "customer_id": 99, "page": 2, "per_page": 20})
        self.assertEqual(listing["total"], 25)
        self.assertEqual(listing["page"], 2)
        self.assertEqual(len(listing["rows"]), 5)

    def test_template_delete_deactivates_used_template(self):
        template = self.store.templates(active_only=True)[0]
        SmsService(self.store, FakeProvider()).send(self.payload(template_id=template["id"]), self.actor)
        self.assertFalse(self.store.delete_template(template["id"]))
        row = next(item for item in self.store.templates() if item["id"] == template["id"])
        self.assertEqual(row["active"], 0)


class SmsBlissClientTests(unittest.TestCase):
    def test_requires_credentials_and_https(self):
        with self.assertRaises(SmsBlissSecurityError):
            SmsBlissClient(login="x", password="y", base_url="http://api.smsbliss.net/messages/v2")
        client = SmsBlissClient(login="", password="", base_url="https://api.smsbliss.net/messages/v2")
        with self.assertRaises(SmsBlissNotConfigured):
            client.version()

    def test_server_posts_json_to_https_and_parses_success(self):
        session = FakeSession(FakeResponse({"status": "ok", "version": 2}))
        client = SmsBlissClient("login", "password", "https://api.smsbliss.net/messages/v2", session=session)
        self.assertEqual(client.version()["version"], 2)
        self.assertTrue(session.last_url.startswith("https://"))
        self.assertEqual(session.last_json["login"], "login")

    def test_timeout_and_corrupt_json_are_classified(self):
        timeout = SmsBlissClient("a", "b", session=FakeSession(error=requests.Timeout))
        with self.assertRaises(SmsBlissUnknownDelivery):
            timeout.send("id", "+79991234567", "text")
        corrupt = SmsBlissClient("a", "b", session=FakeSession(FakeResponse(json_error=True)))
        with self.assertRaises(SmsBlissInvalidResponse):
            corrupt.balance()


class SmsWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        os.environ["CATALOG_DATABASE_PATH"] = str(root / "catalog.db")
        os.environ["ERP_AUTH_DATABASE"] = str(root / "auth.db")
        os.environ["ERP_SMS_DATABASE"] = str(root / "sms-global.db")
        from app.schema_migrations import apply_migrations
        from app.domain_schema_migrations import apply_domain_migrations
        apply_migrations(root / "catalog.db", app_commit="sms-test")
        apply_domain_migrations(root / "auth.db", "auth", "sms-test")
        migrate_database(root / "sms-global.db")

    @classmethod
    def tearDownClass(cls):
        cls.runtime.cleanup()

    def setUp(self):
        from app import web
        self.web = web
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sms.db"
        migrate_database(self.path)
        web.app.config.update(TESTING=True, AUTH_TESTING=False, SMS_DATABASE=str(self.path))
        self.client = web.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_page_is_available_without_credentials_and_contains_no_secrets(self):
        with mock.patch.dict(os.environ, {
            "SMSBLISS_LOGIN": "", "SMSBLISS_PASSWORD": "",
            "SMSBLISS_API_BASE_URL": "https://api.smsbliss.net/messages/v2",
        }, clear=False):
            response = self.client.get("/app/sms")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Центр SMS".encode("utf-8"), response.data)
        self.assertNotIn(b"actual-provider-password", response.data.lower())
        self.assertIn(b"max-width: 720px", (Path(__file__).parents[1] / "app/static/css/sms.css").read_bytes())

    def test_send_endpoint_uses_session_actor_and_is_idempotent(self):
        provider = FakeProvider()
        payload = {
            "client_message_id": "web-idempotent",
            "phone": "+79991234567",
            "text": "Сервисное сообщение",
        }
        with mock.patch.object(self.web, "sms_client", return_value=provider):
            first = self.client.post("/api/v1/sms/messages", json=payload)
            second = self.client.post("/api/v1/sms/messages", json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.get_json()["meta"]["duplicate"])
        self.assertTrue(second.get_json()["meta"]["duplicate"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(SmsStore(self.path).get(client_message_id="web-idempotent")["created_by_id"], "system")

    def test_missing_credentials_do_not_create_message(self):
        provider = FakeProvider()
        provider.configured = False
        with mock.patch.object(self.web, "sms_client", return_value=provider):
            response = self.client.post("/api/v1/sms/messages", json={
                "client_message_id": "no-creds", "phone": "+79991234567", "text": "test",
            })
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(SmsStore(self.path).get(client_message_id="no-creds"))

    def test_role_permissions_are_separate(self):
        with self.web.app.test_request_context("/app/sms"), mock.patch.object(self.web, "auth_is_enabled", return_value=True):
            employee = self.web.sms_permissions({"role": "employee"})
            owner = self.web.sms_permissions({"role": "admin"})
        self.assertTrue(employee["view"] and employee["send"])
        self.assertFalse(employee["manage_templates"] or employee["view_integration"])
        self.assertTrue(all(owner.values()))

    def test_response_does_not_expose_provider_credentials(self):
        store = SmsStore(self.path)
        message, _ = SmsService(store, FakeProvider()).send({
            "client_message_id": "detail-safe", "phone": "+79991234567", "text": "Не секрет",
        }, {"id": "1", "name": "Сотрудник"})
        response = self.client.get("/api/v1/sms/messages/{}".format(message["id"]))
        body = json.dumps(response.get_json(), ensure_ascii=False)
        self.assertNotIn("SMSBLISS_PASSWORD", body)
        self.assertNotIn("ABC123-password", body)


if __name__ == "__main__":
    unittest.main()
