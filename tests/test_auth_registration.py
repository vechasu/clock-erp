import contextlib
import io
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.security import check_password_hash

from app import auth
from app import web


class AuthRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "auth.db"
        web.app.config.update(
            TESTING=True,
            AUTH_TESTING=True,
            AUTH_DATABASE=str(self.database_path),
            SESSION_COOKIE_SECURE=False,
            REGISTRATION_RATE_LIMIT=30,
            INVITATION_CHECK_RATE_LIMIT=30,
            LOGIN_RATE_LIMIT=30,
            AUTH_OFFICE_RATE_LIMIT=500,
        )
        self.client = web.app.test_client()
        self.store = auth.AuthStore(self.database_path)

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp_directory.cleanup()

    def csrf(self, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            token = session.get("_csrf_token")
            if not token:
                token = "test-csrf-token"
                session["_csrf_token"] = token
        return token

    def create_user(self, email="admin@tictactoy.ru", password="strong passphrase"):
        user_id = self.store.create_initial_admin(
            "Максим",
            "Администратор",
            email,
            password,
        )
        return self.store.get_user(user_id)

    def login_session(self, user, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            session["user_id"] = user["id"]
            session["_csrf_token"] = "test-csrf-token"

    def create_invitation(
        self,
        email="employee@tictactoy.ru",
        role="employee",
        lifetime_hours=72,
    ):
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
            ).fetchone()
        admin = (
            self.store.get_user(row["id"])
            if row is not None
            else self.create_user()
        )
        token = self.store.create_invitation(
            admin["id"],
            email,
            role,
            lifetime_hours,
        )
        return token

    def register(
        self,
        token,
        email="employee@tictactoy.ru",
        password="correct horse battery",
        extra=None,
        client=None,
    ):
        client = client or self.client
        client.get("/register")
        invitation_response = client.post(
            "/register/invitation",
            data={
                "csrf_token": self.csrf(client),
                "invitation_token": token,
            },
            follow_redirects=False,
        )
        self.assertEqual(invitation_response.status_code, 302)
        data = {
            "csrf_token": self.csrf(client),
            "first_name": "Анна",
            "last_name": "Иванова",
            "email": email,
            "password": password,
            "password_confirmation": password,
            "terms": "1",
        }
        if extra:
            data.update(extra)
        return client.post("/register", data=data, follow_redirects=False)

    def test_register_page_and_login_cross_links(self):
        register_page = self.client.get("/register")
        login_page = self.client.get("/login")

        self.assertEqual(register_page.status_code, 200)
        self.assertIn("Уже есть аккаунт?", register_page.get_data(as_text=True))
        self.assertIn('href="/login', register_page.get_data(as_text=True))
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("Нет аккаунта?", login_page.get_data(as_text=True))
        self.assertIn('href="/register', login_page.get_data(as_text=True))

    def test_anonymous_root_opens_registration_and_protected_page_opens_login(self):
        root = self.client.get("/", follow_redirects=False)
        protected = self.client.get("/settings", follow_redirects=False)

        self.assertEqual(root.status_code, 302)
        self.assertEqual(root.headers["Location"], "/register")
        self.assertEqual(protected.status_code, 302)
        self.assertTrue(protected.headers["Location"].startswith("/login?next="))

    def test_authenticated_user_opens_erp_and_register_redirects(self):
        user = self.create_user()
        self.login_session(user)

        root = self.client.get("/", follow_redirects=False)
        overview = self.client.get("/overview")
        register_page = self.client.get("/register", follow_redirects=False)

        self.assertEqual(root.status_code, 302)
        self.assertEqual(root.headers["Location"], "/overview")
        self.assertEqual(overview.status_code, 200)
        self.assertIn("Обзор", overview.get_data(as_text=True))
        self.assertEqual(register_page.status_code, 302)
        self.assertEqual(register_page.headers["Location"], "/")

    def test_existing_user_can_login_and_logout_returns_to_login(self):
        user = self.create_user(password="a reliable manager phrase")
        login_page = self.client.get("/login")
        self.assertEqual(login_page.status_code, 200)

        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf(),
                "email": "ADMIN@TICTACTOY.RU",
                "password": "a reliable manager phrase",
                "next": "/settings",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/settings")

        logout = self.client.post(
            "/logout",
            data={"csrf_token": self.csrf()},
            follow_redirects=False,
        )
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(logout.headers["Location"], "/login")
        self.assertIsNotNone(user)

    def test_registration_with_valid_invitation_hashes_password_and_logs_in(self):
        password = "correct horse battery"
        token = self.create_invitation()
        response = self.register(token, password=password)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/register/success")
        with self.client.session_transaction() as session:
            self.assertIsNotNone(session.get("user_id"))
            self.assertNotIn("_pending_invitation", session)

        with self.store.connect() as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE email_normalized = ?",
                ("employee@tictactoy.ru",),
            ).fetchone()
            invitation = connection.execute(
                "SELECT * FROM invitations WHERE token_hash = ?",
                (auth.invitation_digest(token),),
            ).fetchone()

        self.assertNotEqual(user["password_hash"], password)
        self.assertTrue(check_password_hash(user["password_hash"], password))
        self.assertEqual(invitation["state"], "used")
        self.assertNotIn(token, self.database_path.read_bytes().decode(
            "utf-8",
            errors="ignore",
        ))

    def test_bound_email_is_prefilled_readonly_and_cannot_be_changed(self):
        token = self.create_invitation(email="bound@tictactoy.ru")
        self.client.get("/register")
        self.client.post(
            "/register/invitation",
            data={
                "csrf_token": self.csrf(),
                "invitation_token": token,
            },
        )
        page = self.client.get("/register").get_data(as_text=True)

        self.assertIn('value="bound@tictactoy.ru"', page)
        self.assertIn("readonly", page)

        response = self.client.post(
            "/register",
            data={
                "csrf_token": self.csrf(),
                "first_name": "Анна",
                "last_name": "Иванова",
                "email": "other@tictactoy.ru",
                "password": "correct horse battery",
                "password_confirmation": "correct horse battery",
                "terms": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("указанный в приглашении", response.get_data(as_text=True))

    def test_registration_rejects_missing_and_unknown_invitation(self):
        page = self.client.get("/register")
        missing = self.client.post(
            "/register",
            data={
                "csrf_token": self.csrf(),
                "first_name": "Анна",
                "last_name": "Иванова",
                "email": "new@tictactoy.ru",
                "password": "correct horse battery",
                "password_confirmation": "correct horse battery",
                "terms": "1",
            },
        )
        unknown = self.client.post(
            "/register",
            data={
                "csrf_token": self.csrf(),
                "first_name": "Анна",
                "last_name": "Иванова",
                "email": "new@tictactoy.ru",
                "password": "correct horse battery",
                "password_confirmation": "correct horse battery",
                "invitation_code": "unknown-invitation-code",
                "terms": "1",
            },
        )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertIn("Приглашение недействительно", missing.get_data(as_text=True))
        self.assertIn("Приглашение недействительно", unknown.get_data(as_text=True))

    def test_expired_revoked_and_used_invitations_are_rejected(self):
        for state in ("expired", "revoked", "used"):
            with self.subTest(state=state):
                client = web.app.test_client()
                email = f"{state}@tictactoy.ru"
                token = self.create_invitation(email=email)
                token_hash = auth.invitation_digest(token)
                if state == "expired":
                    with self.store.connect() as connection:
                        connection.execute(
                            "UPDATE invitations SET expires_at = ? WHERE token_hash = ?",
                            (int(time.time()) - 1, token_hash),
                        )
                elif state == "revoked":
                    invitation = self.store.get_invitation(token_hash)
                    self.assertTrue(
                        self.store.revoke_invitation(invitation["id"])
                    )
                else:
                    used = self.store.register_user(
                        token_hash,
                        "Анна",
                        "Иванова",
                        email,
                        "correct horse battery",
                    )
                    self.assertIsNotNone(used)

                client.get("/register")
                response = client.post(
                    "/register/invitation",
                    data={
                        "csrf_token": self.csrf(client),
                        "invitation_token": token,
                    },
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    "Приглашение недействительно",
                    response.get_data(as_text=True),
                )

    def test_invitation_can_only_be_claimed_once_under_concurrent_requests(self):
        token = self.create_invitation(email=None)
        token_hash = auth.invitation_digest(token)
        barrier = threading.Barrier(2)
        results = []

        def attempt(index):
            barrier.wait()
            try:
                user = self.store.register_user(
                    token_hash,
                    f"Имя{index}",
                    "Сотрудник",
                    f"parallel{index}@tictactoy.ru",
                    "correct horse battery",
                )
                results.append(("ok", user["id"]))
            except auth.RegistrationError:
                results.append(("rejected", None))

        threads = [
            threading.Thread(target=attempt, args=(index,))
            for index in (1, 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(
            sorted(result[0] for result in results),
            ["ok", "rejected"],
        )

    def test_email_is_unique_case_insensitively(self):
        first_token = self.create_invitation(email=None)
        first = self.register(first_token, email="Case@TicTacToy.ru")
        self.assertEqual(first.status_code, 302)

        second_client = web.app.test_client()
        second_token = self.store.create_invitation(
            self.create_user(email="another-admin@tictactoy.ru")["id"],
            None,
            "employee",
            72,
        )
        second = self.register(
            second_token,
            email="case@tictactoy.RU",
            client=second_client,
        )
        self.assertEqual(second.status_code, 200)
        self.assertIn(
            "Не удалось создать аккаунт",
            second.get_data(as_text=True),
        )

    def test_mismatched_password_and_csrf_are_rejected(self):
        token = self.create_invitation()
        self.client.get("/register")
        self.client.post(
            "/register/invitation",
            data={
                "csrf_token": self.csrf(),
                "invitation_token": token,
            },
        )

        mismatch = self.client.post(
            "/register",
            data={
                "csrf_token": self.csrf(),
                "first_name": "Анна",
                "last_name": "Иванова",
                "email": "employee@tictactoy.ru",
                "password": "correct horse battery",
                "password_confirmation": "different password",
                "terms": "1",
            },
        )
        csrf = self.client.post(
            "/register",
            data={
                "csrf_token": "wrong",
                "first_name": "Анна",
                "last_name": "Иванова",
                "email": "employee@tictactoy.ru",
                "password": "correct horse battery",
                "password_confirmation": "correct horse battery",
                "terms": "1",
            },
        )

        self.assertEqual(mismatch.status_code, 200)
        self.assertIn("Пароли не совпадают", mismatch.get_data(as_text=True))
        self.assertEqual(csrf.status_code, 400)

    def test_registration_rate_limit_is_temporary_and_scoped(self):
        web.app.config["REGISTRATION_RATE_LIMIT"] = 1
        token = self.create_invitation()
        self.client.get("/register")
        self.client.post(
            "/register/invitation",
            data={
                "csrf_token": self.csrf(),
                "invitation_token": token,
            },
        )
        data = {
            "csrf_token": self.csrf(),
            "first_name": "",
            "last_name": "",
            "email": "employee@tictactoy.ru",
            "password": "correct horse battery",
            "password_confirmation": "correct horse battery",
            "terms": "1",
        }

        first = self.client.post("/register", data=data)
        second = self.client.post("/register", data=data)
        other_client = web.app.test_client()
        other_client.get("/register")
        data["csrf_token"] = self.csrf(other_client)
        other = other_client.post("/register", data=data)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(other.status_code, 200)

    def test_invitation_assigns_role_and_client_cannot_promote_itself(self):
        token = self.create_invitation(email=None, role="employee")
        response = self.register(
            token,
            email="role@tictactoy.ru",
            extra={"role": "admin"},
        )
        self.assertEqual(response.status_code, 302)

        with self.store.connect() as connection:
            role = connection.execute(
                "SELECT role FROM users WHERE email_normalized = ?",
                ("role@tictactoy.ru",),
            ).fetchone()[0]
        self.assertEqual(role, "employee")

        admin_client = web.app.test_client()
        admin_token = self.store.create_invitation(
            self.create_user(email="roles-admin@tictactoy.ru")["id"],
            "new-admin@tictactoy.ru",
            "admin",
            72,
        )
        admin_response = self.register(
            admin_token,
            email="new-admin@tictactoy.ru",
            client=admin_client,
        )
        self.assertEqual(admin_response.status_code, 302)
        with self.store.connect() as connection:
            admin_role = connection.execute(
                "SELECT role FROM users WHERE email_normalized = ?",
                ("new-admin@tictactoy.ru",),
            ).fetchone()[0]
        self.assertEqual(admin_role, "admin")

    def test_only_admin_can_create_list_and_revoke_invitations(self):
        admin = self.create_user()
        employee_token = self.store.create_invitation(
            admin["id"],
            "ordinary@tictactoy.ru",
            "employee",
            72,
        )
        employee = self.store.register_user(
            auth.invitation_digest(employee_token),
            "Олег",
            "Сотрудник",
            "ordinary@tictactoy.ru",
            "correct horse battery",
        )

        self.login_session(employee)
        forbidden = self.client.post(
            "/settings/invitations",
            data={
                "csrf_token": self.csrf(),
                "email": "new@tictactoy.ru",
                "role": "admin",
                "lifetime_hours": "72",
            },
        )
        settings = self.client.get("/settings")
        self.assertEqual(forbidden.status_code, 403)
        self.assertNotIn(
            "Приглашения сотрудников",
            settings.get_data(as_text=True),
        )

        admin_client = web.app.test_client()
        self.login_session(admin, admin_client)
        created = admin_client.post(
            "/settings/invitations",
            data={
                "csrf_token": self.csrf(admin_client),
                "email": "new@tictactoy.ru",
                "role": "employee",
                "lifetime_hours": "24",
            },
        )
        self.assertEqual(created.status_code, 200)
        created_html = created.get_data(as_text=True)
        self.assertIn("Скопировать ссылку", created_html)
        token_match = re.search(r"#invite=([A-Za-z0-9_-]+)", created_html)
        self.assertIsNotNone(token_match)
        new_token = token_match.group(1)

        invitation = self.store.get_invitation(
            auth.invitation_digest(new_token)
        )
        revoked = admin_client.post(
            f"/settings/invitations/{invitation['id']}/revoke",
            data={"csrf_token": self.csrf(admin_client)},
            follow_redirects=False,
        )
        self.assertEqual(revoked.status_code, 302)
        self.assertEqual(
            self.store.get_invitation(
                auth.invitation_digest(new_token)
            )["status"],
            "revoked",
        )

    def test_password_and_full_invitation_token_are_not_logged_or_stored(self):
        password = "private manager phrase"
        token = self.create_invitation()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            response = self.register(token, password=password)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(password, output.getvalue())
        self.assertNotIn(token, output.getvalue())
        database_bytes = self.database_path.read_bytes()
        self.assertNotIn(password.encode(), database_bytes)
        self.assertNotIn(token.encode(), database_bytes)

    def test_external_next_is_never_used_for_redirect(self):
        self.create_user(password="safe redirect phrase")
        self.client.get("/login?next=https://evil.example/steal")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf(),
                "email": "admin@tictactoy.ru",
                "password": "safe redirect phrase",
                "next": "//evil.example/steal",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_bootstrap_admin_link_is_one_time_and_disabled_after_first_user(self):
        runner = web.app.test_cli_runner()
        first = runner.invoke(
            args=["auth-bootstrap-admin-link", "--lifetime-hours", "12"]
        )
        second = runner.invoke(
            args=["auth-bootstrap-admin-link", "--lifetime-hours", "12"]
        )

        self.assertEqual(first.exit_code, 0, first.output)
        self.assertRegex(
            first.output.strip(),
            r"^/register#invite=[A-Za-z0-9_-]+$",
        )
        self.assertNotIn("invite=", self.database_path.read_text(
            encoding="utf-8",
            errors="ignore",
        ))
        self.assertNotEqual(second.exit_code, 0)
        self.assertIn("уже существует", second.output)

        token = first.output.strip().split("#invite=", 1)[1]
        user = self.store.register_user(
            auth.invitation_digest(token),
            "Максим",
            "Администратор",
            "owner@tictactoy.ru",
            "safe bootstrap phrase",
        )
        self.assertEqual(user["role"], "admin")

        after_user = runner.invoke(args=["auth-bootstrap-admin-link"])
        self.assertNotEqual(after_user.exit_code, 0)
        self.assertIn("уже завершена", after_user.output)


if __name__ == "__main__":
    unittest.main()
