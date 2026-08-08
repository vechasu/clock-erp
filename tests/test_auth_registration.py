import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.security import check_password_hash

from app import auth
from app import web


class AuthMvpTest(unittest.TestCase):
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
            TICTACTOY_ALLOWED_EMAILS={
                "owner@example.com",
                "second@example.com",
            },
            AUTH_EMAIL_OUTBOX=self.outbox,
            LOGIN_RATE_LIMIT=50,
            REGISTRATION_RATE_LIMIT=50,
            PASSWORD_RESET_RATE_LIMIT=50,
            AUTH_OFFICE_RATE_LIMIT=500,
        )
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

    def register(self, email="owner@example.com", password="correct horse battery"):
        return self.client.post(
            "/register",
            data={
                "csrf_token": self.csrf(),
                "email": email,
                "password": password,
                "password_confirmation": password,
            },
        )

    def verify_latest(self, client=None):
        client = client or self.client
        token = self.outbox[-1]["link"].rsplit("/", 1)[-1]
        return client.get("/verify-email/" + token), token

    def login(self, email="owner@example.com", password="correct horse battery", client=None):
        client = client or self.client
        return client.post(
            "/login",
            data={
                "csrf_token": self.csrf(client),
                "email": email,
                "password": password,
            },
            follow_redirects=False,
        )

    def create_verified_user(self):
        self.register()
        self.verify_latest()

    def request_reset(self, client=None, email="owner@example.com"):
        client = client or self.client
        response = client.post(
            "/forgot-password",
            data={"csrf_token": self.csrf(client), "email": email},
        )
        token = self.outbox[-1]["link"].rsplit("/", 1)[-1]
        return response, token

    def test_allowed_email_registers_with_hash_and_first_user_is_admin(self):
        response = self.register(email="  OWNER@EXAMPLE.COM  ")
        self.assertEqual(response.status_code, 200)
        user = self.store.get_user_by_email("owner@example.com")
        self.assertEqual(user["role"], "admin")
        self.assertIsNone(user["email_verified_at"])
        self.assertTrue(check_password_hash(user["password_hash"], "correct horse battery"))
        self.assertNotIn(b"correct horse battery", self.database_path.read_bytes())

    def test_disallowed_and_duplicate_email_are_rejected(self):
        denied = self.register(email="public@example.com")
        self.assertIn("Регистрация для этого email недоступна", denied.get_data(as_text=True))
        self.register()
        duplicate = self.register(email="OWNER@example.com")
        self.assertIn("Аккаунт с таким email уже существует", duplicate.get_data(as_text=True))

    def test_unverified_cannot_login_then_verification_enables_login(self):
        self.register()
        blocked = self.login()
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("Сначала подтвердите email", blocked.get_data(as_text=True))
        verified, token = self.verify_latest()
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(self.client.get("/verify-email/" + token).status_code, 400)
        self.assertEqual(self.login().status_code, 302)

    def test_wrong_password_is_rejected(self):
        self.create_verified_user()
        response = self.login(password="wrong password")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Неверный email или пароль", response.get_data(as_text=True))

    def test_pages_redirect_and_api_returns_json_401_without_auth(self):
        page = self.client.get("/settings")
        api = self.client.get("/api/v1/settings")
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["Location"].startswith("/login?next="))
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.get_json()["code"], "AUTH_REQUIRED")

    def test_products_sales_receipts_are_available_after_login(self):
        self.create_verified_user()
        self.login()
        safe_views = {
            "warehouse_page": lambda: "products ok",
            "sales_page": lambda: "sales ok",
            "receipts_page": lambda: "receipts ok",
        }
        with mock.patch.dict(web.app.view_functions, safe_views):
            for path in ("/app/products", "/app/sales", "/app/receipts"):
                with self.subTest(path=path):
                    response = self.client.get(path, follow_redirects=False)
                    self.assertNotIn(response.status_code, (302, 401, 403))

    def test_password_reset_is_generic_one_time_and_changes_password(self):
        self.create_verified_user()
        unknown = self.client.post(
            "/forgot-password",
            data={"csrf_token": self.csrf(), "email": "unknown@example.com"},
        )
        self.assertIn("Если аккаунт с таким email существует", unknown.get_data(as_text=True))
        response, token = self.request_reset()
        self.assertEqual(response.status_code, 200)
        record = self.store.token_record(token, auth.TOKEN_PASSWORD_RESET)
        self.assertIsNotNone(record)
        self.assertNotIn(token.encode(), self.database_path.read_bytes())
        changed = self.client.post(
            "/reset-password/" + token,
            data={
                "csrf_token": self.csrf(),
                "password": "new secure password",
                "password_confirmation": "new secure password",
            },
            follow_redirects=False,
        )
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(self.client.get("/reset-password/" + token).status_code, 400)
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.login(password="new secure password").status_code, 302)

    def test_expired_reset_token_does_not_work(self):
        self.create_verified_user()
        _response, token = self.request_reset()
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE auth_tokens SET expires_at = ? WHERE token_hash = ?",
                (int(time.time()) - 1, auth.token_digest(token)),
            )
        self.assertEqual(self.client.get("/reset-password/" + token).status_code, 400)

    def test_new_reset_link_invalidates_previous_link(self):
        self.create_verified_user()
        _response, old_token = self.request_reset()
        _response, new_token = self.request_reset()
        self.assertEqual(self.client.get("/reset-password/" + old_token).status_code, 400)
        self.assertEqual(self.client.get("/reset-password/" + new_token).status_code, 200)

    def test_password_change_invalidates_old_sessions(self):
        self.create_verified_user()
        old_client = web.app.test_client()
        reset_client = web.app.test_client()
        self.assertEqual(self.login(client=old_client).status_code, 302)
        _response, token = self.request_reset(reset_client)
        reset_client.post(
            "/reset-password/" + token,
            data={
                "csrf_token": self.csrf(reset_client),
                "password": "replacement password",
                "password_confirmation": "replacement password",
            },
        )
        self.assertEqual(old_client.get("/settings", follow_redirects=False).status_code, 302)

    def test_logout_ends_session(self):
        self.create_verified_user()
        self.login()
        response = self.client.post(
            "/logout",
            data={"csrf_token": self.csrf()},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/settings", follow_redirects=False).status_code, 302)

    def test_csrf_protects_forms_and_authenticated_api_writes(self):
        self.assertEqual(
            self.client.post(
                "/register",
                data={
                    "email": "owner@example.com",
                    "password": "correct horse battery",
                    "password_confirmation": "correct horse battery",
                },
            ).status_code,
            400,
        )
        self.create_verified_user()
        self.login()
        self.assertEqual(
            self.client.patch("/api/v1/settings", json={"erp_name": "ERP"}).status_code,
            403,
        )

    def test_session_cookie_is_opaque_httponly_and_samesite(self):
        self.create_verified_user()
        response = self.login()
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("owner@example.com", cookie)


if __name__ == "__main__":
    unittest.main()
