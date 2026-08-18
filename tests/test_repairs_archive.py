import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.auth import AuthStore
from app.services.audit_journal import AuditJournal
from app.services.repair_cases import load_repair_file, migrate_repair_file
from tests.test_repairs_full_cycle import base_payload


class RepairsArchiveTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = self.root / "repair_cases.json"
        self.database = self.root / "catalog.db"
        self.patchers = [
            mock.patch.object(web, "get_repair_cases_path", return_value=self.store),
            mock.patch.object(web, "get_excel_warehouse_items", return_value=[]),
            mock.patch.dict(
                os.environ,
                {"CATALOG_DATABASE_PATH": str(self.database)},
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp.cleanup()

    def create(self, **changes):
        response = self.client.post(
            "/api/v1/repairs",
            json=base_payload(**changes),
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["data"]

    def set_status(self, repair_id, status):
        cases = load_repair_file(self.store)
        target = next(case for case in cases if case["id"] == repair_id)
        target["status"] = status
        web.save_repair_cases(cases)

    def archive(self, repair_id):
        return self.client.post(f"/api/v1/repairs/{repair_id}/archive", json={})

    def test_migration_keeps_existing_completed_repairs_active(self):
        self.store.write_text(json.dumps([{
            "id": "existing", "schema_version": 3, "status": "completed",
            "client_name": "Клиент", "product_name": "Часы",
            "created_at": "2026-01-01 10:00", "updated_at": "2026-01-02 10:00",
        }], ensure_ascii=False), encoding="utf-8")
        report = migrate_repair_file(
            self.store,
            apply=True,
            backup_dir=self.root / "backups",
        )
        migrated = load_repair_file(self.store)[0]
        self.assertEqual(report["schema_version"], 4)
        self.assertEqual(migrated["archived_at"], "")
        self.assertEqual(migrated["archived_by"], "")
        self.assertTrue(Path(report["backup_path"]).exists())

    def test_active_archive_filters_search_pagination_and_counts_are_separate(self):
        active = self.create(client_name="Активный Клиент")
        archived = self.create(client_name="Архивный Клиент")
        self.create(client_name="Второй Активный")
        self.set_status(archived["id"], "completed")
        self.assertEqual(self.archive(archived["id"]).status_code, 200)

        active_listing = self.client.get(
            "/api/v1/repairs?view=active&q=активный&page=1&page_size=1"
        ).get_json()
        archive_listing = self.client.get(
            "/api/v1/repairs?view=archive&q=архивный&status=completed&page=1&page_size=1"
        ).get_json()
        self.assertEqual(active_listing["meta"]["total"], 2)
        self.assertEqual(len(active_listing["data"]), 1)
        self.assertNotEqual(active_listing["data"][0]["id"], archived["id"])
        self.assertEqual([item["id"] for item in archive_listing["data"]], [archived["id"]])
        self.assertEqual(active_listing["meta"]["stats"]["active_records"], 2)
        self.assertEqual(active_listing["meta"]["stats"]["archived"], 1)

        active_html = self.client.get("/app/repairs?view=active").get_data(as_text=True)
        archive_html = self.client.get("/app/repairs?view=archive").get_data(as_text=True)
        self.assertIn("Активные · 2", active_html)
        self.assertIn("Архив · 1", archive_html)
        self.assertNotIn(f'data-repair-id="{archived["id"]}"', active_html)
        self.assertIn(f'data-repair-id="{archived["id"]}"', archive_html)

    def test_only_final_repair_archives_and_status_data_are_preserved(self):
        repair = self.create(problem="Исходная неисправность", agreed_cost="1234")
        rejected = self.archive(repair["id"])
        self.assertEqual(rejected.status_code, 409)
        self.assertIn("Сначала завершите ремонт", rejected.get_json()["message"])

        self.set_status(repair["id"], "completed")
        before = load_repair_file(self.store)[0]
        archived = self.archive(repair["id"])
        self.assertEqual(archived.status_code, 200)
        stored = load_repair_file(self.store)[0]
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["problem"], before["problem"])
        self.assertEqual(stored["agreed_cost"], before["agreed_cost"])
        self.assertTrue(stored["archived_at"])
        self.assertTrue(stored["archived_by"])
        self.assertEqual(stored["history"][-1]["action"], "Ремонт перемещён в архив")

    def test_archive_restore_are_idempotent_audited_and_preserve_status(self):
        repair = self.create()
        self.set_status(repair["id"], "cancelled")
        first = self.archive(repair["id"])
        history_after_first = len(load_repair_file(self.store)[0]["history"])
        repeated = self.archive(repair["id"])
        self.assertEqual(first.status_code, 200)
        self.assertTrue(repeated.get_json()["meta"]["repeated"])
        self.assertEqual(len(load_repair_file(self.store)[0]["history"]), history_after_first)

        restored = self.client.post(f"/api/v1/repairs/{repair['id']}/restore", json={})
        history_after_restore = len(load_repair_file(self.store)[0]["history"])
        repeated_restore = self.client.post(
            f"/api/v1/repairs/{repair['id']}/restore", json={}
        )
        stored = load_repair_file(self.store)[0]
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(repeated_restore.get_json()["meta"]["repeated"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["archived_at"], "")
        self.assertEqual(stored["archived_by"], "")
        self.assertEqual(len(stored["history"]), history_after_restore)
        events = AuditJournal().list_events(
            entity_type="repair", entity_id=repair["id"], limit=10,
        )["events"]
        self.assertEqual([event["action"] for event in events], ["restored", "archived"])

    def test_archived_repair_is_read_only_for_all_mutation_apis(self):
        repair = self.create()
        self.set_status(repair["id"], "completed")
        self.archive(repair["id"])
        patch = self.client.patch(
            f"/api/v1/repairs/{repair['id']}", json={"client_name": "Другой"}
        )
        action = self.client.post(
            f"/api/v1/repairs/{repair['id']}/actions/reopen",
            json={"reason": "Нет", "status": "new"},
        )
        shipment = self.client.post(
            f"/api/v1/repairs/{repair['id']}/shipments",
            json={"direction": "inbound", "track_number": "X"},
        )
        self.assertEqual(patch.status_code, 422)
        self.assertEqual(action.status_code, 409)
        self.assertEqual(shipment.status_code, 422)
        self.assertEqual(load_repair_file(self.store)[0]["client_name"], "Иван Петров")

    def test_repeat_repair_creates_clean_active_record_and_audit_link(self):
        source = self.create(
            problem="Старая неисправность", agreed_cost="5000",
            work_result="Старые работы", note="Старый комментарий",
        )
        self.set_status(source["id"], "completed")
        self.archive(source["id"])
        repeated = self.create(
            request_type="repeat_repair",
            parent_repair_id=source["id"],
            problem="Новая заявленная неисправность",
            agreed_cost="", work_result="", note="",
        )
        current = {case["id"]: case for case in load_repair_file(self.store)}
        self.assertNotEqual(repeated["id"], source["id"])
        self.assertEqual(current[repeated["id"]]["status"], "new")
        self.assertFalse(current[repeated["id"]]["archived_at"])
        self.assertEqual(current[repeated["id"]]["agreed_cost"], "")
        self.assertEqual(current[repeated["id"]]["work_result"], "")
        self.assertEqual(current[source["id"]]["status"], "completed")
        self.assertTrue(current[source["id"]]["archived_at"])
        events = AuditJournal().list_events(
            entity_type="repair", entity_id=repeated["id"], limit=10,
        )["events"]
        self.assertEqual(events[0]["action"], "created")
        self.assertEqual(events[0]["metadata"]["source_repair_id"], source["id"])

    def test_authenticated_archive_requires_csrf_and_keeps_existing_role_access(self):
        auth_path = self.root / "auth.db"
        store = AuthStore(auth_path)
        user_id = store.create_initial_admin(
            "Иван", "Сотрудник", "employee@example.test", "StrongPassword!234"
        )
        with store.connect() as connection:
            connection.execute("UPDATE users SET role = 'employee' WHERE id = ?", (user_id,))
        repair = self.create()
        self.set_status(repair["id"], "completed")
        web.app.config.update(AUTH_TESTING=True, AUTH_DATABASE=str(auth_path))
        client = web.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["_csrf_token"] = "archive-csrf"
        rejected = client.post(f"/api/v1/repairs/{repair['id']}/archive", json={})
        allowed = client.post(
            f"/api/v1/repairs/{repair['id']}/archive",
            json={}, headers={"X-CSRF-Token": "archive-csrf"},
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.get_json()["data"]["archived_by"], "Иван Сотрудник")


if __name__ == "__main__":
    unittest.main()
