import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from app import web


def catalog_item():
    return {
        "id": "42",
        "name": "Vechasu Voyager",
        "brand": "Vechasu",
        "model": "Voyager",
        "article": "VV-42",
        "barcode": "4600000000042",
        "thumbnail_url": "/static/watch.png",
        "stock": 1,
    }


def repair_payload():
    return {
        "client_name": "Иван Петров",
        "client_phone": "+79990000000",
        "client_email": "ivan@example.test",
        "client_messenger": "@ivan",
        "order_source": "our",
        "order_number": "20735",
        "communication_channel": "telegram",
        "contact": "@ivan",
        "product_id": "42",
        "product_name": "Vechasu Voyager",
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


class Stage2RepairsApiTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cases_path = self.root / "repair_cases.json"
        self.uploads_path = self.root / "repair_uploads"
        self.patchers = [
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

    def test_repair_api_full_workspace_flow(self):
        catalog = self.client.get("/api/v1/repairs/catalog").get_json()
        self.assertEqual(catalog["data"][0]["name"], "Vechasu Voyager")

        created = self.client.post("/api/repairs", json=repair_payload())
        self.assertEqual(created.status_code, 201)
        repair = created.get_json()["data"]
        repair_id = repair["id"]
        self.assertEqual(repair["status_label"], "Новая")
        self.assertEqual(repair["product_name"], "Vechasu Voyager")

        listing = self.client.get(
            "/api/v1/repairs?q=иван&view=active&page_size=1"
        ).get_json()
        self.assertEqual(listing["meta"]["total"], 1)
        self.assertEqual(listing["meta"]["stats"]["active"], 1)
        self.assertTrue(listing["meta"]["facets"]["statuses"])

        updated = self.client.patch(
            f"/api/repairs/{repair_id}",
            json={
                "diagnostic_result": "Требуется замена платы",
                "decision": "Согласовать ремонт",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            updated.get_json()["data"]["diagnostic_result"],
            "Требуется замена платы",
        )
        self.assertEqual(updated.get_json()["data"]["client_name"], "Иван Петров")

        status = self.client.post(
            f"/api/repairs/{repair_id}/status",
            json={"status": "at_master"},
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["data"]["status"], "at_master")

        shipment = self.client.post(
            f"/api/repairs/{repair_id}/shipments",
            json={
                "direction": "outbound",
                "carrier": "СДЭК",
                "track_number": "TRACK-42",
                "sent_at": "2026-07-30",
            },
        )
        self.assertEqual(shipment.status_code, 201)
        self.assertEqual(
            shipment.get_json()["data"]["shipments"][0]["track_number"],
            "TRACK-42",
        )

        attachment = self.client.post(
            f"/api/repairs/{repair_id}/attachments",
            data={"attachments": (BytesIO(b"test"), "diagnostic.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(attachment.status_code, 201)
        self.assertEqual(
            attachment.get_json()["data"]["attachments"][0]["name"],
            "diagnostic.txt",
        )

        archived = self.client.delete(f"/api/repairs/{repair_id}")
        self.assertEqual(archived.status_code, 200)
        self.assertTrue(archived.get_json()["data"]["archived"])
        self.assertEqual(
            self.client.get("/api/repairs?view=archive").get_json()["meta"]["total"],
            1,
        )

        restored = self.client.post(
            f"/api/repairs/{repair_id}/restore",
            json={},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertFalse(restored.get_json()["data"]["is_archived"])

    def test_repair_api_validation_keeps_store_unchanged(self):
        invalid = self.client.post(
            "/api/repairs",
            json={"client_name": "", "product_name": "", "problem": ""},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["code"], "REPAIR_VALIDATION_FAILED")
        self.assertEqual(
            self.client.get("/api/repairs").get_json()["meta"]["total"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
