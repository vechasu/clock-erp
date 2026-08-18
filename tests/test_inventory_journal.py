import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.brand_inventory import BrandInventory
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.inventory_journal import InventoryJournal


class InventoryJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,"
                "total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','test.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-18T09:00:00+00:00", "2026-08-18T09:00:00+00:00"),
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.inventory = BrandInventory(self.database)
        self.journal = AuditJournal(self.database)
        self.read_model = InventoryJournal(self.database)
        self.product = self.catalog.create_product(
            name="Часы Alpha", article="ALPHA-1", brand="Alpha",
            category="Часы", stock=4,
        )
        with self.database.connect() as connection:
            self.brand_id = connection.execute(
                "SELECT brand_id FROM catalog_excel_products WHERE id = ?", (self.product["id"],)
            ).fetchone()[0]
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}
        )
        self.environment.start()
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def start(self, user="Максим"):
        return self.inventory.start(self.brand_id, user)[0]

    def item(self, session):
        return self.inventory.list_items(session["id"])[0]

    def event(self, session):
        events = self.journal.list_events(
            entity_type="inventory", entity_id=session["id"], limit=10
        )["events"]
        self.read_model.enrich_events(events)
        return events[0]

    def test_01_start_creates_one_inventory_document_event(self):
        session = self.start()
        event = self.event(session)
        self.assertEqual(event["object_label_snapshot"], "Инвентаризация · Alpha")
        self.assertEqual(event["actor_display_name_snapshot"], "Максим")

    def test_02_continue_does_not_duplicate_document_event(self):
        session = self.start()
        continued, created = self.inventory.start(self.brand_id, "Анна")
        self.assertFalse(created)
        self.assertEqual(continued["id"], session["id"])
        self.assertEqual(len(self.journal.list_events(entity_type="inventory", limit=10)["events"]), 1)

    def test_03_active_progress_keeps_pending_separate_from_missing(self):
        session = self.start()
        summary = self.event(session)["inventory_summary"]
        self.assertEqual((summary["pending_positions"], summary["missing_positions"]), (1, 0))

    def test_04_confirm_without_change_is_visible_without_movement(self):
        session = self.start()
        self.inventory.confirm(session["id"], self.item(session)["id"], 4, "Максим", "same")
        document = self.read_model.get_document(session["id"])
        self.assertEqual(document["positions"][0]["result"], "Подтверждён")
        self.assertEqual(
            document["positions"][0]["action_type"], "inventory_item_confirmed"
        )
        self.assertIsNone(document["positions"][0]["movement_id"])

    def test_05_positive_adjustment_has_totals_and_canonical_movement(self):
        session = self.start()
        self.inventory.confirm(session["id"], self.item(session)["id"], 6, "Максим", "up")
        document = self.read_model.get_document(session["id"])
        self.assertEqual(document["summary"]["positive_delta"], 2)
        self.assertEqual(document["positions"][0]["result"], "Излишек")
        self.assertTrue(document["positions"][0]["movement_id"])

    def test_06_negative_adjustment_has_totals(self):
        session = self.start()
        self.inventory.confirm(session["id"], self.item(session)["id"], 2, "Максим", "down")
        document = self.read_model.get_document(session["id"])
        self.assertEqual(document["summary"]["negative_delta"], -2)
        self.assertEqual(document["positions"][0]["result"], "Недостача")

    def test_07_new_product_is_one_added_position(self):
        session = self.start()
        self.inventory.add_new(session["id"], "Новые часы", "NEW-1", 2, "Анна", "new")
        document = self.read_model.get_document(session["id"])
        self.assertEqual(document["summary"]["added_positions"], 1)
        self.assertIn("Добавлен", [item["result"] for item in document["positions"]])

    def test_08_archived_product_is_marked_reactivated(self):
        archived = self.catalog.create_product(
            name="Архивные часы", article="OLD-1", brand="Alpha", category="Часы", stock=0
        )
        self.catalog.delete_product(archived["id"])
        session = self.start()
        self.inventory.add_existing(session["id"], archived["id"], 1, "Анна", "restore")
        document = self.read_model.get_document(session["id"])
        self.assertEqual(document["summary"]["reactivated_positions"], 1)
        self.assertIn("Реактивирован", [item["result"] for item in document["positions"]])

    def test_09_completion_marks_only_unchecked_snapshot_as_missing(self):
        session = self.start()
        self.inventory.complete(session["id"], "Максим", confirmation=True)
        summary = self.read_model.get_document(session["id"])["summary"]
        self.assertEqual((summary["status"], summary["missing_positions"], summary["pending_positions"]), ("completed", 1, 0))

    def test_10_conflict_is_stored_and_visible(self):
        session = self.start()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 3 WHERE id = ?",
                (self.product["id"],),
            )
            connection.execute(
                "INSERT INTO catalog_stock_movements "
                "(id, product_id, movement_type, quantity_delta, stock_before, "
                "stock_after, source, created_at) VALUES "
                "('external-conflict', ?, 'manual_adjustment', -1, 4, 3, "
                "'test', '2026-08-18T10:00:00+00:00')",
                (self.product["id"],),
            )
        self.inventory.confirm(session["id"], self.item(session)["id"], 4, "Максим", "conflict")
        position = self.read_model.get_document(session["id"])["positions"][0]
        self.assertEqual(position["result"], "Конфликт")
        self.assertIn("изменился", position["error"])

    def test_11_cancelled_document_keeps_reason_and_actor(self):
        session = self.start()
        self.inventory.cancel(session["id"], "Перенос", "Анна")
        summary = self.read_model.get_document(session["id"])["summary"]
        self.assertEqual((summary["status"], summary["cancelled_by"], summary["cancelled_reason"]), ("cancelled", "Анна", "Перенос"))

    def test_12_detail_rejects_unrelated_movement_link(self):
        session = self.start()
        self.inventory.confirm(session["id"], self.item(session)["id"], 5, "Максим", "move")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_stock_movements SET source_id = 'other' WHERE source_id = ?",
                (session["id"],),
            )
        self.assertIsNone(self.read_model.get_document(session["id"])["positions"][0]["movement_id"])

    def test_13_search_by_brand_and_document_id(self):
        session = self.start()
        self.assertEqual(self.journal.list_events(query="Alpha", limit=10)["events"][0]["entity_id"], session["id"])
        self.assertEqual(self.journal.list_events(query=session["id"], limit=10)["events"][0]["entity_id"], session["id"])

    def test_14_search_by_product_and_article(self):
        session = self.start()
        for query in ("Часы Alpha", "ALPHA-1"):
            self.assertEqual(self.journal.list_events(
                entity_type="inventory", query=query, limit=10
            )["events"][0]["entity_id"], session["id"])

    def test_15_filter_by_any_inventory_user(self):
        session = self.start()
        self.inventory.confirm(session["id"], self.item(session)["id"], 4, "Анна", "actor")
        self.assertEqual(self.journal.list_events(actor="Анна", limit=10)["events"][0]["entity_id"], session["id"])
        self.assertIn("Анна", self.journal.filter_options()["actors"])

    def test_16_type_and_period_filters_include_inventory(self):
        session = self.start()
        listing = self.journal.list_events(
            entity_type="inventory", date_from="2020-01-01", date_to="2030-01-01", limit=10
        )
        self.assertEqual(listing["events"][0]["entity_id"], session["id"])

    def test_17_page_has_inventory_filter_and_mobile_layout(self):
        self.start()
        markup = web.app.test_client().get("/app/journal?entity_type=inventory").get_data(as_text=True)
        self.assertIn('data-journal-tab="inventory"', markup)
        self.assertIn("inventory-position-values", markup)
        self.assertIn("@media (max-width: 420px)", markup)

    def test_18_detail_api_is_read_only(self):
        session = self.start()
        event = self.event(session)
        with self.database.connect() as connection:
            before = connection.execute("SELECT COUNT(*) FROM catalog_stock_movements").fetchone()[0]
        response = web.app.test_client().get("/api/journal/{}".format(event["id"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["inventory"]["summary"]["id"], session["id"])
        with self.database.connect() as connection:
            after = connection.execute("SELECT COUNT(*) FROM catalog_stock_movements").fetchone()[0]
        self.assertEqual(after, before)

    def test_19_collection_api_returns_one_aggregated_row(self):
        session = self.start()
        self.inventory.confirm(session["id"], self.item(session)["id"], 5, "Максим", "aggregate")
        response = web.app.test_client().get("/api/journal?entity_type=inventory")
        events = response.get_json()["data"]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "Активна · проверено 1 из 1")


if __name__ == "__main__":
    unittest.main()
