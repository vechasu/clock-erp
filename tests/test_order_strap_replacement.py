import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.schema_migrations import apply_migrations
from app.services.audit_journal import AuditJournal
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import (
    CancellationConflictError,
    PotentialStrapDuplicateError,
    SalesInventory,
    SalesInventoryError,
)


class OrderStrapReplacementTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "catalog.db"
        apply_migrations(self.path)
        self.database = CatalogDatabase(self.path)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,"
                "row_count,total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('strap-batch','strap-sha','strap.xlsx',0,0,0,0,'active',"
                "'2026-08-31T00:00:00+00:00','2026-08-31T00:00:00+00:00')"
            )
        catalog = ExcelProductCatalog(self.database)
        self.ordered = catalog.create_product(
            "Bradley Black", article="BRADLEY-BLACK", brand="Bradley",
            category="Часы", stock=0,
        )
        self.base = catalog.create_product(
            "Bradley Blue", article="BRADLEY-BLUE", brand="Bradley",
            category="Часы", stock=1,
        )
        self.removed = catalog.create_product(
            "Синий ремешок Bradley", article="STRAP-BLUE", brand="Bradley",
            category="Ремешки", stock=0,
        )
        self.installed = catalog.create_product(
            "Чёрный ремешок Bradley", article="STRAP-BLACK", brand="Bradley",
            category="Ремешки", stock=1,
        )
        self.catalog = catalog
        self.inventory = SalesInventory(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def item(self):
        return {
            "product_id": self.ordered["id"], "quantity": 1,
            "original_unit_price": 17900, "unit_price": 17900,
            "discount_type": "none", "discount_value": 0,
            "discount_reason": "",
        }

    def replacement(self, mode="existing", **overrides):
        value = {
            "operation_id": "strap-operation-21128",
            "line_index": 0,
            "base_product_id": self.base["id"],
            "removed_strap_mode": mode,
            "removed_strap_product_id": self.removed["id"],
            "installed_strap_product_id": self.installed["id"],
            "comment": "Замена по фактической комплектации",
        }
        value.update(overrides)
        return value

    def conduct(self, replacement=None, key="bitrix-order:21128", hook=None):
        return self.inventory.create_order_strap_replacement_sale(
            {
                "source": "tictactoy", "order_id": "21128",
                "external_order_id": "21128", "order_number": "21128",
                "recipient_name": "Клиент", "track_number": "TRACK",
            },
            [self.item()], replacement or self.replacement(),
            user_name="Максим", idempotency_key=key,
            enforce_external_unique=True, failure_hook=hook,
            audit_actor={"actor_id": "7", "actor_name": "Максим"},
        )

    def stocks(self):
        with self.database.connect() as connection:
            return {
                int(row["id"]): float(row["stock"])
                for row in connection.execute(
                    "SELECT id,stock FROM catalog_excel_products"
                ).fetchall()
            }

    def test_existing_removed_strap_moves_all_real_stock_and_sells_ordered_sku(self):
        sale = self.conduct()
        stock = self.stocks()
        self.assertEqual(stock[self.ordered["id"]], 0)
        self.assertEqual(stock[self.base["id"]], 0)
        self.assertEqual(stock[self.removed["id"]], 1)
        self.assertEqual(stock[self.installed["id"]], 0)
        self.assertEqual(sale["product_id"], str(self.ordered["id"]))
        with self.database.connect() as connection:
            operation = connection.execute(
                "SELECT * FROM erp_order_strap_operations"
            ).fetchone()
        self.assertEqual(operation["event_type"], "order_strap_replacement_sale")
        self.assertEqual(operation["base_product_id"], self.base["id"])

    def test_without_removed_strap_creates_no_positive_movement(self):
        sale = self.conduct(self.replacement("none", removed_strap_product_id=None))
        movements = self.inventory.list_movements(self.removed["id"])
        self.assertEqual(movements, [])
        with self.database.connect() as connection:
            positive = connection.execute(
                "SELECT COUNT(*) FROM catalog_stock_movements "
                "WHERE sale_id=? AND quantity_delta>0", (sale["id"],)
            ).fetchone()[0]
        self.assertEqual(positive, 0)

    def test_new_removed_strap_is_created_inside_operation_and_received(self):
        sale = self.conduct(self.replacement(
            "created", removed_strap_product_id=None,
            new_removed_strap={
                "brand": "Bradley", "name": "Ocean Blue",
                "model": "Ocean Blue", "color": "синий", "article": "",
                "condition": "new", "comment": "Снят с Bradley Blue",
            },
        ))
        with self.database.connect() as connection:
            operation = connection.execute(
                "SELECT * FROM erp_order_strap_operations WHERE sale_id=?",
                (sale["id"],),
            ).fetchone()
            product = connection.execute(
                "SELECT p.stock,c.name AS category FROM catalog_excel_products p "
                "JOIN erp_categories c ON c.id=p.category_id WHERE p.id=?",
                (operation["removed_strap_product_id"],),
            ).fetchone()
        self.assertEqual(operation["created_removed_strap"], 1)
        self.assertEqual(product["stock"], 1)
        self.assertEqual(product["category"], "Ремешки")

    def test_normalized_duplicate_is_reported_before_any_write(self):
        before = self.stocks()
        with self.assertRaises(PotentialStrapDuplicateError) as caught:
            self.conduct(self.replacement(
                "created", removed_strap_product_id=None,
                new_removed_strap={
                    "brand": "  BRADLEY ", "name": "синий   ремешок bradley",
                    "model": "", "article": "", "condition": "new",
                },
            ))
        self.assertEqual(caught.exception.matches[0]["id"], self.removed["id"])
        self.assertEqual(self.stocks(), before)
        self.assertEqual(self.inventory.list_sales(), [])

    def test_duplicate_can_be_resolved_by_using_existing_card(self):
        self.conduct(self.replacement("existing"))
        self.assertEqual(self.stocks()[self.removed["id"]], 1)
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products"
            ).fetchone()[0]
        self.assertEqual(count, 4)

    def test_explicit_duplicate_confirmation_creates_distinct_card(self):
        self.conduct(self.replacement(
            "created", removed_strap_product_id=None, confirm_duplicate=True,
            new_removed_strap={
                "brand": "Bradley", "name": "Синий ремешок Bradley",
                "model": "Special", "article": "", "condition": "display",
            },
        ))
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products"
            ).fetchone()[0]
        self.assertEqual(count, 5)

    def test_zero_installed_strap_is_rejected_with_exact_message(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock=0 WHERE id=?",
                (self.installed["id"],),
            )
        with self.assertRaisesRegex(
            SalesInventoryError,
            "Выбранный ремешок закончился: требуется 1, доступно 0",
        ):
            self.conduct()

    def test_zero_base_watch_is_rejected_with_exact_message(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock=0 WHERE id=?",
                (self.base["id"],),
            )
        with self.assertRaisesRegex(
            SalesInventoryError,
            "Часы-основа закончились: требуется 1, доступно 0",
        ):
            self.conduct()

    def test_stock_change_after_form_open_is_rechecked_in_transaction(self):
        selected_when_opened = self.catalog.get_product(self.installed["id"])
        self.assertEqual(selected_when_opened["stock"], 1)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock=0 WHERE id=?",
                (self.installed["id"],),
            )
        with self.assertRaises(SalesInventoryError):
            self.conduct()
        self.assertEqual(self.inventory.list_sales(), [])

    def test_failure_hook_rolls_back_sale_movements_operation_and_new_product(self):
        before = self.stocks()
        replacement = self.replacement(
            "created", removed_strap_product_id=None,
            new_removed_strap={
                "brand": "Bradley", "name": "Rollback Blue", "model": "",
                "article": "ROLLBACK-BLUE", "condition": "used",
            },
        )
        with self.assertRaisesRegex(RuntimeError, "forced rollback"):
            self.conduct(
                replacement,
                hook=lambda connection: (_ for _ in ()).throw(
                    RuntimeError("forced rollback")
                ),
            )
        self.assertEqual(self.stocks(), before)
        self.assertEqual(self.inventory.list_sales(), [])
        with self.database.connect() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM erp_order_strap_operations"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products "
                "WHERE excel_article='ROLLBACK-BLUE'"
            ).fetchone()[0], 0)

    def test_repeated_idempotency_key_returns_same_sale_without_new_movements(self):
        first = self.conduct()
        movement_count = sum(
            len(self.inventory.list_movements(product_id))
            for product_id in (
                self.base["id"], self.removed["id"], self.installed["id"]
            )
        )
        second = self.conduct()
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.inventory.list_sales()), 1)
        self.assertEqual(sum(
            len(self.inventory.list_movements(product_id))
            for product_id in (
                self.base["id"], self.removed["id"], self.installed["id"]
            )
        ), movement_count)

    def test_two_workers_cannot_double_conduct_order(self):
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            service = SalesInventory(CatalogDatabase(self.path))
            return service.create_order_strap_replacement_sale(
                {"source": "tictactoy", "order_id": "21128"},
                [self.item()], self.replacement(),
                idempotency_key="bitrix-order:21128",
                enforce_external_unique=True,
            )["id"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            sale_ids = list(executor.map(lambda _value: worker(), range(2)))
        self.assertEqual(len(set(sale_ids)), 1)
        self.assertEqual(len(self.inventory.list_sales()), 1)

    def test_ordinary_sale_still_uses_original_product(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock=1 WHERE id=?",
                (self.ordered["id"],),
            )
        sale = self.inventory.create_sale_batch(
            {"source": "tictactoy", "order_id": "ordinary"}, [self.item()],
            idempotency_key="ordinary", enforce_external_unique=True,
        )
        self.assertEqual(sale["product_id"], str(self.ordered["id"]))
        self.assertEqual(self.stocks()[self.ordered["id"]], 0)

    def test_order_sale_and_product_histories_have_structured_event(self):
        sale = self.conduct()
        journal = AuditJournal(self.database)
        order_events = journal.list_events(
            entity_type="order", entity_id="21128", limit=50
        )["events"]
        product_events = journal.list_events(
            entity_type="product", entity_id=str(self.base["id"]), limit=50
        )["events"]
        sale_events = journal.list_events(
            entity_type="sale", entity_id=sale["id"], limit=50
        )["events"]
        for events in (order_events, product_events, sale_events):
            self.assertTrue(any(
                event["metadata"].get("event_type")
                == "order_strap_replacement_sale" for event in events
            ))

    def test_cancellation_mirrors_every_component_movement(self):
        sale = self.conduct()
        self.inventory.cancel_sale(sale["id"], reason="Ошибка комплектации")
        stock = self.stocks()
        self.assertEqual(stock[self.base["id"]], 1)
        self.assertEqual(stock[self.removed["id"]], 0)
        self.assertEqual(stock[self.installed["id"]], 1)
        with self.database.connect() as connection:
            operation = connection.execute(
                "SELECT event_type,status FROM erp_order_strap_operations"
            ).fetchone()
        self.assertEqual(tuple(operation), (
            "order_strap_replacement_cancelled", "cancelled"
        ))

    def test_cancellation_is_blocked_when_removed_strap_was_consumed(self):
        sale = self.conduct()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock=0 WHERE id=?",
                (self.removed["id"],),
            )
        before = self.stocks()
        with self.assertRaisesRegex(
            CancellationConflictError, "Снятый ремешок уже использован"
        ):
            self.inventory.cancel_sale(sale["id"], reason="Отмена")
        self.assertEqual(self.stocks(), before)
        self.assertFalse(self.inventory.get_sale(sale["id"])["cancelled_at"])

    def test_ui_contains_both_picker_modes_preview_and_zero_stock_guard(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/orders.html").read_text(encoding="utf-8")
        self.assertIn("Заменить ремешок", template)
        self.assertIn("Часы-основа", template)
        self.assertIn("По каталогу", template)
        self.assertIn("После проведения", template)
        self.assertIn("Number(item.stock||0)<=0", template)

    def test_migration_preserves_existing_business_rows(self):
        before = self.stocks()
        result = apply_migrations(self.path)
        self.assertEqual(result["business"]["products"], 4)
        self.assertEqual(self.stocks(), before)
        with self.database.connect() as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='erp_order_strap_operations'"
            ).fetchone())


if __name__ == "__main__":
    unittest.main()
