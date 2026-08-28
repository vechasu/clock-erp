import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash

os.environ.setdefault("SERVICE_VAULT_KEY", Fernet.generate_key().decode("ascii"))

from app import auth, web
from app.domain_schema_migrations import apply_domain_migrations
from app.services.service_vault import (
    ServiceVault,
    ServiceVaultError,
    VaultKeyError,
    validate_icon,
    validate_service_url,
)
from scripts.migrate_services_vault import apply as apply_services_migration, verify


class ServicesVaultTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.auth_path = self.root / "auth.db"
        self.services_path = self.root / "services.db"
        apply_domain_migrations(self.auth_path, "auth", "test")
        apply_services_migration(self.services_path)
        self.key = Fernet.generate_key().decode("ascii")
        self.environment = mock.patch.dict(os.environ, {"SERVICE_VAULT_KEY": self.key})
        self.environment.start()
        self.original_config = dict(web.app.config)
        web.app.config.update(
            TESTING=True, AUTH_TESTING=True, SESSION_COOKIE_SECURE=False,
            AUTH_DATABASE=str(self.auth_path), SERVICES_DATABASE=str(self.services_path),
        )
        web.app.extensions["auth_stores"] = {str(self.auth_path): auth.AuthStore(self.auth_path)}
        self.store = web.app.extensions["auth_stores"][str(self.auth_path)]
        self.owner_id = self._user("owner@example.com", "admin")
        self.employee_id = self._user("employee@example.com", "employee")
        self.client = web.app.test_client()
        self.audit = mock.patch.object(web, "_record_service_audit")
        self.audit_mock = self.audit.start()

    def tearDown(self):
        self.audit.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temporary.cleanup()

    def _user(self, email, role):
        now = int(time.time())
        with self.store.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO users(first_name,last_name,email,email_normalized,password_hash,role,active,"
                "created_at,email_verified_at,updated_at,session_version) VALUES(?,?,?,?,?,?,1,?,?,?,1)",
                ("Test", role, email, email, generate_password_hash("not-used", method=auth.PASSWORD_HASH_METHOD), role, now, now, now),
            )
            return cursor.lastrowid

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["session_version"] = 1
            session["_csrf_token"] = "services-csrf"

    def payload(self, password="correct horse battery", permissions=None):
        return {
            "name": "Delivery Portal",
            "url": "https://delivery.example/path",
            "description": "Рабочая доставка",
            "category": "delivery",
            "icon": "truck",
            "favorite": True,
            "accounts": [
                {"label": "Основной", "login": "employee-login", "password": password},
                {"label": "Владелец", "login": "owner-login", "password": "owner-secret"},
            ],
            "permissions": permissions if permissions is not None else [{
                "user_id": self.employee_id, "can_view": True, "can_open": True,
                "can_view_login": True, "can_copy_login": True,
                "can_view_password": False, "can_copy_password": False,
                "can_edit": False, "can_manage_access": False, "can_archive": False,
            }],
        }

    def create(self, payload=None):
        self.login(self.owner_id)
        return self.client.post(
            "/api/services", json=payload or self.payload(),
            headers={"X-CSRF-Token": "services-csrf"},
        )

    def test_owner_creates_multiple_accounts_without_plaintext_exposure(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        service_id = response.get_json()["id"]
        listing = self.client.get("/api/services")
        serialized = listing.get_data(as_text=True)
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn("correct horse battery", serialized)
        self.assertNotIn("employee-login", serialized)
        self.assertEqual(len(listing.get_json()["services"][0]["accounts"]), 2)
        page = self.client.get("/app/services")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"correct horse battery", page.data)
        with sqlite3.connect(str(self.services_path)) as connection:
            blobs = connection.execute(
                "SELECT login_encrypted,password_encrypted FROM service_accounts"
            ).fetchall()
            database_bytes = b" ".join(bytes(value) for row in blobs for value in row if value)
        self.assertNotIn(b"employee-login", database_bytes)
        self.assertNotIn(b"correct horse battery", database_bytes)
        audit_text = repr(self.audit_mock.call_args_list)
        self.assertNotIn("employee-login", audit_text)
        self.assertNotIn("correct horse battery", audit_text)
        self.assertTrue(service_id)

    def test_employee_access_is_checked_for_every_credential_request(self):
        self.create()
        owner_listing = self.client.get("/api/services").get_json()["services"][0]
        account_id = owner_listing["accounts"][0]["id"]
        self.login(self.employee_id)
        self.assertEqual(self.client.get("/api/services").status_code, 200)
        login = self.client.get("/api/service-accounts/{}/login".format(account_id))
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.get_json()["value"], "employee-login")
        self.assertIn("no-store", login.headers["Cache-Control"])
        password = self.client.get("/api/service-accounts/{}/password".format(account_id))
        self.assertEqual(password.status_code, 403)
        self.assertNotIn("correct horse battery", password.get_data(as_text=True))

        copy_only = self.payload()
        copy_only["permissions"][0].update({
            "can_view_login": False, "can_copy_login": True,
            "can_view_password": False, "can_copy_password": True,
        })
        self.login(self.owner_id)
        service_id = owner_listing["id"]
        copy_only["version"] = owner_listing["version"]
        for source, existing in zip(copy_only["accounts"], owner_listing["accounts"]):
            source["id"] = existing["id"]
            source["login"] = ""
            source["password"] = ""
        self.assertEqual(self.client.put(
            "/api/services/{}".format(service_id), json=copy_only,
            headers={"X-CSRF-Token": "services-csrf"},
        ).status_code, 200)
        self.login(self.employee_id)
        self.assertEqual(self.client.get(
            "/api/service-accounts/{}/password".format(account_id)
        ).status_code, 403)
        copied = self.client.post(
            "/api/service-accounts/{}/copied".format(account_id),
            json={"kind": "password"}, headers={"X-CSRF-Token": "services-csrf"},
        )
        self.assertEqual(copied.status_code, 200)
        self.assertEqual(copied.get_json()["value"], "correct horse battery")
        self.assertIn("no-store", copied.headers["Cache-Control"])

    def test_owner_edits_archives_restores_and_conflict_is_detected(self):
        service_id = self.create().get_json()["id"]
        item = self.client.get("/api/services").get_json()["services"][0]
        payload = self.payload(password="")
        payload.update({"name": "Delivery Updated", "version": item["version"]})
        payload["accounts"][0]["id"] = item["accounts"][0]["id"]
        payload["accounts"][1]["id"] = item["accounts"][1]["id"]
        response = self.client.put(
            "/api/services/{}".format(service_id), json=payload,
            headers={"X-CSRF-Token": "services-csrf"},
        )
        self.assertEqual(response.status_code, 200)
        stale = self.client.put(
            "/api/services/{}".format(service_id), json=payload,
            headers={"X-CSRF-Token": "services-csrf"},
        )
        self.assertEqual(stale.status_code, 409)
        archived = self.client.post(
            "/api/services/{}/archive".format(service_id), json={"archived": True},
            headers={"X-CSRF-Token": "services-csrf"},
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(self.client.get("/api/services").get_json()["services"], [])
        self.assertEqual(len(self.client.get("/api/services?archived=1").get_json()["services"]), 1)
        restored = self.client.post(
            "/api/services/{}/archive".format(service_id), json={"archived": False},
            headers={"X-CSRF-Token": "services-csrf"},
        )
        self.assertEqual(restored.status_code, 200)

    def test_csrf_auth_url_and_icon_validation(self):
        unauthenticated = web.app.test_client().post("/api/services", json=self.payload())
        self.assertEqual(unauthenticated.status_code, 401)
        self.login(self.owner_id)
        no_csrf = self.client.post("/api/services", json=self.payload())
        self.assertIn(no_csrf.status_code, (400, 403))
        unsafe = self.payload()
        unsafe["url"] = "javascript:alert(1)"
        rejected = self.client.post(
            "/api/services", json=unsafe, headers={"X-CSRF-Token": "services-csrf"}
        )
        self.assertEqual(rejected.status_code, 400)
        for value in ("javascript:alert(1)", "data:text/html,x", "file:///tmp/x"):
            with self.assertRaises(ServiceVaultError):
                validate_service_url(value)
        png, mime = validate_icon(b"\x89PNG\r\n\x1a\n" + b"x" * 20, "image/png")
        self.assertEqual(mime, "image/png")
        with self.assertRaises(ServiceVaultError):
            validate_icon(b"<svg></svg>", "image/svg+xml")

    def test_favorites_and_order_are_isolated_per_user(self):
        first = self.create().get_json()["id"]
        second_payload = self.payload(password="another-secret")
        second_payload["name"] = "Second"
        second = self.create(second_payload).get_json()["id"]
        self.client.post(
            "/api/services/{}/favorite".format(first), json={"favorite": False},
            headers={"X-CSRF-Token": "services-csrf"},
        )
        self.client.post(
            "/api/services/reorder", json={"ordered_ids": [second, first]},
            headers={"X-CSRF-Token": "services-csrf"},
        )
        owner = self.client.get("/api/services").get_json()["services"]
        self.login(self.employee_id)
        employee = self.client.get("/api/services").get_json()["services"]
        self.assertNotEqual([item["id"] for item in owner], [item["id"] for item in employee])
        self.assertFalse(owner[-1]["favorite"])
        self.assertFalse(employee[0]["favorite"])

    def test_missing_and_wrong_key_fail_without_secret_in_error(self):
        with mock.patch.dict(os.environ, {"SERVICE_VAULT_KEY": ""}):
            with self.assertRaises(VaultKeyError) as missing:
                ServiceVault(self.services_path)
        self.assertNotIn(self.key, str(missing.exception))
        vault = ServiceVault(self.services_path, self.key)
        service_id = vault.create(self.payload(), {"id": self.owner_id, "role": "admin"})
        account_id = vault.list_services({"id": self.owner_id, "role": "admin"})[0]["accounts"][0]["id"]
        wrong = Fernet.generate_key().decode("ascii")
        other = ServiceVault(self.services_path, wrong)
        with self.assertRaises(VaultKeyError):
            other.credential(account_id, {"id": self.owner_id, "role": "admin"}, "password")
        self.assertTrue(service_id)

    def test_migration_is_idempotent_and_sqlite_legacy_safe(self):
        first = apply_services_migration(self.services_path)
        second = apply_services_migration(self.services_path)
        self.assertEqual(first, second)
        self.assertEqual(verify(self.services_path), first)
        source = Path("scripts/migrate_services_vault.py").read_text(encoding="utf-8").upper()
        for forbidden in (" RETURNING ", " ON CONFLICT ", " JSON_EXTRACT", " WHERE ARCHIVED_AT IS NULL"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
