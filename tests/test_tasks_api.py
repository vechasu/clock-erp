import tempfile
import time
import unittest
from pathlib import Path

from app import auth, web
from app.domain_schema_migrations import apply_domain_migrations


class TasksApiTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.auth_path = root / "auth.db"
        self.tasks_path = root / "tasks.db"
        apply_domain_migrations(self.auth_path, "auth", "test")
        apply_domain_migrations(self.tasks_path, "tasks", "test")
        self.store = auth.AuthStore(self.auth_path)
        now = int(time.time())
        with self.store.connect() as connection:
            self.user_id = connection.execute(
                "INSERT INTO users(first_name,last_name,email,email_normalized,password_hash,role,active,"
                "created_at,email_verified_at,updated_at,session_version) VALUES('Анна','Тест',"
                "'anna@example.test','anna@example.test','hash','employee',1,?,?,?,1)",
                (now, now, now),
            ).lastrowid
        web.app.config.update(
            TESTING=True, AUTH_TESTING=True, AUTH_DATABASE=str(self.auth_path),
            TASKS_DATABASE=str(self.tasks_path), SESSION_COOKIE_SECURE=False,
        )
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temporary.cleanup()

    def login(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id
            session["session_version"] = 1
            session["_csrf_token"] = "tasks-csrf"

    def test_tasks_page_and_api_require_authentication(self):
        page = self.client.get("/app/tasks")
        api = self.client.get("/api/v1/tasks")
        self.assertEqual(page.status_code, 302)
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.get_json()["code"], "AUTH_REQUIRED")

    def test_employee_can_create_complete_and_reopen_with_csrf(self):
        self.login()
        page = self.client.get("/app/tasks")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Задачи", page.get_data(as_text=True))
        rejected = self.client.post("/api/v1/tasks", json={"title": "Без CSRF"})
        self.assertEqual(rejected.status_code, 403)
        headers = {"X-CSRF-Token": "tasks-csrf", "Idempotency-Key": "task-once"}
        payload = {"title": "Вернуться к клиенту", "section": "inbox",
                   "priority": "important", "assignee_id": self.user_id}
        created = self.client.post("/api/v1/tasks", json=payload, headers=headers)
        duplicate = self.client.post("/api/v1/tasks", json=payload, headers=headers)
        self.assertEqual((created.status_code, duplicate.status_code), (201, 200))
        self.assertTrue(duplicate.get_json()["meta"]["duplicate"])
        task_id = created.get_json()["data"]["id"]
        completed = self.client.post(
            "/api/v1/tasks/{}/complete".format(task_id), json={},
            headers={"X-CSRF-Token": "tasks-csrf"},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/tasks?view=logbook").get_json()["data"]["total"], 1)
        reopened = self.client.post(
            "/api/v1/tasks/{}/reopen".format(task_id), json={},
            headers={"X-CSRF-Token": "tasks-csrf"},
        )
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/tasks?view=inbox").get_json()["data"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
