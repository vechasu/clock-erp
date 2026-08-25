import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from app import auth, web
from app.catalog_db import CatalogDatabase
from app.domain_schema_migrations import apply_domain_migrations
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

    def product(self, stock=4, name="Часы Alpha", article="A-1", active=True,
                brand="Alpha", category="Часы", model=""):
        item = self.catalog.create_product(
            name=name, article=article, brand=brand, category=category,
            model=model, stock=stock
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

    def classification_ids(self, product_id):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT brand_id, category_id, model_id FROM catalog_excel_products "
                "WHERE id = ?", (product_id,),
            ).fetchone()
        return tuple(row)

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

    def make_legacy(self, session):
        """Represent a pre-migration document for compatibility-only workflows."""
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE erp_inventory_sessions SET scope_type = NULL, category_id = NULL, "
                "model_id = NULL, scope_brand_name = NULL, scope_category_name = NULL, "
                "scope_model_name = NULL WHERE id = ?", (session["id"],),
            )
        return self.service.get(session["id"])

    def first_item(self, session):
        return self.service.list_items(session["id"])[0]

    def test_create_continue_and_prevent_second_active_session(self):
        self.product()
        first = self.start()
        second, created = self.service.start(self.brand_id(), "Другой")
        self.assertFalse(created)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["remaining"], 1)

    def test_snapshot_contains_only_strictly_positive_stock(self):
        positive = self.product(stock=3)
        zero = self.product(stock=0, name="Zero", article="ZERO")
        negative = self.product(stock=1, name="Negative", article="NEGATIVE")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = -2 WHERE id = ?",
                (negative["id"],),
            )
        session = self.start()
        self.assertEqual(session["start_positions"], 1)
        items = self.service.list_items(session["id"])
        self.assertEqual([item["product_id"] for item in items], [positive["id"]])
        self.assertNotIn(zero["id"], {item["product_id"] for item in items})
        self.assertNotIn(negative["id"], {item["product_id"] for item in items})

    def test_nonpositive_scope_does_not_create_empty_inventory(self):
        self.product(stock=0)
        negative = self.product(stock=1, name="Negative", article="NEGATIVE")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = -1 WHERE id = ?",
                (negative["id"],),
            )
        with self.assertRaisesRegex(InventoryError, "нет товаров"):
            self.service.start(self.brand_id())
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_inventory_sessions"
            ).fetchone()[0], 0)

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
        session = self.make_legacy(self.start())
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
        self.product(name="Активная позиция", article="ACTIVE-1")
        session = self.make_legacy(self.start())
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
        self.product(name="Активная позиция", article="ACTIVE-1")
        session = self.make_legacy(self.start())
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

    def test_brand_category_and_model_scopes_use_exact_snapshots(self):
        watch_x1 = self.product(name="X steel", article="X-1", model="Model X")
        watch_x2 = self.product(name="X leather", article="X-2", model="Model X")
        zero_x = self.product(
            stock=0, name="X zero", article="X-0", model="Model X"
        )
        negative_x = self.product(
            name="X negative", article="X-NEG", model="Model X"
        )
        watch_y = self.product(name="Y", article="Y-1", model="Model Y")
        strap = self.product(name="Strap", article="S-1", category="Ремешки", model="S")
        other = self.product(
            name="Other X", article="O-1", brand="Other", model="Model X"
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = -1 WHERE id = ?",
                (negative_x["id"],),
            )
        brand_id, category_id, model_id = self.classification_ids(watch_x1["id"])

        brand_session, created = self.service.start(brand_id, idempotency_key="brand")
        self.assertTrue(created)
        self.assertEqual(
            {row["product_id"] for row in self.service.list_items(brand_session["id"])},
            {watch_x1["id"], watch_x2["id"], watch_y["id"], strap["id"]},
        )
        self.assertNotIn(zero_x["id"], {
            row["product_id"] for row in self.service.list_items(brand_session["id"])
        })
        self.assertNotIn(negative_x["id"], {
            row["product_id"] for row in self.service.list_items(brand_session["id"])
        })
        self.assertEqual(brand_session["scope_type"], "brand")
        self.service.cancel(brand_session["id"], "scope test")

        category_session, _ = self.service.start(
            brand_id, category_id=category_id, idempotency_key="category"
        )
        self.assertEqual(
            {row["product_id"] for row in self.service.list_items(category_session["id"])},
            {watch_x1["id"], watch_x2["id"], watch_y["id"]},
        )
        self.assertNotIn(other["id"], {
            row["product_id"] for row in self.service.list_items(category_session["id"])
        })
        self.assertEqual(category_session["scope_type"], "category")
        self.service.cancel(category_session["id"], "scope test")

        model_session, _ = self.service.start(
            brand_id, category_id=category_id, model_id=model_id,
            idempotency_key="model",
        )
        self.assertEqual(
            {row["product_id"] for row in self.service.list_items(model_session["id"])},
            {watch_x1["id"], watch_x2["id"]},
        )
        self.assertEqual(model_session["scope_type"], "model")

    def test_scope_validation_rejects_impossible_hierarchy_and_empty_snapshot(self):
        alpha = self.product(model="Model X")
        beta = self.product(
            name="Beta", article="B-1", brand="Beta", category="Аксессуары",
            model="Model B",
        )
        alpha_brand, alpha_category, alpha_model = self.classification_ids(alpha["id"])
        _, beta_category, beta_model = self.classification_ids(beta["id"])
        with self.assertRaisesRegex(InventoryError, "сначала выберите категорию"):
            self.service.start(alpha_brand, model_id=alpha_model)
        with self.assertRaisesRegex(InventoryError, "не содержит товаров"):
            self.service.start(alpha_brand, category_id=beta_category)
        with self.assertRaisesRegex(InventoryError, "Модель не найдена"):
            self.service.start(
                alpha_brand, category_id=alpha_category, model_id=beta_model
            )

    def test_snapshot_metadata_and_membership_are_immutable(self):
        original = self.product(model="Model X")
        brand_id, category_id, model_id = self.classification_ids(original["id"])
        session, _ = self.service.start(
            brand_id, category_id=category_id, model_id=model_id,
            idempotency_key="immutable",
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET excel_name_raw='Renamed', "
                "excel_brand='Changed', excel_category='Changed', model='Changed', "
                "active=0, stock=0 WHERE id=?", (original["id"],),
            )
        added = self.product(
            name="New matching SKU", article="NEW-SNAPSHOT", model="Model X"
        )
        items = self.service.list_items(session["id"])
        self.assertEqual([item["product_id"] for item in items], [original["id"]])
        self.assertEqual(items[0]["name"], "Часы Alpha")
        self.assertNotEqual(added["id"], original["id"])

    def test_partial_completion_never_changes_outside_snapshot(self):
        inside = self.product(stock=4, model="Model X")
        outside = self.product(
            stock=9, name="Outside", article="OUT-1", model="Model Y"
        )
        brand_id, category_id, model_id = self.classification_ids(inside["id"])
        session, _ = self.service.start(
            brand_id, category_id=category_id, model_id=model_id
        )
        item = self.first_item(session)
        self.service.confirm(
            session["id"], item["id"], 0, confirm_zero=True,
            idempotency_key="explicit-zero",
        )
        completed = self.service.complete(session["id"], confirmation=True)
        self.assertTrue(completed["ok"])
        self.assertEqual(self.stock(inside["id"]), 0)
        self.assertEqual(self.stock(outside["id"]), 9)
        self.assertEqual(self.movement_count(outside["id"]), 0)
        repeated = self.service.complete(session["id"], confirmation=True)
        self.assertTrue(repeated["repeated"])
        self.assertEqual(self.movement_count(inside["id"]), 1)

    def test_overlapping_scopes_conflict_and_disjoint_models_run_together(self):
        x = self.product(model="Model X")
        y = self.product(name="Y", article="Y-1", model="Model Y")
        brand_id, category_id, model_x_id = self.classification_ids(x["id"])
        _, _, model_y_id = self.classification_ids(y["id"])
        whole, _ = self.service.start(brand_id)
        with self.assertRaisesRegex(InventoryConflict, whole["id"]):
            self.service.start(brand_id, category_id=category_id)
        self.service.cancel(whole["id"], "scope test")
        category, _ = self.service.start(brand_id, category_id=category_id)
        with self.assertRaisesRegex(InventoryConflict, category["id"]):
            self.service.start(
                brand_id, category_id=category_id, model_id=model_x_id
            )
        self.service.cancel(category["id"], "scope test")
        model_x, _ = self.service.start(
            brand_id, category_id=category_id, model_id=model_x_id
        )
        model_y, created = self.service.start(
            brand_id, category_id=category_id, model_id=model_y_id
        )
        self.assertTrue(created)
        self.assertNotEqual(model_x["id"], model_y["id"])
        self.assertEqual(len(self.service.list_active()), 2)

    def test_concurrent_scope_create_has_one_business_effect(self):
        product = self.product(model="Model X")
        brand_id, category_id, model_id = self.classification_ids(product["id"])

        def create(index):
            return self.service.start(
                brand_id, category_id=category_id, model_id=model_id,
                idempotency_key="create-{}".format(index),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(len({result[0]["id"] for result in results}), 1)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_inventory_sessions"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_inventory_items"
            ).fetchone()[0], 1)

    def test_concurrent_completion_is_idempotent(self):
        product = self.product()
        session = self.start()
        item = self.first_item(session)
        self.service.confirm(
            session["id"], item["id"], 5, idempotency_key="complete-once"
        )

        def complete(_):
            return self.service.complete(session["id"], confirmation=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(complete, range(2)))
        self.assertTrue(all(result["ok"] for result in results))
        self.assertTrue(any(result.get("repeated") for result in results))
        self.assertEqual(self.stock(product["id"]), 5)
        self.assertEqual(self.movement_count(product["id"]), 1)

    def test_legacy_document_opens_preserves_items_and_completes(self):
        product = self.product()
        session = self.make_legacy(self.start())
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE erp_inventory_items SET snapshot_name=NULL, snapshot_article=NULL, "
                "snapshot_brand_id=NULL, snapshot_category_id=NULL, snapshot_model_id=NULL, "
                "snapshot_brand_name=NULL, snapshot_category_name=NULL, "
                "snapshot_model_name=NULL WHERE session_id=?", (session["id"],),
            )
        detail = self.service.get(session["id"])
        self.assertTrue(detail["legacy_scope"])
        self.assertEqual(detail["scope_label"], "Alpha · весь бренд")
        item = self.first_item(detail)
        self.assertEqual(item["name"], "Часы Alpha")
        self.service.confirm(
            detail["id"], item["id"], 4, idempotency_key="legacy-confirm"
        )
        self.assertTrue(self.service.complete(detail["id"], confirmation=True)["ok"])
        self.assertEqual(self.stock(product["id"]), 4)


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
        self.assertNotIn("id=\"addFound\"", markup)
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
        self.assertIn('id="startForm" data-shared-catalog-scope', markup)

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
        self.assertIn("вне зафиксированного snapshot", response.get_json()["message"])

    def test_authenticated_write_requires_csrf(self):
        auth_path = Path(self.temp.name) / "auth.db"
        web.app.config.update(AUTH_TESTING=True, AUTH_DATABASE=str(auth_path))
        apply_domain_migrations(auth_path, "auth", "test")
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
