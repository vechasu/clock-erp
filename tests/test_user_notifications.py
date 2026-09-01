import tempfile
import time
import unittest
import sqlite3
from pathlib import Path

from app import auth, web
from app.domain_schema_migrations import apply_domain_migrations
from app.services.user_notifications import UserNotificationStore


def order(identifier, source="tictactoy"):
    if source == "wildberries":
        return {
            "id": "wb:" + str(identifier), "number": str(identifier),
            "wb_order_id": str(identifier), "source_name": "Wildberries",
        }
    return {"id": str(identifier), "number": str(identifier), "source_name": "Сайт / Ziro (Bitrix)"}


class UserNotificationStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        path = Path(self.temporary.name) / "auth.db"
        apply_domain_migrations(path, "auth", "test")
        store = auth.AuthStore(path)
        now = int(time.time())
        with store.connect() as connection:
            for user_id in (1, 2):
                connection.execute(
                    "INSERT INTO users(id,first_name,last_name,email,email_normalized,password_hash,role,active,created_at,email_verified_at,updated_at,session_version) "
                    "VALUES(?,?,'',?,?, 'hash','employee',1,?,?,?,1)",
                    (user_id, "Пользователь", "user{}@test".format(user_id),
                     "user{}@test".format(user_id), now, now, now),
                )
        self.store = UserNotificationStore(path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_order_baseline_new_delivery_and_dedupe(self):
        self.assertEqual(
            self.store.publish_saved_orders("tictactoy", set(), [order(100), order(101)], [1, 2]),
            0,
        )
        self.assertEqual(
            self.store.publish_saved_orders("tictactoy", {"100", "101"}, [order(100), order(101), order(102)], [1, 2]),
            2,
        )
        self.assertEqual(
            self.store.publish_saved_orders("tictactoy", {"100", "101", "102"}, [order(102)], [1, 2]),
            0,
        )
        first = self.store.feed(1)
        self.assertEqual((first["unread"], len(first["items"])), (1, 1))
        self.assertTrue(first["items"][0]["fresh"])
        self.assertEqual(first["items"][0]["target_url"], "/order/102")
        self.assertFalse(self.store.feed(1)["items"][0]["fresh"])
        self.assertTrue(self.store.feed(2)["items"][0]["fresh"])

    def test_auth_v2_upgrade_adds_notification_schema_without_user_loss(self):
        path = self.store.path
        connection = sqlite3.connect(str(path))
        try:
            connection.execute("DROP TABLE user_notification_preferences")
            connection.execute("DROP TABLE user_notifications")
            connection.execute("DROP TABLE notification_entities")
            connection.execute(
                "DELETE FROM erp_migration_ledger WHERE migration_id='2026-09-01-user-notifications-v1'"
            )
            connection.commit()
        finally:
            connection.close()
        apply_domain_migrations(path, "auth", "upgrade-test")
        upgraded = UserNotificationStore(path)
        self.assertEqual(upgraded.feed(1)["items"], [])
        self.assertIsNotNone(auth.AuthStore(path).get_user(1))

    def test_wb_notification_exists_only_after_saved_batch(self):
        self.store.publish_saved_orders("wildberries", set(), [order(200, "wildberries")], [1])
        self.assertEqual(self.store.feed(1)["items"], [])
        created = self.store.publish_saved_orders(
            "wildberries", {"wb:200"}, [order(200, "wildberries"), order(201, "wildberries")], [1]
        )
        self.assertEqual(created, 1)
        self.assertEqual(self.store.feed(1)["items"][0]["target_url"], "/order/wildberries/201")

    def test_task_is_personal_read_state_and_preferences_persist(self):
        task = {"id": 77, "title": "Собрать заказ #21134", "due_date": "2026-09-01", "due_time": "15:00"}
        self.assertTrue(self.store.publish_task(task, 2, "Иван"))
        self.assertFalse(self.store.publish_task(task, 2, "Иван"))
        self.assertEqual(self.store.feed(1)["items"], [])
        item = self.store.feed(2)["items"][0]
        self.assertEqual(item["metadata"], {"author": "Иван", "due": "2026-09-01 15:00"})
        self.assertTrue(self.store.mark_read(2, item["id"]))
        self.assertEqual(self.store.feed(2)["unread"], 0)
        self.store.publish_task({"id": 78, "title": "Вторая"}, 2, "Иван")
        self.assertEqual(self.store.mark_all_read(2), 1)
        saved = self.store.save_preferences(2, {
            "order_sound": False, "task_sound": False, "browser_notifications": True,
        })
        self.assertEqual(saved, self.store.preferences(2))
        self.assertTrue(saved["browser_notifications"])


class UserNotificationApiTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.auth_path = root / "auth.db"
        self.tasks_path = root / "tasks.db"
        apply_domain_migrations(self.auth_path, "auth", "test")
        apply_domain_migrations(self.tasks_path, "tasks", "test")
        self.auth_store = auth.AuthStore(self.auth_path)
        now = int(time.time())
        with self.auth_store.connect() as connection:
            self.author_id = connection.execute(
                "INSERT INTO users(first_name,last_name,email,email_normalized,password_hash,role,active,created_at,email_verified_at,updated_at,session_version) "
                "VALUES('Иван','','ivan@test','ivan@test','hash','employee',1,?,?,?,1)", (now, now, now),
            ).lastrowid
            self.assignee_id = connection.execute(
                "INSERT INTO users(first_name,last_name,email,email_normalized,password_hash,role,active,created_at,email_verified_at,updated_at,session_version) "
                "VALUES('Анна','','anna@test','anna@test','hash','employee',1,?,?,?,1)", (now, now, now),
            ).lastrowid
        web.app.config.update(
            TESTING=True, AUTH_TESTING=True, AUTH_DATABASE=str(self.auth_path),
            TASKS_DATABASE=str(self.tasks_path), NOTIFICATIONS_DATABASE=str(self.auth_path),
            NOTIFICATIONS_TESTING=True, SESSION_COOKIE_SECURE=False,
        )
        self.client = web.app.test_client()
        self.login(self.author_id)

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temporary.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["session_version"] = 1
            session["_csrf_token"] = "notification-csrf"

    def test_task_assignment_poll_read_and_preferences(self):
        created = self.client.post("/api/v1/tasks", json={
            "title": "Собрать заказ #21134", "assignee_id": self.assignee_id,
            "due_date": "2026-09-01", "due_time": "15:00",
        }, headers={"X-CSRF-Token": "notification-csrf", "Idempotency-Key": "notify-task"})
        self.assertEqual(created.status_code, 201)
        self.login(self.assignee_id)
        first = self.client.get("/api/v1/notifications").get_json()["data"]
        self.assertEqual((first["unread"], first["items"][0]["fresh"]), (1, True))
        self.assertEqual(first["items"][0]["metadata"]["author"], "Иван")
        second = self.client.get("/api/v1/notifications").get_json()["data"]
        self.assertFalse(second["items"][0]["fresh"])
        notification_id = first["items"][0]["id"]
        read = self.client.post(
            "/api/v1/notifications/{}/read".format(notification_id), json={},
            headers={"X-CSRF-Token": "notification-csrf"},
        )
        self.assertEqual(read.status_code, 200)
        preferences = self.client.put(
            "/api/v1/notification-preferences", json={"task_sound": False},
            headers={"X-CSRF-Token": "notification-csrf"},
        )
        self.assertFalse(preferences.get_json()["data"]["task_sound"])

    def test_sidebar_contains_bell_center_and_browser_controls(self):
        page = self.client.get("/app/tasks").get_data(as_text=True)
        for marker in (
            "data-notification-bell", "notificationCenter", "Все", "Заказы", "Задачи",
            "Отметить все как прочитанные", "Звук новых заказов",
            "Системные уведомления браузера", "event-notifications.js",
        ):
            self.assertIn(marker, page)


if __name__ == "__main__":
    unittest.main()
