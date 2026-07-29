import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.services.legacy_repair_import import (
    LegacyRepairImportError,
    import_legacy_repair_file,
    load_import_dataset,
    plan_legacy_repair_import,
)
from app.services.repair_cases import (
    load_repair_file,
    migrate_repair_case,
)
from scripts import import_legacy_repairs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = (
    PROJECT_ROOT / "scripts" / "data" / "legacy_repairs_2026.json"
)
IMPORTED_AT = "2026-07-29 15:00"


def order_snapshot(order_number):
    return {
        "ok": True,
        "order_number": str(order_number),
        "order_date": "2026-01-31",
        "client": {
            "client_name": f"Клиент {order_number}",
            "client_phone": f"+7{str(order_number)[-10:]:0>10}",
            "client_email": f"{order_number}@example.test",
        },
        "products": [
            {"id": f"product-{order_number}", "name": f"Товар {order_number}"}
        ],
    }


class LegacyRepairImportTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.cases_path = self.root / "instance" / "repair_cases.json"
        self.backup_dir = self.root / "backups"
        self.dataset = load_import_dataset(DATASET_PATH)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_apply_is_idempotent_and_creates_backup(self):
        dry_run = import_legacy_repair_file(
            self.cases_path,
            self.dataset,
            imported_at=IMPORTED_AT,
            order_resolver=order_snapshot,
        )
        applied = import_legacy_repair_file(
            self.cases_path,
            self.dataset,
            apply=True,
            backup_dir=self.backup_dir,
            imported_at=IMPORTED_AT,
            order_resolver=order_snapshot,
        )
        cases_after_first_run = load_repair_file(self.cases_path)
        second_run = import_legacy_repair_file(
            self.cases_path,
            self.dataset,
            apply=True,
            backup_dir=self.backup_dir,
            imported_at=IMPORTED_AT,
            order_resolver=order_snapshot,
        )
        cases_after_second_run = load_repair_file(self.cases_path)

        self.assertEqual(dry_run["counts"]["create"], 11)
        self.assertEqual(applied["counts"]["create"], 11)
        self.assertEqual(applied["counts"]["update"], 0)
        self.assertEqual(applied["counts"]["skip"], 0)
        self.assertEqual(len(cases_after_first_run), 11)
        self.assertEqual(second_run["counts"]["skip"], 11)
        self.assertEqual(len(cases_after_second_run), 11)
        self.assertEqual(
            {case["id"] for case in cases_after_first_run},
            {case["id"] for case in cases_after_second_run},
        )
        backup_path = Path(applied["backup_path"])
        self.assertTrue(backup_path.is_file())
        self.assertEqual(
            json.loads(backup_path.read_text(encoding="utf-8")),
            [],
        )
        self.assertEqual(len(applied["backup_sha256"]), 64)

    def test_tracking_numbers_are_strings_and_directions_are_preserved(self):
        applied = import_legacy_repair_file(
            self.cases_path,
            self.dataset,
            apply=True,
            backup_dir=self.backup_dir,
            imported_at=IMPORTED_AT,
        )
        cases = load_repair_file(self.cases_path)
        source_cases = {
            case["legacy_import"]["source_key"]: case
            for case in cases
        }

        self.assertEqual(applied["counts"]["create"], 11)
        all_shipments = [
            shipment
            for case in cases
            for shipment in case["shipments"]
        ]
        self.assertTrue(all(
            isinstance(shipment["track_number"], str)
            for shipment in all_shipments
        ))
        case_20735 = source_cases["repair-02-order-20735"]
        self.assertEqual(
            [
                (shipment["track_number"], shipment["direction"])
                for shipment in case_20735["shipments"]
            ],
            [
                ("10299847838", "inbound"),
                ("10296685636", "outbound"),
            ],
        )
        case_20953 = source_cases["repair-03-order-20953"]
        self.assertEqual(
            case_20953["shipments"][0]["direction"],
            "unknown",
        )

    def test_empty_optional_fields_and_dates_remain_empty(self):
        import_legacy_repair_file(
            self.cases_path,
            self.dataset,
            apply=True,
            backup_dir=self.backup_dir,
            imported_at=IMPORTED_AT,
        )
        cases = {
            case["legacy_import"]["source_key"]: case
            for case in load_repair_file(self.cases_path)
        }

        telegram_case = cases["repair-01-telegram-ereminvasiliy"]
        unknown_problem = cases["repair-03-order-20953"]
        self.assertEqual(telegram_case["client_name"], "")
        self.assertEqual(telegram_case["product_name"], "")
        self.assertEqual(telegram_case["request_at"], "")
        self.assertEqual(unknown_problem["problem"], "")
        self.assertEqual(unknown_problem["accepted_at"], "")

    def test_two_repairs_can_share_one_order_without_merging(self):
        import_legacy_repair_file(
            self.cases_path,
            self.dataset,
            apply=True,
            backup_dir=self.backup_dir,
            imported_at=IMPORTED_AT,
            order_resolver=order_snapshot,
        )
        cases_18593 = [
            case
            for case in load_repair_file(self.cases_path)
            if case["order_number"] == "18593"
        ]

        self.assertEqual(len(cases_18593), 2)
        self.assertEqual(
            {
                case["legacy_import"]["source_key"]
                for case in cases_18593
            },
            {
                "repair-09-order-18593-initial",
                "repair-11-order-18593-warranty",
            },
        )
        self.assertEqual(len({case["id"] for case in cases_18593}), 2)

    def test_multiline_notes_and_russian_text_are_preserved(self):
        import_legacy_repair_file(
            self.cases_path,
            self.dataset,
            apply=True,
            backup_dir=self.backup_dir,
            imported_at=IMPORTED_AT,
        )
        case = next(
            case
            for case in load_repair_file(self.cases_path)
            if case["legacy_import"]["source_key"]
            == "repair-08-order-20988"
        )

        self.assertIn("\n1. «направили часы ему", case["internal_comment"])
        self.assertIn("\n2. «часы у нас", case["internal_comment"])
        self.assertIn(
            "Статус перенесён из старого Excel, требуется проверка.",
            case["legacy_import"]["review_notes"],
        )

    def test_imported_cards_open_and_filter_by_status(self):
        import_legacy_repair_file(
            self.cases_path,
            self.dataset,
            apply=True,
            backup_dir=self.backup_dir,
            imported_at=IMPORTED_AT,
        )
        web.app.config.update(TESTING=True)
        with mock.patch.object(
            web,
            "get_repair_cases_path",
            return_value=self.cases_path,
        ), mock.patch.object(
            web,
            "get_excel_warehouse_items",
            return_value=[],
        ):
            client = web.app.test_client()
            page = client.get("/repair")
            filtered = client.get("/repair?status=waiting_payment")

        page_html = page.get_data(as_text=True)
        filtered_html = filtered.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(filtered.status_code, 200)
        self.assertIn("R-2026-0001", page_html)
        self.assertIn("R-2026-0011", page_html)
        self.assertIn("Периодически не переключаются диски", page_html)
        self.assertIn('class="mobile-card"', page_html)
        self.assertIn("R-2026-0005", filtered_html)
        self.assertNotIn("R-2026-0007", filtered_html)

    def test_matching_waybill_updates_existing_card_without_overwriting(self):
        existing = migrate_repair_case({
            "id": "existing-case",
            "schema_version": 2,
            "repair_number": "R-2026-0099",
            "created_at": "2026-07-01 10:00",
            "updated_at": "2026-07-01 10:00",
            "status": "waiting_decision",
            "request_type": "paid_repair",
            "location": "at_us",
            "order_number": "20735",
            "order_source": "our",
            "client_name": "Существующий клиент",
            "product_name": "Существующий товар",
            "problem": "Существующее описание",
            "shipments": [{
                "id": "existing-shipment",
                "direction": "inbound",
                "carrier": "СДЭК",
                "track_number": "10299847838",
                "sent_at": "",
                "status": "",
                "received_at": "",
            }],
            "history": [],
        }, migrated_at=IMPORTED_AT)

        plan = plan_legacy_repair_import(
            [existing],
            self.dataset,
            imported_at=IMPORTED_AT,
            order_resolver=order_snapshot,
        )
        updated = next(
            case for case in plan["result_cases"]
            if case["id"] == "existing-case"
        )

        self.assertEqual(plan["counts"]["update"], 1)
        self.assertEqual(plan["counts"]["create"], 10)
        self.assertEqual(updated["client_name"], "Существующий клиент")
        self.assertEqual(updated["product_name"], "Существующий товар")
        self.assertEqual(updated["status"], "waiting_decision")
        self.assertEqual(
            {shipment["track_number"] for shipment in updated["shipments"]},
            {"10299847838", "10296685636"},
        )
        self.assertEqual(
            updated["legacy_import"]["source_key"],
            "repair-02-order-20735",
        )

    def test_ambiguous_duplicate_stops_apply(self):
        source = self.dataset["records"][1]
        duplicate_dataset = copy.deepcopy(self.dataset)
        duplicate_dataset["records"] = [copy.deepcopy(source)]
        existing_cases = []
        for suffix in ("one", "two"):
            existing_cases.append(migrate_repair_case({
                "id": suffix,
                "schema_version": 2,
                "repair_number": f"R-2026-{suffix}",
                "created_at": IMPORTED_AT,
                "updated_at": IMPORTED_AT,
                "status": "new",
                "request_type": "diagnostics",
                "location": "unknown",
                "client_name": "",
                "product_name": "",
                "problem": "",
                "shipments": [{
                    "track_number": "10299847838",
                    "direction": "inbound",
                }],
            }, migrated_at=IMPORTED_AT))

        plan = plan_legacy_repair_import(
            existing_cases,
            duplicate_dataset,
            imported_at=IMPORTED_AT,
        )
        self.assertEqual(plan["counts"]["manual"], 1)
        with self.assertRaises(LegacyRepairImportError):
            self.cases_path.parent.mkdir(parents=True, exist_ok=True)
            self.cases_path.write_text(
                json.dumps(existing_cases, ensure_ascii=False),
                encoding="utf-8",
            )
            import_legacy_repair_file(
                self.cases_path,
                duplicate_dataset,
                apply=True,
                backup_dir=self.backup_dir,
                imported_at=IMPORTED_AT,
            )

    @mock.patch.object(import_legacy_repairs.requests, "get")
    def test_order_lookup_uses_only_known_client_and_product_fields(
        self,
        get,
    ):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status": "ok",
            "order": {
                "id": "20735",
                "number": "20735",
                "date": "2026-01-31 10:06:58",
                "user": {"name": "Имя профиля", "email": "profile@test"},
                "properties": [
                    {"code": "FIO", "value": "Фаизова Вера"},
                    {"code": "EMAIL", "value": "client@example.test"},
                    {"code": "PHONE", "value": "+79990000000"},
                    {"code": "ADDRESS", "value": "Не переносить"},
                ],
                "products": [
                    {"id": "199482", "name": "LUNAR Black"},
                ],
            },
        }
        get.return_value = response

        snapshot = import_legacy_repairs.fetch_order_snapshot("20735")

        self.assertTrue(snapshot["ok"])
        self.assertEqual(
            snapshot["client"]["client_name"],
            "Фаизова Вера",
        )
        self.assertEqual(
            snapshot["products"],
            [{"id": "199482", "name": "LUNAR Black"}],
        )
        self.assertNotIn("address", json.dumps(snapshot).lower())

    def test_cli_prints_russian_report_under_ascii_server_locale(self):
        environment = os.environ.copy()
        environment.update({
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONIOENCODING": "ascii",
            "PYTHONUTF8": "0",
        })
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "import_legacy_repairs.py"),
                "--path",
                str(self.cases_path),
            ],
            cwd=str(PROJECT_ROOT),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            result.stderr.decode("utf-8", errors="replace"),
        )
        output = result.stdout.decode("utf-8")
        self.assertIn("Режим: DRY-RUN", output)
        self.assertIn("требуют проверки=10", output)


if __name__ == "__main__":
    unittest.main()
