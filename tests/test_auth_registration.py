import hashlib
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash

from app import auth
from app import web
from app.domain_schema_migrations import apply_domain_migrations


PASSWORD = "correct horse battery"


class AuthHardeningTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "auth.db"
        self.outbox = []
        web.app.config.update(
            TESTING=True,
            AUTH_TESTING=True,
            AUTH_DATABASE=str(self.database_path),
            SESSION_COOKIE_SECURE=False,
            AUTH_EMAIL_OUTBOX=self.outbox,
            AUTH_RATE_LIMIT_WINDOW_SECONDS=900,
            LOGIN_EMAIL_RATE_LIMIT=50,
            LOGIN_IP_RATE_LIMIT=100,
            LOGIN_GLOBAL_RATE_LIMIT=1000,
            REGISTRATION_EMAIL_RATE_LIMIT=50,
            REGISTRATION_IP_RATE_LIMIT=100,
            REGISTRATION_GLOBAL_RATE_LIMIT=1000,
            INVITATION_CHECK_RATE_LIMIT=50,
            INVITATION_CHECK_IP_RATE_LIMIT=100,
            INVITATION_CHECK_GLOBAL_RATE_LIMIT=1000,
            PASSWORD_RESET_RATE_LIMIT=50,
            AUTH_IP_RATE_LIMIT=100,
            AUTH_GLOBAL_RATE_LIMIT=1000,
            AUTH_ABSOLUTE_SESSION_LIFETIME=auth.AUTH_ABSOLUTE_TIMEOUT_SECONDS,
        )
        apply_domain_migrations(self.database_path, "auth", "test")
        self.store = auth.AuthStore(self.database_path)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp_directory.cleanup()

    def csrf(self, client=None):
        client = client or self.client
        client.get("/login")
        with client.session_transaction() as session_data:
            return session_data["_csrf_token"]

    def insert_user(
        self,
        email="owner@example.com",
        password=PASSWORD,
        role="employee",
        verified=True,
        active=True,
    ):
        now = int(time.time())
        with self.store.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    first_name, last_name, email, email_normalized,
                    password_hash, role, active, created_at,
                    email_verified_at, updated_at, session_version
                ) VALUES ('', '', ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    email,
                    auth.normalize_email(email),
                    generate_password_hash(password, method=auth.PASSWORD_HASH_METHOD),
                    role,
                    1 if active else 0,
                    now,
                    now if verified else None,
                    now,
                ),
            )
        return cursor.lastrowid

    def login(self, email="owner@example.com", password=PASSWORD, client=None, **extra):
        client = client or self.client
        data = {
            "csrf_token": self.csrf(client),
            "email": email,
            "password": password,
        }
        data.update(extra)
        return client.post("/login", data=data, follow_redirects=False)

    def accept_invitation(self, token, client=None):
        client = client or self.client
        return client.post(
            "/register/invitation",
            data={"csrf_token": self.csrf(client), "invitation_token": token},
            follow_redirects=False,
        )

    def register_invited(self, token, email, client=None):
        client = client or self.client
        self.accept_invitation(token, client)
        return client.post(
            "/register",
            data={
                "csrf_token": self.csrf(client),
                "email": email,
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
            },
            follow_redirects=False,
        )

    def session_row(self, client=None):
        client = client or self.client
        sid = client.get_cookie("session").value
        with self.store.connect() as connection:
            return connection.execute(
                "SELECT * FROM auth_sessions WHERE session_hash = ?",
                (hashlib.sha256(sid.encode()).hexdigest(),),
            ).fetchone()

    def test_page_and_api_require_login(self):
        page = self.client.get("/app/settings")
        api = self.client.get("/api/v1/settings")
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["Location"].startswith("/login?next="))
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.get_json()["code"], "AUTH_REQUIRED")

    def test_correct_credentials_login_and_session_fixation(self):
        self.insert_user()
        self.client.get("/login")
        old_sid = self.client.get_cookie("session").value
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(old_sid, self.client.get_cookie("session").value)
        self.assertIsNotNone(self.session_row())

    def test_username_without_email_format_can_log_in(self):
        self.insert_user("mrv", role="admin")

        page = self.client.get("/login").get_data(as_text=True)
        self.assertIn('type="text"', page)
        self.assertIn('autocomplete="username"', page)

        response = self.login("mrv")
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(self.session_row())

    def test_login_failures_are_indistinguishable(self):
        self.insert_user("verified@example.com")
        self.insert_user("pending@example.com", verified=False)
        self.insert_user("blocked@example.com", active=False)
        cases = (
            ("missing@example.com", PASSWORD),
            ("verified@example.com", "wrong password"),
            ("pending@example.com", PASSWORD),
            ("blocked@example.com", PASSWORD),
        )
        results = []
        for email, password in cases:
            client = web.app.test_client()
            response = self.login(email, password, client)
            results.append((response.status_code, auth.LOGIN_PUBLIC_ERROR in response.get_data(as_text=True)))
        self.assertEqual(results, [(200, True)] * len(cases))

    def test_registration_without_invitation_is_forbidden(self):
        response = self.client.post(
            "/register",
            data={
                "csrf_token": self.csrf(),
                "email": "public@example.com",
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.store.user_count(), 0)

    def test_valid_invitation_registers_bound_employee_once(self):
        token = self.store.create_invitation(None, "staff@example.com", "employee", 24)
        response = self.register_invited(token, "STAFF@example.com")
        self.assertEqual(response.status_code, 302)
        user = self.store.get_user_by_email("staff@example.com")
        self.assertEqual(user["role"], "employee")
        self.assertIsNotNone(user["email_verified_at"])
        self.assertEqual(self.store.get_invitation(auth.invitation_digest(token))["status"], "used")
        other = web.app.test_client()
        self.assertEqual(self.register_invited(token, "staff@example.com", other).status_code, 403)

    def test_expired_and_revoked_invitations_are_rejected(self):
        expired = self.store.create_invitation(None, "expired@example.com", "employee", 1)
        revoked = self.store.create_invitation(None, "revoked@example.com", "employee", 1)
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE invitations SET expires_at = ? WHERE token_hash = ?",
                (int(time.time()) - 1, auth.invitation_digest(expired)),
            )
        invitation_id = self.store.get_invitation(auth.invitation_digest(revoked))["id"]
        self.store.revoke_invitation(invitation_id)
        for token in (expired, revoked):
            client = web.app.test_client()
            response = self.accept_invitation(token, client)
            self.assertEqual(response.status_code, 302)
            with client.session_transaction() as session_data:
                self.assertNotIn("_pending_invitation", session_data)

    def test_first_public_user_cannot_become_admin(self):
        self.test_registration_without_invitation_is_forbidden()
        self.assertEqual(self.store.user_count(), 0)

    def test_bootstrap_is_atomic_and_only_for_empty_database(self):
        token = self.store.create_bootstrap_invitation(24)
        invitation = self.store.get_invitation(auth.invitation_digest(token))
        self.assertEqual(invitation["role"], "admin")
        with self.assertRaises(auth.RegistrationError):
            self.store.create_bootstrap_invitation(24)
        self.register_invited(token, "owner@example.com")
        with self.assertRaises(auth.RegistrationError):
            self.store.create_bootstrap_invitation(24)
        with self.assertRaises(auth.RegistrationError):
            self.store.create_initial_admin("Owner", "Admin", "other@example.com", PASSWORD)

    def test_idle_and_absolute_session_timeouts_remove_server_session(self):
        self.insert_user()
        self.login()
        row = self.session_row()
        with self.store.connect() as connection:
            connection.execute("UPDATE auth_sessions SET expires_at = ? WHERE id = ?", (int(time.time()) - 1, row["id"]))
        self.assertEqual(self.client.get("/app/settings").status_code, 302)
        with self.store.connect() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM auth_sessions WHERE id = ?", (row["id"],)).fetchone())

        client = web.app.test_client()
        self.login(client=client)
        row = self.session_row(client)
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE auth_sessions SET created_at = ?, expires_at = ? WHERE id = ?",
                (int(time.time()) - auth.AUTH_ABSOLUTE_TIMEOUT_SECONDS - 1, int(time.time()) + 3600, row["id"]),
            )
        self.assertEqual(client.get("/app/settings").status_code, 302)
        with self.store.connect() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM auth_sessions WHERE id = ?", (row["id"],)).fetchone())

    def test_logout_and_session_version_revoke_sessions(self):
        user_id = self.insert_user()
        self.login()
        row_id = self.session_row()["id"]
        response = self.client.post("/logout", data={"csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 302)
        with self.store.connect() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM auth_sessions WHERE id = ?", (row_id,)).fetchone())

        self.login()
        with self.store.connect() as connection:
            connection.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user_id,))
        self.assertEqual(self.client.get("/app/settings").status_code, 302)

        blocked_client = web.app.test_client()
        self.login(client=blocked_client)
        with self.store.connect() as connection:
            connection.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        self.assertEqual(blocked_client.get("/app/settings").status_code, 302)

    def test_password_reset_token_is_one_time_expires_and_revokes_sessions(self):
        user_id = self.insert_user()
        old_client = web.app.test_client()
        self.login(client=old_client)
        token = self.store.create_token(user_id, auth.TOKEN_PASSWORD_RESET, 1800)
        self.assertTrue(self.store.reset_password(token, "replacement password"))
        self.assertFalse(self.store.reset_password(token, PASSWORD))
        self.assertEqual(old_client.get("/app/settings").status_code, 302)
        expired = self.store.create_token(user_id, auth.TOKEN_PASSWORD_RESET, -1)
        self.assertFalse(self.store.reset_password(expired, PASSWORD))

    def test_rate_limit_by_email_ip_and_global_uses_only_hashed_keys(self):
        web.app.config.update(LOGIN_EMAIL_RATE_LIMIT=2, LOGIN_IP_RATE_LIMIT=100, LOGIN_GLOBAL_RATE_LIMIT=100)
        for _attempt in range(2):
            self.assertEqual(self.login("target@example.com", "wrong").status_code, 200)
        self.assertEqual(self.login("target@example.com", "wrong").status_code, 429)

        with self.store.connect() as connection:
            buckets = [row[0] for row in connection.execute("SELECT bucket FROM auth_attempts")]
        self.assertTrue(all(len(bucket) == 64 for bucket in buckets))
        self.assertNotIn("target@example.com", self.database_path.read_bytes().decode("utf-8", errors="ignore"))

        other_db = Path(self.temp_directory.name) / "ip-limit.db"
        web.app.config.update(AUTH_DATABASE=str(other_db), LOGIN_EMAIL_RATE_LIMIT=100, LOGIN_IP_RATE_LIMIT=2)
        apply_domain_migrations(other_db, "auth", "test")
        self.store = auth.AuthStore(other_db)
        self.client = web.app.test_client()
        self.login("one@example.com", "wrong")
        self.login("two@example.com", "wrong")
        self.assertEqual(self.login("three@example.com", "wrong").status_code, 429)

    def test_shared_office_ip_allows_multiple_employees(self):
        for index in range(3):
            self.insert_user("employee{}@example.com".format(index))
        web.app.config.update(LOGIN_EMAIL_RATE_LIMIT=3, LOGIN_IP_RATE_LIMIT=10, LOGIN_GLOBAL_RATE_LIMIT=100)
        for index in range(3):
            client = web.app.test_client()
            response = self.login("employee{}@example.com".format(index), client=client)
            self.assertEqual(response.status_code, 302)

    def test_csrf_protects_html_and_json_writes(self):
        self.insert_user(role="admin")
        self.login()
        self.assertEqual(self.client.post("/settings", data={}).status_code, 400)
        response = self.client.patch("/api/v1/settings", json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "CSRF_INVALID")

    def test_open_redirect_is_rejected(self):
        self.insert_user()
        for target in ("https://evil.example/", "//evil.example/", "/\\evil"):
            client = web.app.test_client()
            response = self.login(client=client, next=target)
            self.assertEqual(response.headers["Location"], "/")

    def test_employee_cannot_manage_invitations(self):
        self.insert_user(role="employee")
        self.login()
        response = self.client.post(
            "/settings/invitations",
            data={"csrf_token": self.csrf(), "email": "new@example.com"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_see_create_and_revoke_invitations(self):
        self.insert_user(role="admin")
        self.login()
        page = self.client.get("/app/settings")
        self.assertIn("Приглашения сотрудников", page.get_data(as_text=True))
        created = self.client.post(
            "/settings/invitations",
            data={
                "csrf_token": self.csrf(),
                "email": "new@example.com",
                "role": "employee",
                "lifetime_hours": "24",
            },
        )
        self.assertEqual(created.status_code, 200)
        invitation = self.store.list_invitations()[0]
        revoked = self.client.post(
            "/settings/invitations/{}/revoke".format(invitation["id"]),
            data={"csrf_token": self.csrf()},
        )
        self.assertEqual(revoked.status_code, 302)
        self.assertEqual(self.store.list_invitations()[0]["status"], "revoked")

    def test_email_verification_token_is_one_time_and_expires(self):
        user_id = self.insert_user("pending@example.com", verified=False)
        token = self.store.create_token(user_id, auth.TOKEN_EMAIL_VERIFICATION, 3600)
        self.assertTrue(self.store.verify_email(token))
        self.assertFalse(self.store.verify_email(token))
        expired = self.store.create_token(user_id, auth.TOKEN_EMAIL_VERIFICATION, -1)
        self.assertFalse(self.store.verify_email(expired))

    def test_missing_external_secret_never_sends_update_request(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(web.requests, "post") as post:
            os.environ.pop("UPDATE_ORDER_STATUS_TOKEN", None)
            result = web.update_order_status(123, "A")
        self.assertEqual(result["code"], "UPDATE_ORDER_STATUS_NOT_CONFIGURED")
        post.assert_not_called()

    def test_external_status_token_comes_from_environment_and_keeps_contract(self):
        test_token = os.urandom(24).hex()
        response = mock.Mock(ok=True)
        response.json.return_value = {"status": "ok"}
        with mock.patch.dict(
            os.environ,
            {"UPDATE_ORDER_STATUS_TOKEN": test_token},
        ), mock.patch.object(
            web.requests,
            "post",
            return_value=response,
        ) as post:
            result = web.update_order_status(123, "A")

        self.assertEqual(result, {"status": "ok"})
        post.assert_called_once_with(
            web.UPDATE_ORDER_STATUS_URL,
            data={
                "token": test_token,
                "order_id": "123",
                "status": "A",
            },
            timeout=15,
        )

    def test_missing_external_secret_keeps_startup_and_order_reads_available(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"orders": []}
        web.ORDERS_CACHE.update(items=[], loaded_at=0)
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
            web.requests,
            "get",
            return_value=response,
        ) as get, mock.patch.object(web.requests, "post") as post:
            os.environ.pop("UPDATE_ORDER_STATUS_TOKEN", None)
            self.assertEqual(self.client.get("/login").status_code, 200)
            self.assertEqual(web.get_orders(force=True), [])

        get.assert_called_once_with(web.ORDERS_URL, timeout=20)
        post.assert_not_called()

    def test_external_status_token_is_not_exposed_in_errors_or_logs(self):
        test_token = os.urandom(24).hex()
        response = mock.Mock(ok=False, status_code=502)
        response.text = test_token
        with mock.patch.dict(
            os.environ,
            {"UPDATE_ORDER_STATUS_TOKEN": test_token},
        ), mock.patch.object(
            web.requests,
            "post",
            return_value=response,
        ), mock.patch("builtins.print") as printed, mock.patch.object(
            web.app.logger,
            "error",
        ) as logged:
            result = web.update_order_status(123, "A")

        self.assertNotIn(test_token, str(result))
        self.assertNotIn(test_token, str(printed.call_args_list))
        self.assertNotIn(test_token, str(logged.call_args_list))

        with mock.patch.dict(
            os.environ,
            {"UPDATE_ORDER_STATUS_TOKEN": test_token},
        ), mock.patch.object(
            web.requests,
            "post",
            side_effect=RuntimeError(test_token),
        ):
            failed = web.update_order_status(123, "A")
        self.assertNotIn(test_token, str(failed))

    def test_old_schema_migration_is_idempotent_and_preserves_user(self):
        legacy_path = Path(self.temp_directory.name) / "legacy.db"
        now = int(time.time())
        with sqlite3.connect(str(legacy_path)) as connection:
            connection.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT NOT NULL, last_name TEXT NOT NULL,
                    email TEXT NOT NULL, email_normalized TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO users (first_name,last_name,email,email_normalized,password_hash,role,active,created_at) VALUES ('A','B','legacy@example.com','legacy@example.com','hash','employee',1,?)",
                (now,),
            )
        apply_domain_migrations(legacy_path, "auth", "test")
        apply_domain_migrations(legacy_path, "auth", "test")
        with sqlite3.connect(str(legacy_path)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            count = connection.execute("SELECT COUNT(*) FROM users WHERE email_normalized = 'legacy@example.com'").fetchone()[0]
        self.assertTrue({"email_verified_at", "updated_at", "session_version", "last_login_at"}.issubset(columns))
        self.assertEqual(count, 1)

    def test_route_registry_has_no_public_business_endpoint(self):
        public = {
            rule.endpoint
            for rule in web.app.url_map.iter_rules()
            if rule.endpoint in auth.PUBLIC_ENDPOINTS
        }
        self.assertEqual(public, auth.PUBLIC_ENDPOINTS)
        self.assertTrue(
            all(
                rule.endpoint not in auth.PUBLIC_ENDPOINTS
                for rule in web.app.url_map.iter_rules()
                if rule.rule.startswith("/api/")
            )
        )

    def test_cookie_flags_are_opaque(self):
        self.insert_user()
        response = self.login()
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("owner@example.com", cookie)


if __name__ == "__main__":
    unittest.main()
