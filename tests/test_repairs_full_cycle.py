import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from app import web
from app.auth import AuthStore
from app.services.repair_cases import (
    REPAIR_SCHEMA_VERSION,
    load_repair_file,
    migrate_repair_file,
    repair_attention_key,
)


def order_fixture():
    return {
        "id": "20735",
        "number": "20735",
        "user": {"name": "Иван Петров", "phone": "+79990000000"},
        "products": [
            {"id": "p-1", "name": "Vechasu Voyager", "quantity": 1},
            {"id": "p-2", "name": "Vechasu Lunar", "quantity": 1},
        ],
    }


def base_payload(**changes):
    payload = {
        "order_source": "none",
        "client_name": "Иван Петров",
        "contact": "@ivan",
        "communication_channel": "telegram",
        "product_name": "Voyager",
        "brand": "Vechasu",
        "model": "Voyager",
        "request_type": "paid_repair",
        "problem": "Часы не включаются",
        "problem_details": "После зарядки экран остаётся выключенным",
        "equipment": "Часы и коробка",
        "external_condition": "Без повреждений",
        "location": "with_customer",
        "next_action": "Связаться с клиентом",
        "waiting_for": "us",
        "control_date": (date.today() + timedelta(days=1)).isoformat(),
    }
    payload.update(changes)
    return payload


class RepairsFullCycleTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = self.root / "repair_cases.json"
        self.order = order_fixture()
        self.patchers = [
            mock.patch.object(web, "get_repair_cases_path", return_value=self.store),
            mock.patch.object(web, "get_excel_warehouse_items", return_value=[]),
            mock.patch.object(web, "get_order", side_effect=lambda _order_id: self.order),
            mock.patch.object(web, "get_orders", return_value=[self.order]),
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
        response = self.client.post("/api/v1/repairs", json=base_payload(**changes))
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["data"]

    def action(self, repair_id, action, **changes):
        payload = {
            "next_action": "Следующий контроль",
            "waiting_for": "us",
            "control_date": (date.today() + timedelta(days=2)).isoformat(),
            "idempotency_key": f"{repair_id}:{action}:{len(changes)}",
            **changes,
        }
        return self.client.post(
            f"/api/v1/repairs/{repair_id}/actions/{action}",
            json=payload,
            headers={"Idempotency-Key": payload["idempotency_key"]},
        )

    def test_create_from_order_requires_exact_position_and_keeps_snapshot(self):
        created = self.create(
            order_source="our",
            order_id="20735",
            order_item_id="p-1:position:1",
            client_name="Подменённое имя",
            product_name="Подменённый товар",
        )
        self.assertEqual(created["order_id"], "20735")
        self.assertEqual(created["order_item_id"], "p-1:position:1")
        self.assertEqual(created["client_name"], "Иван Петров")
        self.assertEqual(created["product_name"], "Vechasu Voyager")
        self.assertEqual(created["schema_version"], REPAIR_SCHEMA_VERSION)

        self.order["user"]["name"] = "Новое имя в заказе"
        stored = self.client.get(f"/api/v1/repairs/{created['id']}").get_json()["data"]
        self.assertEqual(stored["client_name"], "Иван Петров")

    def test_position_from_another_order_is_rejected(self):
        response = self.client.post(
            "/api/v1/repairs",
            json=base_payload(
                order_source="our",
                order_id="20735",
                order_item_id="foreign-position",
            ),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(load_repair_file(self.store), [])

    def test_without_order_accepts_non_phone_contact_and_one_item_per_case(self):
        first = self.create(client_phone="", client_email="", contact="@telegram")
        second = self.create(model="Lunar", product_name="Lunar")
        self.assertFalse(first["order_id"])
        self.assertEqual(first["contact"], "@telegram")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(load_repair_file(self.store)), 2)
        self.assertEqual(first["product_name"], "Voyager")

    def test_multiple_repairs_can_share_order_and_different_positions(self):
        first = self.create(order_source="our", order_id="20735", order_item_id="p-1:position:1")
        second = self.create(order_source="our", order_id="20735", order_item_id="p-2:position:2")
        self.assertEqual(first["order_id"], second["order_id"])
        self.assertNotEqual(first["order_item_id"], second["order_item_id"])
        self.assertNotEqual(first["id"], second["id"])

    def test_repeat_repair_is_new_record_and_preserves_parent(self):
        parent = self.create()
        cases = load_repair_file(self.store)
        cases[0]["status"] = "completed"
        cases[0]["completion_result"] = "repaired"
        web.save_repair_cases(cases)
        repeated = self.create(request_type="repeat_repair", parent_repair_id=parent["id"])
        current = {item["id"]: item for item in load_repair_file(self.store)}
        self.assertEqual(repeated["parent_repair_id"], parent["id"])
        self.assertEqual(current[parent["id"]]["status"], "completed")
        self.assertEqual(current[parent["id"]]["repeat_repair_id"], repeated["id"])

    def test_status_and_physical_location_are_independent(self):
        repair = self.create(location="at_us")
        stored = load_repair_file(self.store)[0]
        self.assertEqual(stored["status"], "new")
        self.assertEqual(stored["location"], "at_us")
        response = self.action(repair["id"], "request_shipment")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["status"], "waiting_customer_shipment")
        self.assertEqual(data["location"], "with_customer")

    def test_full_paid_flow_validates_transitions_diagnostics_money_and_history(self):
        repair = self.create()
        invalid = self.action(repair["id"], "mark_ready")
        self.assertEqual(invalid.status_code, 409)

        transitions = [
            ("request_shipment", {}),
            ("mark_customer_sent", {}),
            ("receive", {}),
            ("start_diagnostics", {}),
            ("finish_diagnostics", {
                "diagnostic_result": "Неисправна плата",
                "proposed_solution": "Заменить плату",
            }),
            ("accept_paid", {
                "customer_decision": "Согласен",
                "agreed_cost": "2500,50",
            }),
            ("record_payment", {"payment_amount": "2500.50"}),
            ("mark_ready", {}),
            ("send_to_customer", {
                "return_method": "cdek",
                "outgoing_waybill": "OUT-100",
            }),
            ("complete", {
                "completion_result": "repaired",
                "work_result": "Плата заменена",
            }),
        ]
        result = None
        for index, (action, values) in enumerate(transitions):
            values["idempotency_key"] = f"paid-flow-{index}"
            result = self.action(repair["id"], action, **values)
            self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        data = result.get_json()["data"]
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["location"], "delivered")
        self.assertEqual(data["agreed_cost"], "2500.50")
        self.assertEqual(data["payment_amount"], "2500.50")
        self.assertTrue(data["paid_at"])
        self.assertTrue(data["completed_at"])
        self.assertFalse(data["control_date"])
        self.assertGreaterEqual(len(data["history"]), len(transitions) + 1)

    def test_diagnostics_and_paid_cost_are_required(self):
        repair = self.create()
        cases = load_repair_file(self.store)
        cases[0]["status"] = "diagnostics"
        web.save_repair_cases(cases)
        missing = self.action(repair["id"], "finish_diagnostics")
        self.assertEqual(missing.status_code, 409)
        cases = load_repair_file(self.store)
        cases[0]["status"] = "waiting_decision"
        web.save_repair_cases(cases)
        missing_cost = self.action(repair["id"], "accept_paid", customer_decision="Да")
        self.assertEqual(missing_cost.status_code, 409)
        negative = self.action(
            repair["id"], "accept_paid", customer_decision="Да", agreed_cost="-1"
        )
        self.assertEqual(negative.status_code, 409)

    def test_warranty_repair_can_be_free(self):
        repair = self.create(request_type="warranty_repair")
        cases = load_repair_file(self.store)
        cases[0]["status"] = "waiting_decision"
        web.save_repair_cases(cases)
        response = self.action(repair["id"], "accept_free", customer_decision="Согласовано по гарантии")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "in_repair")

    def test_logistics_fields_are_separate_and_pickup_needs_no_waybill(self):
        repair = self.create(incoming_waybill="IN-100")
        cases = load_repair_file(self.store)
        cases[0]["status"] = "ready_return"
        web.save_repair_cases(cases)
        completed = self.action(
            repair["id"], "complete", return_method="pickup",
            completion_result="returned_unrepaired", work_result="Диагностика завершена",
        )
        self.assertEqual(completed.status_code, 200, completed.get_data(as_text=True))
        data = completed.get_json()["data"]
        self.assertEqual(data["incoming_waybill"], "IN-100")
        self.assertFalse(data["outgoing_waybill"])

    def test_delivery_requires_outgoing_waybill(self):
        repair = self.create()
        cases = load_repair_file(self.store)
        cases[0]["status"] = "ready_return"
        web.save_repair_cases(cases)
        response = self.action(repair["id"], "send_to_customer", return_method="cdek")
        self.assertEqual(response.status_code, 409)

    def test_completion_cancellation_and_reopen_require_business_fields(self):
        repair = self.create()
        cases = load_repair_file(self.store)
        cases[0]["status"] = "ready_return"
        web.save_repair_cases(cases)
        self.assertEqual(self.action(repair["id"], "complete", return_method="pickup").status_code, 409)
        cases = load_repair_file(self.store)
        cases[0]["status"] = "new"
        web.save_repair_cases(cases)
        self.assertEqual(self.action(repair["id"], "cancel", reason="").status_code, 409)
        cancelled = self.action(repair["id"], "cancel", reason="Создано ошибочно")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["data"]["status"], "cancelled")
        no_reason = self.action(repair["id"], "reopen", reason="", status="waiting_diagnostics")
        self.assertEqual(no_reason.status_code, 409)
        reopened = self.action(
            repair["id"], "reopen", reason="Клиент вернулся",
            status="waiting_diagnostics", next_action="Провести диагностику",
        )
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.get_json()["data"]["status"], "waiting_diagnostics")

    def test_double_post_is_idempotent(self):
        repair = self.create()
        payload = {
            "next_action": "Ждать отправку", "waiting_for": "customer",
            "control_date": (date.today() + timedelta(days=2)).isoformat(),
            "idempotency_key": "same-key",
        }
        first = self.client.post(
            f"/api/v1/repairs/{repair['id']}/actions/request_shipment",
            json=payload, headers={"Idempotency-Key": "same-key"},
        )
        first_history_count = len(load_repair_file(self.store)[0]["history"])
        second = self.client.post(
            f"/api/v1/repairs/{repair['id']}/actions/request_shipment",
            json=payload, headers={"Idempotency-Key": "same-key"},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["meta"]["repeated"])
        self.assertEqual(
            len(load_repair_file(self.store)[0]["history"]),
            first_history_count,
        )

    def test_search_filters_reset_url_and_attention_sort(self):
        overdue = self.create(
            client_name="Анна", contact="anna@example.test", model="Lunar",
            product_name="Lunar", incoming_waybill="IN-777",
            control_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        future = self.create(
            client_name="Борис", contact="@boris", model="Voyager",
            product_name="Voyager", waiting_for="customer",
            control_date=(date.today() + timedelta(days=5)).isoformat(),
        )
        cases = load_repair_file(self.store)
        cases[1]["outgoing_waybill"] = "OUT-888"
        cases[1]["order_number"] = "ORDER-888"
        web.save_repair_cases(cases)
        for query in ("анна", "anna@example", "lunar", "in-777"):
            response = self.client.get(f"/api/v1/repairs?q={query}&view=all")
            self.assertEqual(response.get_json()["meta"]["total"], 1)
            self.assertEqual(response.get_json()["data"][0]["id"], overdue["id"])
        for query in ("order-888", "out-888"):
            response = self.client.get(f"/api/v1/repairs?q={query}&view=all")
            self.assertEqual(response.get_json()["meta"]["total"], 1)
            self.assertEqual(response.get_json()["data"][0]["id"], future["id"])
        filtered = self.client.get("/api/v1/repairs?view=all&location=with_customer&waiting_for=customer")
        self.assertEqual(filtered.get_json()["meta"]["total"], 1)
        self.assertEqual(filtered.get_json()["data"][0]["id"], future["id"])
        attention = self.client.get("/api/v1/repairs?view=all&attention=1")
        self.assertEqual(attention.get_json()["data"][0]["id"], overdue["id"])
        page = self.client.get("/app/repairs?q=анна&view=all&attention=1")
        html = page.get_data(as_text=True)
        self.assertIn('value="анна"', html)
        self.assertIn('name="attention" value="1" checked', html)
        self.assertIn('href="/app/repairs"', html)
        ordered = sorted(load_repair_file(self.store), key=repair_attention_key)
        self.assertEqual(ordered[0]["id"], overdue["id"])

    def test_control_date_today_filter(self):
        repair = self.create(control_date=date.today().isoformat(), waiting_for="customer")
        response = self.client.get("/api/v1/repairs?view=all&control=today")
        self.assertEqual(response.get_json()["meta"]["total"], 1)
        self.assertEqual(response.get_json()["data"][0]["id"], repair["id"])

    def test_order_removal_cannot_delete_repair(self):
        repair = self.create(order_source="our", order_id="20735", order_item_id="p-1:position:1")
        self.order = None
        stored = load_repair_file(self.store)
        self.assertEqual(stored[0]["id"], repair["id"])
        self.assertEqual(stored[0]["order_id"], "20735")

    def test_list_does_not_return_full_history_per_row(self):
        self.create()
        response = self.client.get("/api/v1/repairs?view=all&page_size=50")
        row = response.get_json()["data"][0]
        self.assertNotIn("history", row)
        self.assertIn("latest_event", row)

    def test_list_reads_repair_store_once_without_per_row_history_queries(self):
        self.create(client_name="Первый")
        self.create(client_name="Второй")
        original = web.load_repair_cases
        with mock.patch.object(web, "load_repair_cases", wraps=original) as loader:
            response = self.client.get("/api/v1/repairs?view=all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(loader.call_count, 1)

    def test_page_has_required_desktop_mobile_drawers_and_no_forbidden_kpi(self):
        self.create()
        html = self.client.get("/app/repairs").get_data(as_text=True)
        self.assertIn("Учёт ремонтных обращений", html)
        self.assertIn("Добавить ремонт", html)
        self.assertIn("repair-table", html)
        self.assertIn("repair-mobile-card", html)
        self.assertIn("repair-drawer", html)
        self.assertIn("Контрольная дата", html)
        self.assertNotIn("Номер ремонта", html)
        self.assertNotIn("Серийный номер", html)
        self.assertNotIn("KPI", html)

    def test_v3_migration_preserves_unknown_legacy_data_and_creates_backup(self):
        legacy = [{
            "id": "legacy-1", "schema_version": 2, "status": "at_master",
            "request_type": "paid_repair", "location": "with_master",
            "client_name": "Старый клиент", "product_name": "Старые часы",
            "problem": "Не идут", "unknown_business_field": "Сохранить",
            "created_at": "2025-01-01 10:00", "history": [],
        }]
        self.store.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        backup = self.root / "backups"
        report = migrate_repair_file(self.store, apply=True, backup_dir=backup)
        migrated = json.loads(self.store.read_text(encoding="utf-8"))[0]
        self.assertEqual(report["schema_version"], 3)
        self.assertTrue(Path(report["backup_path"]).is_file())
        self.assertEqual(migrated["unknown_business_field"], "Сохранить")
        self.assertEqual(migrated["status"], "diagnostics")
        self.assertEqual(migrated["legacy_snapshot"]["unknown_business_field"], "Сохранить")

    def test_authenticated_mutation_requires_csrf_and_employee_cannot_manual_status(self):
        auth_path = self.root / "auth.db"
        store = AuthStore(auth_path)
        user_id = store.create_initial_admin("Иван", "Сотрудник", "employee@example.test", "StrongPassword!234")
        with store.connect() as connection:
            connection.execute("UPDATE users SET role = 'employee' WHERE id = ?", (user_id,))
        web.app.config.update(AUTH_TESTING=True, AUTH_DATABASE=str(auth_path))
        client = web.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["_csrf_token"] = "repair-csrf"
        rejected = client.post("/api/v1/repairs", json=base_payload())
        self.assertEqual(rejected.status_code, 403)
        created = client.post(
            "/api/v1/repairs", json=base_payload(),
            headers={"X-CSRF-Token": "repair-csrf"},
        )
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        repair_id = created.get_json()["data"]["id"]
        forbidden = client.post(
            f"/api/v1/repairs/{repair_id}/status",
            json={"status": "diagnostics", "comment": "Исправление"},
            headers={"X-CSRF-Token": "repair-csrf"},
        )
        self.assertEqual(forbidden.status_code, 403)


if __name__ == "__main__":
    unittest.main()
