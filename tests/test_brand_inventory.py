import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from app import auth, web
from app.catalog_db import CatalogDatabase
from app.services.brand_inventory import BrandInventory, InventoryConflict, InventoryError
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import SalesInventory


class BrandInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,"
                "total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','test.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-18T09:00:00+00:00", "2026-08-18T09:00:00+00:00"),
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.service = BrandInventory(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def product(self, stock=4, name="Часы Alpha", article="A-1", active=True):
        item = self.catalog.create_product(
            name=name, article=article, brand="Alpha", category="Часы", stock=stock
        )
        if not active:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE catalog_excel_products SET active = 0 WHERE id = ?", (item["id"],)
                )
        return item

    def brand_id(self):
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT id FROM erp_brands WHERE normalized_name='alpha'"
            ).fetchone()[0]

    def stock(self, product_id):
        with self.database.connect() as connection:
            return int(connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id = ?", (product_id,)
            ).fetchone()[0])

    def movement_count(self, product_id):
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM catalog_stock_movements WHERE product_id = ? "
                "AND movement_type='inventory_adjustment'", (product_id,)
            ).fetchone()[0]

    def external_stock_change(self, product_id, stock_after):
        with self.database.transaction() as connection:
            stock_before = float(connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id = ?",
                (product_id,),
            ).fetchone()[0])
            connection.execute(
                "UPDATE catalog_excel_products SET stock = ? WHERE id = ?",
                (stock_after, product_id),
            )
            connection.execute(
                "INSERT INTO catalog_stock_movements "
                "(id, product_id, movement_type, quantity_delta, stock_before, "
                "stock_after, source, created_at) VALUES (?, ?, "
                "'manual_adjustment', ?, ?, ?, 'test', ?)",
                (
                    "external-{}-{}".format(product_id, stock_after),
                    product_id,
                    stock_after - stock_before,
                    stock_before,
                    stock_after,
                    "2026-08-18T10:00:00+00:00",
                ),
            )

    def start(self):
        session, created = self.service.start(self.brand_id(), "Максим")
        self.assertTrue(created)
        return session

    def first_item(self, session):
        return self.service.list_items(session["id"])[0]

    def test_create_continue_and_prevent_second_active_session(self):
        self.product()
        first = self.start()
        second, created = self.service.start(self.brand_id(), "Другой")
        self.assertFalse(created)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["remaining"], 1)

    def test_empty_brand_starts_and_completes(self):
        self.product(stock=0)
        session = self.start()
        self.assertEqual(session["start_positions"], 0)
        result = self.service.complete(session["id"], confirmation=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["session"]["status"], "completed")

    def test_matching_quantity_is_confirmed_without_movement(self):
        product = self.product()
        session = self.start()
        item = self.first_item(session)
        result = self.service.confirm(
            session["id"], item["id"], 4,
            idempotency_key="same",
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.stock(product["id"]), 4)
        self.assertEqual(self.movement_count(product["id"]), 0)
        detail = self.service.get(session["id"])
        self.assertEqual(detail["remaining"], 0)
        self.assertEqual(result["state"], "confirmed")
        self.assertTrue(result["checked"])
        self.assertEqual(result["action_type"], "inventory_item_confirmed")
        self.assertEqual(detail["checked_positions"], 1)
        repeated = self.service.confirm(
            session["id"], item["id"], 4, idempotency_key="same",
        )
        self.assertEqual(repeated["result"], "already_confirmed")
        self.assertEqual(self.service.get(session["id"])["checked_positions"], 1)
        self.assertEqual(self.movement_count(product["id"]), 0)

    def test_adjust_up_and_down_use_canonical_movements(self):
        products = [
            self.product(name="Часы 5", article="A-5"),
            self.product(name="Часы 3", article="A-3"),
        ]
        session = self.start()
        for product, (actual, delta) in zip(products, ((5, 1), (3, -1))):
            with self.subTest(actual=actual):
                item = next(i for i in self.service.list_items(session["id"])
                            if i["product_id"] == product["id"])
                result = self.service.confirm(
                    session["id"], item["id"], actual,
                    idempotency_key="adjust-{}".format(actual),
                )
                self.assertEqual(result["delta"], delta)
                self.assertEqual(self.stock(product["id"]), actual)
                journal_entry = SalesInventory(self.database).list_movements(product["id"])[0]
                self.assertEqual(journal_entry["label"], "Инвентаризация")
                with self.database.connect() as connection:
                    movement = connection.execute(
                        "SELECT * FROM catalog_stock_movements WHERE id = ?",
                        (result["movement_id"],),
                    ).fetchone()
                    self.assertEqual(movement["source_type"], "inventory")
                    self.assertEqual(movement["source_id"], session["id"])
                    self.assertEqual(movement["source_line_id"], item["id"])

    def test_zero_requires_explicit_confirmation(self):
        self.product()
        session = self.start()
        item = self.first_item(session)
        with self.assertRaises(InventoryError):
            self.service.confirm(session["id"], item["id"], 0, idempotency_key="zero")
        self.service.confirm(
            session["id"], item["id"], 0, idempotency_key="zero-ok", confirm_zero=True
        )

    def test_repeated_request_and_two_workers_create_one_movement(self):
        product = self.product()
        session = self.start()
        item = self.first_item(session)

        def confirm(_):
            return self.service.confirm(
                session["id"], item["id"], 5, idempotency_key="one-request"
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, range(2)))
        self.assertTrue(any(result["repeated"] for result in results))
        self.assertEqual(self.stock(product["id"]), 5)
        self.assertEqual(self.movement_count(product["id"]), 1)

    def test_sale_after_snapshot_creates_conflict_then_recheck(self):
        product = self.product()
        session = self.start()
        item = self.first_item(session)
        self.external_stock_change(product["id"], 3)
        result = self.service.confirm(
            session["id"], item["id"], 4, idempotency_key="stale"
        )
        self.assertTrue(result["conflict"])
        self.assertEqual(self.movement_count(product["id"]), 0)
        refreshed = self.service.refresh_conflict(session["id"], item["id"])
        self.assertEqual(refreshed["system_stock"], 3)
        adjusted = self.service.confirm(
            session["id"], item["id"], 4, idempotency_key="fresh"
        )
        self.assertEqual(adjusted["delta"], 1)

    def test_conflict_refresh_then_matching_confirmation_uses_confirm_state(self):
        product = self.product()
        session = self.start()
        item = self.first_item(session)
        self.external_stock_change(product["id"], 3)
        conflict = self.service.confirm(
            session["id"], item["id"], 3, idempotency_key="stale-match"
        )
        self.assertTrue(conflict["needs_recheck"])
        refreshed = self.service.refresh_conflict(session["id"], item["id"])
        self.assertEqual(refreshed["state"], "unverified")
        self.assertFalse(refreshed["needs_recheck"])
        with self.assertRaisesRegex(InventoryConflict, "Позиция не требует перепроверки"):
            self.service.refresh_conflict(session["id"], item["id"])
        confirmed = self.service.confirm(
            session["id"], item["id"], 3, idempotency_key="fresh-match"
        )
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["delta"], 0)
        self.assertEqual(self.stock(product["id"]), 3)
        self.assertEqual(self.movement_count(product["id"]), 0)

    def test_kpi_uses_total_positions_as_canonical_invariant(self):
        self.product()
        session = self.start()
        self.service.add_new(
            session["id"], "Найденная позиция", "FOUND", 2,
            idempotency_key="found-kpi",
        )
        detail = self.service.get(session["id"])
        self.assertEqual(detail["start_positions"], 1)
        self.assertEqual(detail["added_positions"], 1)
        self.assertEqual(detail["total_positions"], 2)
        self.assertEqual(detail["checked_positions"], 1)
        self.assertEqual(detail["remaining"], 1)
        self.assertEqual(
            detail["checked_positions"] + detail["remaining"],
            detail["total_positions"],
        )

    def test_completion_never_writes_pending_to_zero(self):
        one = self.product(stock=4)
        two = self.product(stock=2, name="Часы Beta", article="B-2")
        session = self.start()
        result = self.service.complete(session["id"], confirmation=True)
        self.assertFalse(result["ok"])
        self.assertTrue(result["conflict"])
        self.assertIn("не списываются автоматически", result["message"])
        self.assertEqual((self.stock(one["id"]), self.stock(two["id"])), (4, 2))
        with self.database.connect() as connection:
            statuses = {row[0] for row in connection.execute(
                "SELECT status FROM erp_inventory_items WHERE session_id = ?", (session["id"],)
            )}
        self.assertEqual(statuses, {"pending"})
        self.assertEqual(self.service.get(session["id"])["status"], "active")

    def test_completion_failure_rolls_back_completed_state(self):
        one = self.product(stock=4)
        two = self.product(stock=2, name="Часы Beta", article="B-2")
        session = self.start()
        for item in self.service.list_items(session["id"]):
            self.service.confirm(
                session["id"], item["id"], item["snapshot_stock"],
                idempotency_key="confirmed-{}".format(item["id"]),
            )
        with self.assertRaises(RuntimeError):
            self.service.complete(
                session["id"], confirmation=True,
                failure_hook=lambda _: (_ for _ in ()).throw(RuntimeError("fail")),
            )
        self.assertEqual((self.stock(one["id"]), self.stock(two["id"])), (4, 2))
        self.assertEqual(self.service.get(session["id"])["status"], "active")

    def test_conflict_is_never_zeroed_on_completion(self):
        product = self.product()
        session = self.start()
        item = self.first_item(session)
        self.external_stock_change(product["id"], 3)
        result = self.service.complete(session["id"], confirmation=True)
        self.assertTrue(result["conflict"])
        self.assertEqual(self.stock(product["id"]), 3)
        self.assertEqual(self.service.get(session["id"])["status"], "active")

    def test_add_existing_zero_stock_and_reactivate_archived(self):
        product = self.product(stock=0)
        original_source_key = product["source_key"]
        self.catalog.delete_product(product["id"])
        session = self.start()
        result = self.service.add_existing(
            session["id"], product["id"], 2, idempotency_key="found"
        )
        self.assertEqual(result["status"], "added")
        self.assertEqual(self.stock(product["id"]), 2)
        with self.database.connect() as connection:
            restored = connection.execute(
                "SELECT active, source_key, deleted_at FROM catalog_excel_products WHERE id = ?",
                (product["id"],),
            ).fetchone()
            self.assertEqual(restored["active"], 1)
            self.assertEqual(restored["source_key"], original_source_key)
            self.assertIsNone(restored["deleted_at"])

    def test_new_product_is_atomic_and_duplicate_is_rejected(self):
        self.product(stock=0)
        session = self.start()
        result = self.service.add_new(
            session["id"], "Найденные часы", "NEW-1", 2, idempotency_key="new"
        )
        self.assertEqual(result["status"], "added")
        with self.assertRaises(InventoryConflict):
            self.service.add_new(
                session["id"], "Найденные часы", "NEW-2", 1, idempotency_key="duplicate"
            )
        with self.assertRaises(RuntimeError):
            self.service.add_new(
                session["id"], "Откат", "ROLLBACK", 1, idempotency_key="rollback",
                failure_hook=lambda _: (_ for _ in ()).throw(RuntimeError("fail")),
            )
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products WHERE excel_name_raw = 'Откат'"
            ).fetchone()[0], 0)

    def test_cancel_keeps_posted_adjustment_and_does_not_zero_pending(self):
        one = self.product(stock=4)
        two = self.product(stock=2, name="Часы Beta", article="B-2")
        session = self.start()
        item = next(i for i in self.service.list_items(session["id"])
                    if i["product_id"] == one["id"])
        self.service.confirm(session["id"], item["id"], 5, idempotency_key="posted")
        cancelled = self.service.cancel(session["id"], "Перенос", "Максим")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual((self.stock(one["id"]), self.stock(two["id"])), (5, 2))

    def test_active_inventory_is_listed_until_cancelled_and_cancel_is_audited(self):
        product = self.product()
        session = self.start()

        active = self.service.list_active()
        self.assertEqual([item["id"] for item in active], [session["id"]])
        self.assertEqual(active[0]["checked_positions"], 0)
        self.assertEqual(active[0]["remaining"], 1)

        cancelled = self.service.cancel(session["id"], "Техническая проверка", "Максим")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.service.list_active(), [])
        self.assertEqual(self.stock(product["id"]), 4)
        self.assertEqual(self.movement_count(product["id"]), 0)
        with self.database.connect() as connection:
            event = connection.execute(
                "SELECT action, actor_display_name_snapshot, metadata_json "
                "FROM erp_audit_events WHERE entity_type='inventory' "
                "AND entity_id=? ORDER BY id DESC LIMIT 1",
                (session["id"],),
            ).fetchone()
        self.assertEqual(event["action"], "cancelled")
        self.assertEqual(event["actor_display_name_snapshot"], "Максим")
        self.assertIn("Техническая проверка", event["metadata_json"])


