import json
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from app import web
from app.services.repair_cases import (
    RepairDataError,
    load_repair_file,
    migrate_repair_case,
    migrate_repair_file,
    migrate_repair_cases,
    save_repair_file,
)


PRODUCT_ID = "42"


def catalog_item():
    return {
        "id": PRODUCT_ID,
        "name": "Vechasu Voyager Black",
        "brand": "Vechasu",
        "model": "Voyager",
        "article": "VV-42",
        "barcode": "4600000000042",
        "thumbnail_url": "/static/test-watch.png",
        "stock": 0,
    }


def repair_form(**changes):
    data = {
        "client_name": "Иван Петров",
        "client_phone": "+79990000000",
        "client_email": "ivan@example.test",
        "client_messenger": "@ivan",
        "order_source": "our",
        "order_number": "20735",
        "communication_channel": "telegram",
        "contact": "@ivan",
        "product_id": PRODUCT_ID,
        "product_name": "Vechasu Voyager Black",
        "brand": "Vechasu",
        "model": "Voyager",
        "article": "VV-42",
        "serial_number": "SN-42",
        "equipment": "Часы и коробка",
        "request_type": "warranty_repair",
        "status": "new",
        "location": "with_customer",
        "responsible": "Максим",
        "problem": "Часы не включаются",
        "diagnostic_result": "",
        "master_conclusion": "",
        "decision": "",
        "master": "",
        "estimate_cost": "0",
        "final_cost": "",
        "request_at": "2026-07-20",
        "customer_sent_at": "",
        "accepted_at": "",
        "master_handoff_at": "",
        "repair_completed_at": "",
        "returned_at": "",
        "due_date": "2026-08-10",
        "communication": "Клиент написал в Telegram",
        "internal_comment": "Гарантия проверена",
        "event_comment": "Первичное обращение",
    }
    data.update(changes)
    return data


class RepairWorkspaceTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_directory.name)
        self.cases_path = self.temp_path / "repair_cases.json"
        self.uploads_path = self.temp_path / "repair_uploads"
        self.patches = [
            mock.patch.object(
                web,
                "get_repair_cases_path",
                return_value=self.cases_path,
            ),
            mock.patch.object(
                web,
                "get_repair_uploads_path",
                return_value=self.uploads_path,
            ),
            mock.patch.object(
                web,
                "get_excel_warehouse_items",
                return_value=[catalog_item()],
            ),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp_directory.cleanup()

    def create_repair(self, **changes):
        response = self.client.post(
            "/repair/add",
            data=repair_form(**changes),
        )
        self.assertEqual(response.status_code, 302)
        cases = load_repair_file(self.cases_path)
        self.assertEqual(len(cases), 1)
        return cases[0]

    def test_migration_preserves_legacy_fields_comments_and_ambiguous_waybill(self):
        source = [{
            "id": "legacy-1",
            "repair_number": "R-2025-0001",
            "status": "waiting",
            "repair_type": "service",
            "order_number": "1658546",
            "client_name": "Анна",
            "client_phone": "+70000000000",
            "product_name": "Старые часы",
            "comment": "Старый комментарий",
            "internal_comment": "Не удалять историю",
            "track_number": "TRACK-LEGACY",
            "created_at": "2025-11-10 10:00",
        }]

        migrated, report = migrate_repair_cases(
            source,
            migrated_at="2026-07-29 12:00",
        )

        case = migrated[0]
        self.assertEqual(report["migrated_records"], 1)
        self.assertEqual(report["review_required"], 1)
        self.assertGreater(report["mapped_fields"], 0)
        self.assertEqual(case["status"], "waiting_decision")
        self.assertEqual(case["request_type"], "diagnostics")
        self.assertEqual(case["comment"], "Старый комментарий")
        self.assertEqual(
            case["legacy_snapshot"]["comment"],
            "Старый комментарий",
        )
        self.assertEqual(case["shipments"][0]["track_number"], "TRACK-LEGACY")
        self.assertEqual(case["shipments"][0]["direction"], "unknown")
        comments = [event["comment"] for event in case["history"]]
        self.assertIn("Старый комментарий", comments)
        self.assertIn("Не удалять историю", comments)

    def test_corrupt_store_is_not_silently_replaced(self):
        self.cases_path.write_text("{broken", encoding="utf-8")

        with self.assertRaises(RepairDataError):
            load_repair_file(self.cases_path)

        self.assertEqual(
            self.cases_path.read_text(encoding="utf-8"),
            "{broken",
        )

    def test_migration_apply_creates_backup_and_report(self):
        self.cases_path.write_text(
            json.dumps([{
                "id": "legacy-apply",
                "status": "ready",
                "repair_type": "paid",
                "client_name": "Иван",
                "product_name": "Часы",
                "problem": "Не идут",
            }], ensure_ascii=False),
            encoding="utf-8",
        )
        backup_dir = self.temp_path / "backups"

        report = migrate_repair_file(
            self.cases_path,
            apply=True,
            backup_dir=backup_dir,
        )

        self.assertTrue(report["applied"])
        self.assertEqual(report["migrated_records"], 1)
        self.assertTrue(Path(report["backup_path"]).is_file())
        self.assertTrue(
            (self.temp_path / "repair_migration_report.json").is_file()
        )
        migrated = json.loads(
            self.cases_path.read_text(encoding="utf-8")
        )
        self.assertEqual(migrated[0]["schema_version"], 2)
        self.assertEqual(migrated[0]["status"], "at_us")

    def test_create_links_catalog_product_and_creates_history(self):
        case = self.create_repair()

        self.assertEqual(case["product_id"], PRODUCT_ID)
        self.assertEqual(case["product_name"], "Vechasu Voyager Black")
        self.assertEqual(case["brand"], "Vechasu")
        self.assertEqual(case["article"], "VV-42")
        self.assertEqual(case["product_url"], "/products/42")
        self.assertEqual(case["status"], "new")
        self.assertEqual(case["schema_version"], 2)
        self.assertTrue(case["history"])
        self.assertEqual(
            case["history"][0]["action"],
            "Карточка ремонта создана",
        )

    def test_imported_incomplete_case_can_be_edited_without_fake_fields(self):
        imported = migrate_repair_case({
            "id": "legacy-incomplete",
            "schema_version": 2,
            "repair_number": "R-2026-0100",
            "created_at": "2026-07-29 15:00",
            "updated_at": "2026-07-29 15:00",
            "status": "new",
            "request_type": "diagnostics",
            "location": "unknown",
            "order_number": "20953",
            "order_source": "our",
            "client_name": "",
            "product_name": "",
            "problem": "",
            "legacy_import": {
                "source_key": "repair-03-order-20953",
            },
        })
        save_repair_file(self.cases_path, [imported])
        response = self.client.post(
            "/repair/update",
            data=repair_form(
                case_id="legacy-incomplete",
                client_name="",
                product_id="",
                product_name="",
                problem="",
                status="waiting_decision",
            ),
        )
        stored = load_repair_file(self.cases_path)[0]

        self.assertEqual(response.status_code, 302)
        self.assertEqual(stored["client_name"], "")
        self.assertEqual(stored["product_name"], "")
        self.assertEqual(stored["problem"], "")
        self.assertEqual(stored["status"], "waiting_decision")

    def test_attachment_is_saved_and_downloaded_from_case_card(self):
        data = repair_form()
        data["attachments"] = (
            BytesIO(b"fake-image-content"),
            "watch-photo.jpg",
        )

        response = self.client.post(
            "/repair/add",
            data=data,
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        case = load_repair_file(self.cases_path)[0]
        self.assertEqual(len(case["attachments"]), 1)
        attachment = case["attachments"][0]
        stored_path = (
            self.uploads_path
            / case["id"]
            / attachment["stored_name"]
        )
        self.assertTrue(stored_path.is_file())
        download = self.client.get(
            f"/repair/attachment/{case['id']}/{attachment['stored_name']}"
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"fake-image-content")
        download.close()

    def test_edit_and_status_change_append_history_without_erasing_old_events(self):
        created = self.create_repair()
        original_event_ids = [event["id"] for event in created["history"]]

        updated_form = repair_form(
            case_id=created["id"],
            status="waiting_payment",
            location="at_us",
            diagnostic_result="Нужна замена платы",
            master_conclusion="Ремонт возможен",
            decision="Согласовать оплату",
            event_comment="Диагностика завершена",
        )
        updated = self.client.post("/repair/update", data=updated_form)
        changed_status = self.client.post(
            "/repair/status",
            data={
                "case_id": created["id"],
                "status": "outbound_transit",
                "comment": "Передано в доставку",
            },
        )

        self.assertEqual(updated.status_code, 302)
        self.assertEqual(changed_status.status_code, 302)
        case = load_repair_file(self.cases_path)[0]
        self.assertEqual(case["status"], "outbound_transit")
        self.assertEqual(case["decision"], "Согласовать оплату")
        self.assertTrue(
            set(original_event_ids).issubset(
                {event["id"] for event in case["history"]}
            )
        )
        self.assertGreater(len(case["history"]), len(original_event_ids))
        self.assertTrue(any(
            event["field"] == "status"
            for event in case["history"]
        ))
        self.assertTrue(any(
            event["field"] == "decision"
            for event in case["history"]
        ))

    def test_live_status_change_returns_updated_case_for_metrics(self):
        case = self.create_repair()

        response = self.client.post(
            "/repair/status",
            data={
                "case_id": case["id"],
                "status": "at_master",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["case"]["status"], "at_master")
        self.assertEqual(payload["case"]["status_label"], "У мастера")
        self.assertEqual(
            payload["case"]["latest_event"],
            "У мастера",
        )

    def test_quick_actions_record_dates_locations_and_history(self):
        case = self.create_repair()
        actions = ("receive", "handoff", "return", "complete")

        for action in actions:
            response = self.client.post(
                "/repair/action",
                data={"case_id": case["id"], "action": action},
            )
            self.assertEqual(response.status_code, 302)

        stored = load_repair_file(self.cases_path)[0]
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["location"], "outbound_transit")
        self.assertTrue(stored["accepted_at"])
        self.assertTrue(stored["master_handoff_at"])
        self.assertTrue(stored["repair_completed_at"])
        actions_in_history = {
            event["action"] for event in stored["history"]
        }
        self.assertIn("Часы приняты", actions_in_history)
        self.assertIn("Часы переданы мастеру", actions_in_history)
        self.assertIn("Оформлен возврат клиенту", actions_in_history)
        self.assertIn("Ремонт завершён", actions_in_history)

    def test_multiple_shipments_persist_after_reload(self):
        case = self.create_repair()

        for direction, track in (
            ("inbound", "TRACK-IN"),
            ("outbound", "TRACK-OUT"),
        ):
            response = self.client.post(
                "/repair/logistics/add",
                data={
                    "case_id": case["id"],
                    "direction": direction,
                    "carrier": "СДЭК",
                    "track_number": track,
                    "sent_at": "2026-07-22",
                    "shipment_status": "В пути",
                },
            )
            self.assertEqual(response.status_code, 302)

        reloaded = load_repair_file(self.cases_path)[0]
        self.assertEqual(len(reloaded["shipments"]), 2)
        self.assertEqual(
            [item["track_number"] for item in reloaded["shipments"]],
            ["TRACK-IN", "TRACK-OUT"],
        )
        self.assertEqual(reloaded["status"], "outbound_transit")
        shipment_events = [
            event
            for event in reloaded["history"]
            if event["action"] == "Добавлена накладная"
        ]
        self.assertEqual(len(shipment_events), 2)

    def test_archive_and_restore_keep_full_history(self):
        case = self.create_repair()
        history_before = list(case["history"])

        archived = self.client.post(
            "/repair/delete",
            data={"case_id": case["id"]},
        )
        restored = self.client.post(
            "/repair/action",
            data={"case_id": case["id"], "action": "restore"},
        )

        self.assertEqual(archived.status_code, 302)
        self.assertEqual(restored.status_code, 302)
        stored = load_repair_file(self.cases_path)[0]
        self.assertEqual(stored["archived_at"], "")
        self.assertGreater(len(stored["history"]), len(history_before))
        self.assertEqual(
            stored["history"][0]["id"],
            history_before[0]["id"],
        )

    def test_page_has_exactly_nine_columns_drawer_and_mobile_cards(self):
        self.create_repair()

        response = self.client.get("/repair")
        html = response.get_data(as_text=True)
        header = re.search(
            r"<thead>(.*?)</thead>",
            html,
            flags=re.DOTALL,
        ).group(1)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(re.findall(r"<th\b", header)), 9)
        for label in (
            "Статус",
            "Заказ / клиент",
            "Товар",
            "Связь",
            "Обращение",
            "Логистика",
            "Ключевые даты",
            "Последнее событие",
            "Действия",
        ):
            self.assertIn(label, header)
        self.assertIn('id="repairDrawer"', html)
        self.assertIn('class="mobile-card"', html)
        self.assertIn("immutable", "immutable history contract")
        self.assertIn("@media (max-width: 900px)", html)

    def test_search_status_period_and_metrics_use_structured_data(self):
        first = self.create_repair(
            status="at_master",
            location="with_master",
            order_number="ORDER-ONE",
            request_at="2026-07-10",
        )
        second = dict(first)
        second.update({
            "id": "repair-2",
            "repair_number": "R-2026-0002",
            "order_number": "ORDER-TWO",
            "client_name": "Мария",
            "status": "inbound_transit",
            "location": "inbound_transit",
            "request_at": "2026-07-25",
            "shipments": [{
                "id": "shipment-2",
                "direction": "inbound",
                "carrier": "Почта России",
                "track_number": "TRACK-SEARCH",
                "sent_at": "2026-07-24",
                "status": "В пути",
                "received_at": "",
            }],
        })
        save_repair_file(self.cases_path, [first, second])

        page = self.client.get("/repair")
        search = self.client.get("/repair?q=TRACK-SEARCH")
        filtered = self.client.get(
            "/repair?status=inbound_transit"
            "&date_from=2026-07-20&date_to=2026-07-29"
        )
        excluded = self.client.get(
            "/repair?status=inbound_transit&date_to=2026-07-20"
        )

        page_html = page.get_data(as_text=True)
        self.assertIn('id="metricTotal" class="metric-value">2', page_html)
        self.assertIn('id="metricMaster" class="metric-value">1', page_html)
        self.assertIn('id="metricDelivery" class="metric-value">1', page_html)
        self.assertIn("ORDER-TWO", search.get_data(as_text=True))
        self.assertNotIn("ORDER-ONE", search.get_data(as_text=True))
        self.assertIn("ORDER-TWO", filtered.get_data(as_text=True))
        self.assertNotIn("ORDER-ONE", filtered.get_data(as_text=True))
        self.assertNotIn("ORDER-TWO", excluded.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
