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

    def test_waiting_contacts_result_cancel_restore_and_notifications(self):
        self.login()
        headers = {"X-CSRF-Token": "tasks-csrf", "Idempotency-Key": "waiting-once"}
        created = self.client.post("/api/v1/tasks", json={
            "title": "Запрос без ФИО", "assignee_id": self.user_id,
            "status": "waiting", "waiting_for": "Ответ поставщика",
            "check_date": "2020-01-01", "source_comment": "Нужен зелёный ремешок",
            "contact_phone": "+7 999 000-11-22", "contact_channel": "WhatsApp",
        }, headers=headers)
        self.assertEqual(created.status_code, 201)
        task_id = created.get_json()["data"]["id"]
        self.assertEqual(self.client.get("/api/v1/tasks?view=waiting").get_json()["data"]["total"], 1)
        self.assertEqual(self.client.get("/api/v1/tasks?view=overdue").get_json()["data"]["total"], 1)
        self.assertEqual(self.client.get("/api/v1/tasks?view=waiting&q=999000").get_json()["data"]["total"], 1)
        completed = self.client.post(
            "/api/v1/tasks/{}/complete".format(task_id), json={"result": "Получили ответ"},
            headers={"X-CSRF-Token": "tasks-csrf"},
        )
        self.assertEqual(completed.get_json()["data"]["completion_result"], "Получили ответ")
        restored = self.client.post(
            "/api/v1/tasks/{}/reopen".format(task_id), json={},
            headers={"X-CSRF-Token": "tasks-csrf"},
        )
        self.assertEqual(restored.get_json()["data"]["status"], "new")
        cancelled = self.client.post(
            "/api/v1/tasks/{}/status".format(task_id), json={"status": "cancelled"},
            headers={"X-CSRF-Token": "tasks-csrf"},
        )
        self.assertEqual(cancelled.get_json()["data"]["status"], "cancelled")
        self.assertGreaterEqual(len(cancelled.get_json()["data"]["history"]), 4)

    def test_task_output_is_json_escaped_and_double_submit_is_idempotent(self):
        self.login()
        headers = {"X-CSRF-Token": "tasks-csrf", "Idempotency-Key": "xss-once"}
        payload = {"title": "<script>alert(1)</script>", "assignee_id": self.user_id}
        first = self.client.post("/api/v1/tasks", json=payload, headers=headers)
        second = self.client.post("/api/v1/tasks", json=payload, headers=headers)
        self.assertEqual(first.get_json()["data"]["id"], second.get_json()["data"]["id"])
        self.assertTrue(second.get_json()["meta"]["duplicate"])
        page = self.client.get("/app/tasks").get_data(as_text=True)
        self.assertIn("Просрочено", page)
        self.assertIn("Ожидаю", page)
        self.assertIn("Название, описание, клиент, заказ или товар", page)
        self.assertIn('aria-controls="advancedFilters"', page)
        self.assertIn("tasks.css?v=5", page)
        self.assertIn("tasks.js?v=5", page)
        self.assertIn("Календарь", page)
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_calendar_api_range_reschedule_and_permission(self):
        self.login()
        headers = {"X-CSRF-Token": "tasks-csrf", "Idempotency-Key": "calendar-one"}
        created = self.client.post("/api/v1/tasks", json={
            "title": "Календарная", "assignee_id": self.user_id,
            "due_date": "2026-08-28", "due_time": "10:15",
        }, headers=headers).get_json()["data"]
        calendar = self.client.get(
            "/api/v1/tasks/calendar?start=2026-08-24&end=2026-08-30&scope=mine"
        )
        self.assertEqual(calendar.status_code, 200)
        self.assertEqual(calendar.get_json()["data"]["rows"][0]["id"], created["id"])
        self.assertTrue(calendar.get_json()["data"]["rows"][0]["can_edit"])
        moved = self.client.post(
            "/api/v1/tasks/{}/calendar-reschedule".format(created["id"]),
            json={"due_date": "2026-08-29", "due_time": "14:00", "version": created["version"]},
            headers={"X-CSRF-Token": "tasks-csrf"},
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual((moved.get_json()["data"]["due_date"], moved.get_json()["data"]["due_time"]),
                         ("2026-08-29", "14:00"))
        invalid = self.client.get("/api/v1/tasks/calendar?start=2026-01-01&end=2026-12-31")
        self.assertEqual(invalid.status_code, 422)

    def test_counts_api_honors_scope_filters_and_keeps_search_out_of_badges(self):
        self.login()
        headers = {"X-CSRF-Token": "tasks-csrf"}
        for title, values in (
            ("Нужная", {"section": "inbox", "priority": "urgent", "assignee_id": self.user_id}),
            ("Другая", {"section": "inbox", "priority": "other", "assignee_id": self.user_id}),
        ):
            response = self.client.post("/api/v1/tasks", json=dict({"title": title}, **values), headers=headers)
            self.assertEqual(response.status_code, 201)
        baseline = self.client.get("/api/v1/tasks/counts?view=inbox&scope=mine").get_json()["data"]
        searched = self.client.get("/api/v1/tasks/counts?view=inbox&scope=mine&q=Нужная").get_json()["data"]
        filtered = self.client.get("/api/v1/tasks/counts?view=inbox&scope=mine&priority=urgent").get_json()["data"]
        self.assertEqual((baseline["inbox"], searched["inbox"], filtered["inbox"]), (2, 2, 1))
        self.assertEqual(searched["statistics"], {"remaining": 1, "completed": 0, "total": 1})


if __name__ == "__main__":
    unittest.main()