if __name__ == "__main__":
    unittest.main()


class BrandInventoryWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        database = CatalogDatabase(self.database_path)
        database.initialize()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,"
                "total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','test.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-18T09:00:00+00:00", "2026-08-18T09:00:00+00:00"),
            )
        self.catalog = ExcelProductCatalog(database)
        self.product = self.catalog.create_product(
            name="Часы API", article="API-1", brand="API Brand", category="Часы", stock=4
        )
        with database.connect() as connection:
            self.brand_id = connection.execute(
                "SELECT brand_id FROM catalog_excel_products WHERE id = ?", (self.product["id"],)
            ).fetchone()[0]
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.database_path)}
        )
        self.environment.start()
        self.previous_auth_testing = web.app.config.get("AUTH_TESTING")
        self.previous_auth_database = web.app.config.get("AUTH_DATABASE")
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.update(
            AUTH_TESTING=self.previous_auth_testing,
            AUTH_DATABASE=self.previous_auth_database,
        )
        self.environment.stop()
        self.temp.cleanup()

    def test_api_persists_progress_and_page_has_responsive_inventory_ui(self):
        started = self.client.post("/api/v1/inventories", json={"brand_id": self.brand_id})
        self.assertEqual(started.status_code, 200)
        session = started.get_json()["session"]
        queue = self.client.get(
            "/api/v1/inventories/{}/items?q=API".format(session["id"])
        ).get_json()
        self.assertEqual(len(queue["items"]), 1)
        item = queue["items"][0]
        page = self.client.get(
            "/app/products/inventory?inventory_id={}".format(session["id"])
        )
        markup = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Добавить найденный товар", markup)
        self.assertIn("@media(max-width:650px)", markup)
        self.assertIn("tr.classList.add('removing')", markup)
        self.assertIn("toast(e.message,true)", markup)
        self.assertIn("button.removeAttribute('data-refresh')", markup)
        self.assertIn("item.needs_recheck", markup)
        self.assertIn("Всего позиций", markup)
        self.assertIn("На инвентаризации", markup)
        self.assertIn("progressPercent", markup)

        confirmed = self.client.post(
            "/api/v1/inventories/{}/items/{}/confirm".format(session["id"], item["id"]),
            json={"actual_stock": 5, "idempotency_key": "web-confirm"},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.get_json()["message"], "Остаток изменён с 4 на 5")
        reloaded = self.client.get(
            "/api/v1/inventories/{}/items".format(session["id"])
        ).get_json()
        self.assertEqual(reloaded["items"], [])
        self.assertEqual(reloaded["session"]["checked_positions"], 1)

    def test_unfiltered_inventory_page_lists_active_sessions(self):
        started = self.client.post("/api/v1/inventories", json={"brand_id": self.brand_id})
        self.assertEqual(started.status_code, 200)
        session = started.get_json()["session"]

        page = self.client.get("/app/products/inventory")
        markup = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Активные инвентаризации", markup)
        self.assertIn(session["id"], markup)
        self.assertIn("API Brand", markup)
        self.assertIn("Проверено 0 из 1", markup)
        self.assertIn("inventory_id={}".format(session["id"]), markup)

    def test_api_validation_and_product_brand_ownership(self):
        started = self.client.post("/api/v1/inventories", json={"brand_id": self.brand_id})
        session = started.get_json()["session"]
        item_id = self.client.get(
            "/api/v1/inventories/{}/items".format(session["id"])
        ).get_json()["items"][0]["id"]
        empty = self.client.post(
            "/api/v1/inventories/{}/items/{}/confirm".format(session["id"], item_id),
            json={"actual_stock": "", "idempotency_key": "empty"},
        )
        self.assertEqual(empty.status_code, 400)
        invalid = self.client.post(
            "/api/v1/inventories/{}/items/{}/confirm".format(
                session["id"], item_id
            ),
            json={"actual_stock": -1, "idempotency_key": "invalid"},
        )
        self.assertEqual(invalid.status_code, 400)
        other = self.catalog.create_product(
            name="Другой", article="OTHER", brand="Other", category="Часы", stock=0
        )
        response = self.client.post(
            "/api/v1/inventories/{}/items/existing".format(session["id"]),
            json={"product_id": other["id"], "actual_stock": 1, "idempotency_key": "other"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("другому бренду", response.get_json()["message"])

    def test_authenticated_write_requires_csrf(self):
        auth_path = Path(self.temp.name) / "auth.db"
        web.app.config.update(AUTH_TESTING=True, AUTH_DATABASE=str(auth_path))
        user_id = auth.AuthStore(auth_path).create_initial_admin(
            "Inventory", "Admin", "inventory@example.test", "safe test password"
        )
        client = web.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["_csrf_token"] = "inventory-csrf"
        rejected = client.post("/api/v1/inventories", json={"brand_id": self.brand_id})
        self.assertEqual(rejected.status_code, 403)
        accepted = client.post(
            "/api/v1/inventories", json={"brand_id": self.brand_id},
            headers={"X-CSRF-Token": "inventory-csrf"},
        )
        self.assertEqual(accepted.status_code, 200)
