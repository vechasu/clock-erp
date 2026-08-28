import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.auth import AuthStore
from app.domain_schema_migrations import apply_domain_migrations


class NavigationPreferencesTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temporary.name) / "auth.db"
        apply_domain_migrations(self.auth_path, "auth", "navigation-test")
        self.store = AuthStore(self.auth_path)
        now = int(time.time())
        with self.store.connect() as connection:
            for user_id, role in ((1, "admin"), (2, "employee")):
                connection.execute(
                    """
                    INSERT INTO users (
                        id, first_name, last_name, email, email_normalized,
                        password_hash, role, active, created_at,
                        email_verified_at, updated_at, session_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1)
                    """,
                    (
                        user_id,
                        "User",
                        str(user_id),
                        "user{}@example.test".format(user_id),
                        "user{}@example.test".format(user_id),
                        "hash",
                        role,
                        now,
                        now,
                        now,
                    ),
                )
        web.app.config.update(
            TESTING=True,
            AUTH_TESTING=True,
            AUTH_DATABASE=str(self.auth_path),
            SESSION_COOKIE_SECURE=False,
        )
        web.app.extensions.setdefault("auth_stores", {})[
            str(self.auth_path)
        ] = self.store
        self.client = web.app.test_client()
        self.login(self.client, 1)

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temporary.cleanup()

    @staticmethod
    def login(client, user_id):
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["session_version"] = 1
            session["_csrf_token"] = "navigation-csrf"

    def get_items(self, client=None):
        response = (client or self.client).get(
            "/api/v1/navigation-preferences"
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["data"]

    def save(self, order, hidden, client=None, **extra):
        payload = {"order": order, "hidden": hidden}
        payload.update(extra)
        return (client or self.client).put(
            "/api/v1/navigation-preferences",
            json=payload,
            headers={"X-CSRF-Token": "navigation-csrf"},
        )

    def test_missing_preferences_preserve_system_menu(self):
        items = self.get_items()
        self.assertEqual(
            [item["key"] for item in items],
            [definition["key"] for definition in web.NAVIGATION_DEFINITIONS],
        )
        self.assertTrue(all(item["visible"] for item in items))

    def test_order_hide_restore_reset_and_persistence(self):
        original = self.get_items()
        order = [item["key"] for item in original]
        order.remove("tasks")
        order.insert(0, "tasks")
        response = self.save(order, ["analytics"])
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()["data"]
        self.assertEqual(saved[0]["key"], "tasks")
        self.assertFalse(next(
            item for item in saved if item["key"] == "analytics"
        )["visible"])

        another_device = web.app.test_client()
        self.login(another_device, 1)
        persisted = self.get_items(another_device)
        self.assertEqual(persisted, saved)

        restored = self.save(order, [], another_device)
        self.assertTrue(next(
            item for item in restored.get_json()["data"]
            if item["key"] == "analytics"
        )["visible"])
        reset = another_device.delete(
            "/api/v1/navigation-preferences",
            headers={"X-CSRF-Token": "navigation-csrf"},
        )
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.get_json()["data"], original)

    def test_two_users_are_isolated_and_client_cannot_choose_user_id(self):
        items = self.get_items()
        order = [item["key"] for item in items]
        self.assertEqual(self.save(order, ["analytics"]).status_code, 200)

        employee = web.app.test_client()
        self.login(employee, 2)
        employee_items = self.get_items(employee)
        self.assertTrue(next(
            item for item in employee_items if item["key"] == "analytics"
        )["visible"])
        rejected = self.save(order, [], employee, user_id=1)
        self.assertEqual(rejected.status_code, 422)
        self.assertFalse(next(
            item for item in self.get_items() if item["key"] == "analytics"
        )["visible"])

    def test_unknown_duplicate_forbidden_and_required_keys_are_rejected(self):
        order = [item["key"] for item in self.get_items()]
        self.assertEqual(self.save(order + ["unknown"], []).status_code, 422)
        self.assertEqual(self.save(order + [order[0]], []).status_code, 422)
        self.assertEqual(self.save(order, ["settings"]).status_code, 422)

        employee = web.app.test_client()
        self.login(employee, 2)
        analytics = next(
            definition for definition in web.NAVIGATION_DEFINITIONS
            if definition["key"] == "analytics"
        )
        with mock.patch.dict(analytics, {"roles": ("admin",)}):
            allowed = [item["key"] for item in self.get_items(employee)]
            self.assertNotIn("analytics", allowed)
            self.assertEqual(
                self.save(allowed + ["analytics"], [], employee).status_code,
                422,
            )

    def test_global_disable_and_corrupt_data_fail_closed(self):
        analytics = next(
            definition for definition in web.NAVIGATION_DEFINITIONS
            if definition["key"] == "analytics"
        )
        with mock.patch.dict(analytics, {"enabled": False}):
            self.assertNotIn(
                "analytics", [item["key"] for item in self.get_items()]
            )

        with self.store.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO user_navigation_preferences "
                "(user_id,ordered_keys,hidden_keys,updated_at) VALUES (1,?,?,1)",
                (json.dumps(["orders", "orders"]), json.dumps(["unknown"])),
            )
        with self.assertLogs(web.app.logger.name, level="WARNING"):
            items = self.get_items()
        self.assertEqual(
            [item["key"] for item in items],
            [definition["key"] for definition in web.NAVIGATION_DEFINITIONS],
        )

    def test_new_system_key_is_merged_without_becoming_hidden(self):
        self.assertEqual(
            web._merge_navigation_order(
                ["orders", "new", "tasks", "settings"],
                ["tasks", "orders", "settings"],
            ),
            ["tasks", "orders", "new", "settings"],
        )

    def test_hidden_direct_link_keeps_preference_and_active_state(self):
        order = [item["key"] for item in self.get_items()]
        self.save(order, ["analytics"])
        with web.app.test_request_context("/app/analytics"):
            with mock.patch.object(
                web, "current_auth_user", return_value=self.store.get_user(1)
            ):
                items = web.get_navigation_items(include_disabled=True)
        analytics = next(item for item in items if item["key"] == "analytics")
        self.assertTrue(analytics["active"])
        self.assertFalse(analytics["enabled"])
        self.assertEqual(
            self.store.get_navigation_preferences(1)["hidden_keys"],
            ["analytics"],
        )

    def test_settings_ui_has_drag_keyboard_mobile_and_accessibility_controls(self):
        response = self.client.get("/app/settings")
        html = response.get_data(as_text=True)
        self.assertIn("Синие вкладки", html)
        self.assertIn("data-navigation-move=\"up\"", html)
        self.assertIn("draggable=\"true\"", html)
        self.assertIn("Всегда отображается", html)
        self.assertIn("aria-live=\"polite\"", html)
        css = (Path(web.PROJECT_ROOT) / "app/static/css/settings-navigation.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("@media (max-width: 760px)", css)
        javascript = (
            Path(web.PROJECT_ROOT) / "app/static/js/settings.js"
        ).read_text(encoding="utf-8")
        self.assertIn('addEventListener("dragstart"', javascript)
        self.assertIn('data-navigation-move="up"', javascript)
        self.assertIn('window.location.reload()', javascript)


if __name__ == "__main__":
    unittest.main()
