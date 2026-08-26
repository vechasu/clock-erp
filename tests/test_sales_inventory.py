import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.catalog_migration_steps import apply_fresh_catalog_schema
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import (
    CancellationConflictError,
    InsufficientStockError,
    ReturnConflictError,
    SalesInventory,
    SalesInventoryError,
    positive_integer,
)
from app.services.audit_journal import AuditJournal


class SalesInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_directory.name)
        self.database_path = self.temp_path / "catalog.db"
        self.database = CatalogDatabase(self.database_path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    "batch-test",
                    "sha-test",
                    "test.xlsx",
                    0,
                    0,
                    0,
                    0,
                    "2026-07-29T09:00:00+00:00",
                    "2026-07-29T09:00:00+00:00",
                ),
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.inventory = SalesInventory(self.database)

    def tearDown(self):
        self.temp_directory.cleanup()

    def create_product(self, stock=3, name="Часы Test", article="ARTICLE-1"):
        return self.catalog.create_product(
            name=name,
            article=article,
            brand="Brand",
            category="Коллекция",
            stock=stock,
        )

    @staticmethod
    def payload(product, sale_id="sale-1"):
        return {
            "id": sale_id,
            "created_at": "2026-07-29",
            "source": "Tictactoy",
            "product_id": str(product["id"]),
            "product_name": product["display_name"],
            "brand": product["display_brand"],
            "category": product["display_category"],
            "order_number": "125",
            "note": "",
        }

    def stock(self, product_id):
        return self.catalog.get_product(product_id)["stock"]

    def sale_effects(self, product_id):
        with self.database.connect() as connection:
            return {
                "sales": connection.execute(
                    "SELECT COUNT(*) FROM erp_sales"
                ).fetchone()[0],
                "items": connection.execute(
                    "SELECT COUNT(*) FROM erp_sale_items"
                ).fetchone()[0],
                "movements": connection.execute(
                    "SELECT COUNT(*) FROM catalog_stock_movements"
                ).fetchone()[0],
                "idempotency": connection.execute(
                    "SELECT COUNT(*) FROM erp_sales "
                    "WHERE idempotency_key IS NOT NULL"
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM erp_audit_events "
                    "WHERE entity_type = 'sale'"
                ).fetchone()[0],
                "stock": connection.execute(
                    "SELECT stock FROM catalog_excel_products WHERE id = ?",
                    (product_id,),
                ).fetchone()[0],
                "in_transaction": connection.in_transaction,
            }

    def test_positive_integer_quantity_contract(self):
        for value in (1, "1", "1e0", Decimal("1"), 1.0):
            with self.subTest(value=value):
                self.assertEqual(positive_integer(value, "Количество"), 1)
        for value in (0, -1, 0.5, "0.5", True, False, "nan", "inf"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    SalesInventoryError,
                    "положительным целым числом",
                ):
                    positive_integer(value, "Количество")

    def test_fractional_sale_is_rejected_without_side_effects(self):
        product = self.create_product(stock=1)
        before = self.sale_effects(product["id"])

        for quantity in (0.5, "0.5"):
            with self.subTest(quantity=quantity):
                with self.assertRaisesRegex(
                    SalesInventoryError,
                    "положительным целым числом",
                ):
                    self.inventory.create_sale(
                        self.payload(product),
                        product["id"],
                        quantity,
                        1000,
                        idempotency_key="qa001-direct",
                    )
                self.assertEqual(
                    self.sale_effects(product["id"]),
                    before,
                )

    def test_failed_fractional_sale_does_not_consume_idempotency_key(self):
        product = self.create_product(stock=1)
        with self.assertRaises(SalesInventoryError):
            self.inventory.create_sale(
                self.payload(product, "sale-invalid"),
                product["id"],
                "0.5",
                1000,
                idempotency_key="qa001-retry",
            )

        created = self.inventory.create_sale(
            self.payload(product, "sale-valid"),
            product["id"],
            "1",
            1000,
            idempotency_key="qa001-retry",
        )

        self.assertEqual(created["id"], "sale-valid")
        self.assertEqual(self.stock(product["id"]), 0)
        self.assertEqual(len(self.inventory.list_movements(product["id"])), 1)

    def test_batch_fraction_is_rejected_before_any_item_mutation(self):
        watch = self.create_product(stock=2, article="QA001-WATCH")
        strap = self.create_product(stock=2, article="QA001-STRAP")
        before_watch = self.sale_effects(watch["id"])
        before_strap = self.stock(strap["id"])

        with self.assertRaisesRegex(
            SalesInventoryError,
            "положительным целым числом",
        ):
            self.inventory.create_sale_batch(
                {"source": "tictactoy", "external_order_id": "qa001-batch"},
                [
                    {"product_id": watch["id"], "quantity": 1, "unit_price": 1},
                    {"product_id": strap["id"], "quantity": 0.5, "unit_price": 1},
                ],
                idempotency_key="qa001-batch",
                enforce_external_unique=True,
            )

        self.assertEqual(self.sale_effects(watch["id"]), before_watch)
        self.assertEqual(self.stock(strap["id"]), before_strap)

    def test_concurrent_fractional_and_integer_sales_keep_whole_stock(self):
        product = self.create_product(stock=1)

        def sell(request):
            sale_id, quantity = request
            service = SalesInventory(CatalogDatabase(self.database_path))
            try:
                service.create_sale(
                    self.payload(product, sale_id),
                    product["id"],
                    quantity,
                    1000,
                    idempotency_key=sale_id,
                )
                return "created"
            except SalesInventoryError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                sell,
                (("qa001-fraction", 0.5), ("qa001-integer", 1)),
            ))

        self.assertCountEqual(results, ["rejected", "created"])
        self.assertEqual(self.stock(product["id"]), 0)
        self.assertEqual(len(self.inventory.list_sales()), 1)
        self.assertEqual(len(self.inventory.list_movements(product["id"])), 1)

    def test_sale_decreases_stock_and_writes_movement_atomically(self):
        product = self.create_product(stock=3)
        sale = self.inventory.create_sale(
            self.payload(product),
            product["id"],
            1,
            1000,
            user_name="Тест",
        )

        self.assertEqual(self.stock(product["id"]), 2)
        self.assertEqual(sale["product_id"], str(product["id"]))
        self.assertEqual(sale["status"], "completed")
        movement = self.inventory.list_movements(product["id"])[0]
        self.assertEqual(movement["type"], "sale")
        self.assertEqual(movement["diff"], -1)
        self.assertEqual(movement["stock_after"], 2)
        self.assertEqual(movement["sale_id"], "sale-1")

    def test_performed_time_precedes_source_created_time(self):
        product = self.create_product(stock=2)
        payload = self.payload(product)
        payload.update({
            "created_at": "2026-08-18 00:11:36",
            "performed_at": "2026-08-18T15:05:00+03:00",
        })

        sale = self.inventory.create_sale(
            payload, product["id"], 1, 1000
        )

        self.assertEqual(sale["created_at"], payload["performed_at"])
        self.assertEqual(sale["performed_at"], payload["performed_at"])

    def test_sale_without_explicit_date_uses_moscow_business_time(self):
        product = self.create_product(stock=2)
        payload = self.payload(product)
        payload.pop("created_at")
        expected = "2026-08-18T15:05:00+03:00"

        with mock.patch(
            "app.services.sales_inventory.sale_now_iso",
            return_value=expected,
        ):
            sale = self.inventory.create_sale(
                payload, product["id"], 1, 1000
            )

        self.assertEqual(sale["created_at"], expected)

    def test_archive_and_restore_do_not_change_stock_or_movements(self):
        product = self.create_product(stock=3)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
            user_name="Автор",
        )
        movement_count = len(self.inventory.list_movements(product["id"]))

        archived = self.inventory.set_archived(
            sale["id"], True, user_name="Архиватор",
        )
        repeated = self.inventory.set_archived(
            sale["id"], True, user_name="Архиватор",
        )

        self.assertTrue(archived["archived_at"])
        self.assertEqual(repeated["archived_at"], archived["archived_at"])
        self.assertEqual(self.stock(product["id"]), 2)
        self.assertEqual(
            len(self.inventory.list_movements(product["id"])), movement_count,
        )
        restored = self.inventory.set_archived(
            sale["id"], False, user_name="Архиватор",
        )
        self.assertFalse(restored["archived_at"])
        self.assertEqual(self.stock(product["id"]), 2)
        events = AuditJournal(self.database).list_events(
            entity_type="sale", entity_id=sale["id"], limit=100,
        )["events"]
        statuses = [event["status_snapshot"] for event in events]
        self.assertIn("archived", statuses)
        self.assertIn("active", statuses)

    def test_return_status_cannot_be_used_to_create_sale(self):
        product = self.create_product(stock=3)
        payload = self.payload(product)
        payload["order_status"] = "returned"
        with self.assertRaisesRegex(SalesInventoryError, "существующей продажи"):
            self.inventory.create_sale(payload, product["id"], 1, 1000)
        self.assertEqual(self.stock(product["id"]), 3)

    def test_unknown_and_zero_prices_remain_distinct(self):
        product = self.create_product(stock=3)
        unknown = self.inventory.create_sale(
            self.payload(product, "sale-null"), product["id"], 1, None
        )
        zero = self.inventory.create_sale(
            self.payload(product, "sale-zero"), product["id"], 1, 0
        )
        self.assertIsNone(unknown["unit_price"])
        self.assertEqual(zero["unit_price"], 0)
        self.assertEqual(self.stock(product["id"]), 1)

    def test_multi_quantity_and_insufficient_stock(self):
        product = self.create_product(stock=3)
        self.inventory.create_sale(
            self.payload(product),
            product["id"],
            2,
            1000,
        )
        self.assertEqual(self.stock(product["id"]), 1)

        with self.assertRaises(InsufficientStockError):
            self.inventory.create_sale(
                self.payload(product, "sale-2"),
                product["id"],
                2,
                1000,
            )

        self.assertEqual(self.stock(product["id"]), 1)
        self.assertIsNone(self.inventory.get_sale("sale-2"))

    def test_failure_rolls_back_sale_stock_and_history(self):
        product = self.create_product(stock=3)

        def fail(_connection):
            raise RuntimeError("forced rollback")

        with self.assertRaises(RuntimeError):
            self.inventory.create_sale(
                self.payload(product),
                product["id"],
                1,
                1000,
                failure_hook=fail,
            )

        self.assertEqual(self.stock(product["id"]), 3)
        self.assertIsNone(self.inventory.get_sale("sale-1"))
        self.assertEqual(
            self.inventory.list_movements(product["id"]),
            [],
        )

    def test_batch_sale_is_one_sale_with_multiple_items_and_movements(self):
        watch = self.create_product(stock=5, name="Bradley Steel", article="B-1")
        strap = self.create_product(stock=3, name="Ремешок", article="S-1")
        sale = self.inventory.create_sale_batch(
            {
                "source": "tictactoy", "order_number": "18593",
                "external_order_id": "18593", "sale_type": "automatic",
            },
            [
                {"product_id": watch["id"], "quantity": 2, "unit_price": 7500,
                 "product_name": "Bradley Steel"},
                {"product_id": strap["id"], "quantity": 1, "unit_price": 2400,
                 "product_name": "Ремешок"},
            ],
            idempotency_key="bitrix-order:18593",
            enforce_external_unique=True,
        )

        rows = self.inventory.list_sales()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["id"] for row in rows}, {sale["id"]})
        self.assertEqual(self.stock(watch["id"]), 3)
        self.assertEqual(self.stock(strap["id"]), 2)
        self.assertEqual(len(self.inventory.list_movements(watch["id"])), 1)
        self.assertEqual(len(self.inventory.list_movements(strap["id"])), 1)

    def test_batch_failure_rolls_back_every_item(self):
        watch = self.create_product(stock=5, name="Bradley Steel", article="B-2")
        strap = self.create_product(stock=3, name="Ремешок", article="S-2")

        with self.assertRaisesRegex(RuntimeError, "forced batch rollback"):
            self.inventory.create_sale_batch(
                {"source": "tictactoy", "external_order_id": "18594"},
                [
                    {"product_id": watch["id"], "quantity": 2, "unit_price": 1},
                    {"product_id": strap["id"], "quantity": 1, "unit_price": 1},
                ],
                idempotency_key="bitrix-order:18594",
                enforce_external_unique=True,
                failure_hook=lambda _connection: (_ for _ in ()).throw(
                    RuntimeError("forced batch rollback")
                ),
            )

        self.assertEqual(self.stock(watch["id"]), 5)
        self.assertEqual(self.stock(strap["id"]), 3)
        self.assertEqual(self.inventory.list_sales(), [])

    def test_concurrent_batch_retry_changes_stock_once(self):
        product = self.create_product(stock=5, name="Bradley Steel", article="B-3")

        def create(_index):
            return self.inventory.create_sale_batch(
                {"source": "tictactoy", "external_order_id": "18595"},
                [{"product_id": product["id"], "quantity": 2, "unit_price": 1}],
                idempotency_key="bitrix-order:18595",
                enforce_external_unique=True,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            sales = list(executor.map(create, range(2)))

        self.assertEqual(sales[0]["id"], sales[1]["id"])
        self.assertEqual(self.stock(product["id"]), 3)
        self.assertEqual(len({row["id"] for row in self.inventory.list_sales()}), 1)

    def test_metadata_edit_does_not_change_stock_or_movement_history(self):
        product = self.create_product(stock=3)
        sale = self.inventory.create_sale(
            self.payload(product),
            product["id"],
            1,
            1000,
        )
        movements_before = self.inventory.list_movements(product["id"])
        payload = dict(sale)
        payload["note"] = "Обновлённое примечание"

        updated = self.inventory.update_metadata(
            "sale-1",
            payload,
            1000,
        )

        self.assertEqual(self.stock(product["id"]), 2)
        self.assertEqual(
            self.inventory.list_movements(product["id"]),
            movements_before,
        )
        self.assertEqual(updated["note"], "Обновлённое примечание")

    def test_sale_keeps_historical_product_snapshot_after_catalog_edit(self):
        product = self.create_product(stock=3)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
        )

        self.catalog.update_product(
            product["id"],
            name="Новое название карточки",
            brand="Новый бренд",
            category="Новая категория",
        )

        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["product_id"], str(product["id"]))
        self.assertEqual(stored["product_name"], sale["product_name"])
        self.assertEqual(stored["brand"], sale["brand"])
        self.assertEqual(stored["category"], sale["category"])

    def test_protected_change_rejects_entire_update_atomically(self):
        product = self.create_product(stock=3)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
        )
        movements_before = self.inventory.list_movements(product["id"])

        with self.assertRaisesRegex(SalesInventoryError, "Количество"):
            self.inventory.update_sale(
                sale["id"],
                {**sale, "quantity": 2, "note": "не сохранять"},
                2,
                1000,
            )

        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored.get("note") or "", "")
        self.assertEqual(stored["quantity"], 1)
        self.assertEqual(self.stock(product["id"]), 2)
        self.assertEqual(
            self.inventory.list_movements(product["id"]), movements_before
        )

    def test_exact_protected_field_list_is_enforced(self):
        product = self.create_product(stock=3)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
        )
        cases = (
            ("created_at", "2026-08-05T14:14", 1, 1000, "Дату"),
            ("product_id", "999", 1, 1000, "Товар"),
            ("product_name", "Другое название", 1, 1000, "Название товара"),
            ("brand", "Другой бренд", 1, 1000, "Бренд"),
            ("category", "Другая категория", 1, 1000, "Категорию"),
            ("quantity", 2, 2, 1000, "Количество"),
        )
        for field, value, quantity, price, message in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(SalesInventoryError, message):
                    self.inventory.update_sale(
                        sale["id"],
                        {**sale, field: value, "note": "не сохранять"},
                        quantity,
                        price,
                    )
                stored = self.inventory.get_sale(sale["id"])
                self.assertEqual(stored.get("note") or "", "")
                self.assertEqual(stored["quantity"], 1)
                self.assertEqual(stored["unit_price"], 1000)

    def test_update_quantity_uses_stock_delta_and_rolls_back(self):
        product = self.create_product(stock=5)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
        )
        payload = {**sale, "quantity": 3}

        with self.assertRaisesRegex(SalesInventoryError, "Количество"):
            self.inventory.update_sale(
                "sale-1", payload, 3, 1000, idempotency_key="update-1",
            )
        self.assertEqual(self.stock(product["id"]), 4)
        self.assertEqual(self.inventory.get_sale("sale-1")["quantity"], 1)

    def test_update_quantity_applies_only_delta_and_repeated_values_are_noop(self):
        product = self.create_product(stock=5)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
        )

        with self.assertRaisesRegex(SalesInventoryError, "Количество"):
            self.inventory.update_sale(
                "sale-1", {**sale, "quantity": 2}, 2, 1000,
            )
        repeated = self.inventory.update_sale(
            "sale-1", {**sale, "quantity": 1, "note": "ok"}, 1, 1000,
        )
        movements = self.inventory.list_movements(product["id"])
        self.assertEqual(repeated["quantity"], 1)
        self.assertEqual(self.stock(product["id"]), 4)
        self.assertEqual(repeated["note"], "ok")
        self.assertEqual(len(movements), 1)

    def test_update_quantity_three_to_one_and_three_to_five_uses_only_delta(self):
        product = self.create_product(stock=10)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 3, 1000,
        )
        self.assertEqual(self.stock(product["id"]), 7)

        for quantity in (1, 5):
            with self.assertRaisesRegex(SalesInventoryError, "Количество"):
                self.inventory.update_sale(
                    "sale-1", {**sale, "quantity": quantity}, quantity, 1000,
                )
        self.assertEqual(self.stock(product["id"]), 7)
        self.assertEqual(len(self.inventory.list_movements(product["id"])), 1)

    def test_replace_with_insufficient_stock_rolls_back_both_products(self):
        old_product = self.create_product(stock=4)
        new_product = self.create_product(
            stock=1, name="Часы Limited", article="ARTICLE-2",
        )
        sale = self.inventory.create_sale(
            self.payload(old_product), old_product["id"], 2, 1000,
        )
        old_movements = self.inventory.list_movements(old_product["id"])

        with self.assertRaisesRegex(SalesInventoryError, "Товар"):
            self.inventory.update_sale(
                "sale-1",
                {**sale, "product_id": str(new_product["id"])},
                2,
                1000,
            )

        stored = self.inventory.get_sale("sale-1")
        self.assertEqual(stored["product_id"], str(old_product["id"]))
        self.assertEqual(stored["quantity"], 2)
        self.assertEqual(self.stock(old_product["id"]), 2)
        self.assertEqual(self.stock(new_product["id"]), 1)
        self.assertEqual(
            self.inventory.list_movements(old_product["id"]),
            old_movements,
        )
        self.assertEqual(
            self.inventory.list_movements(new_product["id"]),
            [],
        )

    def test_update_replaces_product_and_status_changes_stock(self):
        old_product = self.create_product(stock=4)
        new_product = self.create_product(
            stock=5, name="Часы New", article="ARTICLE-2",
        )
        sale = self.inventory.create_sale(
            self.payload(old_product), old_product["id"], 2, 1000,
        )
        replacement = {
            **sale,
            "product_id": str(new_product["id"]),
            "order_status": "completed",
        }

        with self.assertRaisesRegex(SalesInventoryError, "Товар"):
            self.inventory.update_sale(
                "sale-1", replacement, 3, 1000,
                idempotency_key="replace-1",
            )
        stored = self.inventory.get_sale("sale-1")
        self.assertEqual(stored["product_id"], str(old_product["id"]))
        self.assertEqual(stored["quantity"], 2)
        self.assertEqual(self.stock(old_product["id"]), 2)
        self.assertEqual(self.stock(new_product["id"]), 5)

    def test_cancel_restores_stock_then_soft_delete_does_not_change_it(self):
        product = self.create_product(stock=3)
        self.inventory.create_sale(
            self.payload(product), product["id"], 2, 1000,
        )

        cancelled = self.inventory.cancel_sale(
            "sale-1", reason="Ошибка ввода", idempotency_key="cancel:sale-1",
        )
        self.assertEqual(self.stock(product["id"]), 3)
        first = self.inventory.delete_sale("sale-1")
        second = self.inventory.delete_sale(
            "sale-1", idempotency_key="sale-delete:sale-1",
        )

        self.assertTrue(first["deleted_at"])
        self.assertEqual(second["deleted_at"], first["deleted_at"])
        self.assertEqual(self.stock(product["id"]), 3)
        self.assertEqual(self.inventory.list_sales(), [])
        cancellations = [
            movement for movement in self.inventory.list_movements(product["id"])
            if movement["type"] == "cancellation"
        ]
        self.assertEqual(len(cancellations), 1)

    def test_partial_and_full_return_restore_stock_once(self):
        product = self.create_product(stock=4)
        self.inventory.create_sale(
            self.payload(product),
            product["id"],
            3,
            1000,
        )

        partial = self.inventory.return_sale(
            "sale-1",
            1,
            reason="Не подошло",
        )
        self.assertEqual(self.stock(product["id"]), 2)
        self.assertEqual(partial["status"], "partially_returned")
        self.assertEqual(partial["returned_quantity"], 1)

        returned = self.inventory.return_sale("sale-1", 2)
        self.assertEqual(self.stock(product["id"]), 4)
        self.assertEqual(returned["status"], "returned")
        self.assertEqual(returned["returned_quantity"], 3)
        movements = self.inventory.list_movements(product["id"])
        self.assertEqual([item["type"] for item in movements[:2]], [
            "return",
            "return",
        ])

        with self.assertRaises(ReturnConflictError):
            self.inventory.return_sale("sale-1", 1)
        self.assertEqual(self.stock(product["id"]), 4)

    def test_return_requires_proven_sale_movement(self):
        product = self.create_product(stock=2)
        self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000
        )
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM catalog_stock_movements WHERE sale_id = ?",
                ("sale-1",),
            )

        with self.assertRaisesRegex(ReturnConflictError, "исходное списание"):
            self.inventory.return_sale("sale-1", 1)
        self.assertEqual(self.stock(product["id"]), 1)
        self.assertEqual(self.inventory.get_sale("sale-1")["status"], "completed")

    def test_return_rolls_back_stock_and_state_on_failure(self):
        product = self.create_product(stock=2)
        self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000
        )
        with self.assertRaisesRegex(RuntimeError, "forced"):
            self.inventory.return_sale(
                "sale-1",
                1,
                failure_hook=lambda _connection: (_ for _ in ()).throw(
                    RuntimeError("forced")
                ),
            )
        self.assertEqual(self.stock(product["id"]), 1)
        self.assertEqual(self.inventory.get_sale("sale-1")["status"], "completed")
        self.assertEqual(len(self.inventory.list_movements(product["id"])), 1)

    def test_cannot_return_more_than_remaining(self):
        product = self.create_product(stock=3)
        self.inventory.create_sale(
            self.payload(product),
            product["id"],
            2,
            1000,
        )

        with self.assertRaises(ReturnConflictError):
            self.inventory.return_sale("sale-1", 3)

        self.assertEqual(self.stock(product["id"]), 1)
        self.assertEqual(
            self.inventory.get_sale("sale-1")["returned_quantity"],
            0,
        )

    def test_concurrent_sales_cannot_make_stock_negative(self):
        product = self.create_product(stock=1)

        def sell(sale_id):
            service = SalesInventory(
                CatalogDatabase(self.database_path)
            )
            try:
                service.create_sale(
                    self.payload(product, sale_id),
                    product["id"],
                    1,
                    1000,
                )
                return "ok"
            except InsufficientStockError:
                return "insufficient"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                sell,
                ("sale-a", "sale-b"),
            ))

        self.assertCountEqual(results, ["ok", "insufficient"])
        self.assertEqual(self.stock(product["id"]), 0)
        self.assertEqual(len(self.inventory.list_sales()), 1)

    def test_concurrent_return_cannot_restore_twice(self):
        product = self.create_product(stock=1)
        self.inventory.create_sale(
            self.payload(product),
            product["id"],
            1,
            1000,
        )

        def return_one(_index):
            service = SalesInventory(
                CatalogDatabase(self.database_path)
            )
            try:
                service.return_sale("sale-1", 1)
                return "ok"
            except ReturnConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(return_one, range(2)))

        self.assertCountEqual(results, ["ok", "conflict"])
        self.assertEqual(self.stock(product["id"]), 1)

    def test_cancellation_uses_net_movements_after_partial_return(self):
        product = self.create_product(stock=5)
        self.inventory.create_sale(
            self.payload(product), product["id"], 3, 1000,
        )
        self.inventory.return_sale("sale-1", 1, reason="Возврат клиента")

        cancelled = self.inventory.cancel_sale(
            "sale-1",
            reason="Ошибка ввода",
            comment="Исправим новой продажей",
            user_name="Тест",
        )

        self.assertEqual(self.stock(product["id"]), 5)
        self.assertEqual(cancelled["order_status"], "cancelled")
        self.assertEqual(cancelled["cancellation_reason"], "Ошибка ввода")
        self.assertEqual(cancelled["cancellation_comment"], "Исправим новой продажей")
        self.assertEqual(cancelled["cancelled_by"], "Тест")
        self.assertEqual(cancelled["created_at"], "2026-07-29")
        self.assertEqual(
            [item["diff"] for item in reversed(self.inventory.list_movements(product["id"]))],
            [-3, 1, 2],
        )

    def test_fully_returned_sale_cannot_be_cancelled(self):
        product = self.create_product(stock=2)
        self.inventory.create_sale(self.payload(product), product["id"], 1, 1000)
        self.inventory.return_sale("sale-1", 1)
        with self.assertRaisesRegex(CancellationConflictError, "Возвращённую"):
            self.inventory.cancel_sale("sale-1", reason="Дубль")
        self.assertEqual(self.stock(product["id"]), 2)

    def test_legacy_managed_sale_without_movement_cancels_without_stock_change(self):
        product = self.create_product(stock=2)
        self.inventory.create_sale(self.payload(product), product["id"], 1, 1000)
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM catalog_stock_movements WHERE sale_id = ?",
                ("sale-1",),
            )
        cancelled = self.inventory.cancel_sale("sale-1", reason="Дубль")
        self.assertEqual(cancelled["order_status"], "cancelled")
        self.assertEqual(self.stock(product["id"]), 1)
        self.assertEqual(self.inventory.list_movements(product["id"]), [])

    def test_contradictory_movements_block_cancellation(self):
        product = self.create_product(stock=2)
        self.inventory.create_sale(self.payload(product), product["id"], 1, 1000)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_stock_movements SET quantity_delta = 1 "
                "WHERE sale_id = ?",
                ("sale-1",),
            )
        with self.assertRaisesRegex(CancellationConflictError, "безопасно"):
            self.inventory.cancel_sale("sale-1", reason="Ошибка ввода")
        self.assertEqual(self.stock(product["id"]), 1)
        self.assertEqual(self.inventory.get_sale("sale-1")["order_status"], "completed")

    def test_cancellation_rolls_back_and_repeated_request_is_noop(self):
        product = self.create_product(stock=3)
        self.inventory.create_sale(self.payload(product), product["id"], 2, 1000)
        with self.assertRaisesRegex(RuntimeError, "forced"):
            self.inventory.cancel_sale(
                "sale-1",
                reason="Ошибка ввода",
                failure_hook=lambda _connection: (_ for _ in ()).throw(RuntimeError("forced")),
            )
        self.assertEqual(self.stock(product["id"]), 1)
        self.assertEqual(len(self.inventory.list_movements(product["id"])), 1)
        first = self.inventory.cancel_sale("sale-1", reason="Ошибка ввода")
        second = self.inventory.cancel_sale("sale-1", reason="Ошибка ввода")
        self.assertEqual(first["cancelled_at"], second["cancelled_at"])
        self.assertEqual(self.stock(product["id"]), 3)
        self.assertEqual(len(self.inventory.list_movements(product["id"])), 2)

    def test_active_delete_is_blocked_and_soft_delete_never_changes_stock(self):
        product = self.create_product(stock=3)
        self.inventory.create_sale(self.payload(product), product["id"], 1, 1000)
        with self.assertRaisesRegex(CancellationConflictError, "Сначала отмените"):
            self.inventory.delete_sale("sale-1")
        self.inventory.cancel_sale("sale-1", reason="Дубль")
        stock_before_delete = self.stock(product["id"])
        movements_before_delete = self.inventory.list_movements(product["id"])
        first = self.inventory.delete_sale("sale-1", user_name="Тест")
        second = self.inventory.delete_sale("sale-1", user_name="Тест")
        self.assertEqual(first["deleted_at"], second["deleted_at"])
        self.assertEqual(self.stock(product["id"]), stock_before_delete)
        self.assertEqual(self.inventory.list_movements(product["id"]), movements_before_delete)
        self.assertEqual(self.inventory.list_sales(), [])
        self.assertIsNotNone(self.inventory.get_sale("sale-1"))

    def test_cancel_inactive_historical_product_and_reuse_order_number(self):
        product = self.create_product(stock=3)
        self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
            enforce_external_unique=True,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET active = 0 WHERE id = ?",
                (product["id"],),
            )
        self.inventory.cancel_sale("sale-1", reason="Ошибка ввода")
        with self.database.transaction() as connection:
            stored_stock = connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id = ?",
                (product["id"],),
            ).fetchone()["stock"]
            self.assertEqual(stored_stock, 3)
            connection.execute(
                "UPDATE catalog_excel_products SET active = 1 WHERE id = ?",
                (product["id"],),
            )
        replacement = self.payload(product, "sale-2")
        created = self.inventory.create_sale(
            replacement, product["id"], 1, 1000,
            enforce_external_unique=True,
        )
        self.assertEqual(created["id"], "sale-2")

    def test_external_order_uniqueness_is_serialized_without_partial_index(self):
        product = self.create_product(stock=5)

        def create(sale_id):
            return self.inventory.create_sale(
                self.payload(product, sale_id),
                product["id"],
                1,
                1000,
                enforce_external_unique=True,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            created = list(executor.map(create, ("sale-race-1", "sale-race-2")))

        self.assertEqual(created[0]["id"], created[1]["id"])
        self.assertEqual(self.stock(product["id"]), 4)
        with self.database.connect() as connection:
            active = connection.execute(
                "SELECT COUNT(*) AS count FROM erp_sales "
                "WHERE source = ? AND external_order_id = ? "
                "AND cancelled_at IS NULL AND deleted_at IS NULL",
                ("Tictactoy", "125"),
            ).fetchone()
        self.assertEqual(active["count"], 1)

    def test_schema_replaces_legacy_unique_index_without_partial_index(self):
        product = self.create_product(stock=5)
        with self.database.transaction() as connection:
            connection.execute(
                "DROP INDEX IF EXISTS idx_erp_sales_source_external"
            )
            connection.execute(
                "CREATE UNIQUE INDEX idx_erp_sales_source_external "
                "ON erp_sales(source, external_order_id)"
            )
        with sqlite3.connect(str(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            apply_fresh_catalog_schema(connection)
        CatalogDatabase(
            self.database_path,
            cache_initialization=False,
        ).initialize()
        with self.database.connect() as connection:
            index = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_erp_sales_source_external'"
            ).fetchone()
            index_list = connection.execute(
                "PRAGMA index_list(erp_sales)"
            ).fetchall()
            unique_by_name = {
                row["name"]: row["unique"] for row in index_list
            }
        self.assertIsNotNone(index)
        self.assertNotIn(" WHERE ", (index["sql"] or "").upper())
        self.assertEqual(unique_by_name["idx_erp_sales_source_external"], 0)

        first = self.inventory.create_sale(
            self.payload(product, "sale-active-1"),
            product["id"],
            1,
            1000,
            enforce_external_unique=True,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_sales ("
                "id, source, external_order_id, status, created_at, "
                "metadata_json, inserted_at, updated_at"
                ") SELECT ?, source, external_order_id, status, created_at, "
                "metadata_json, inserted_at, updated_at FROM erp_sales WHERE id = ?",
                ("sale-existing-duplicate", first["id"]),
            )
        CatalogDatabase(
            self.database_path,
            cache_initialization=False,
        ).initialize()
        repeated = self.inventory.create_sale(
            self.payload(product, "sale-active-3"),
            product["id"],
            1,
            1000,
            enforce_external_unique=True,
        )
        self.assertIn(repeated["id"], {first["id"], "sale-existing-duplicate"})
        self.assertEqual(self.stock(product["id"]), 4)

    def test_list_sales_loads_cancellation_plans_without_n_plus_one(self):
        product = self.create_product(stock=10)
        for index in range(5):
            self.inventory.create_sale(
                self.payload(product, "query-sale-{}".format(index)),
                product["id"],
                1,
                1000,
            )
        statements = []
        original_connect = self.database.connect

        @contextmanager
        def traced_connect():
            with original_connect() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        with mock.patch.object(self.database, "connect", traced_connect):
            sales = self.inventory.list_sales()
        movement_queries = [
            statement for statement in statements
            if "FROM catalog_stock_movements" in statement
        ]
        self.assertEqual(len(sales), 5)
        self.assertEqual(len(movement_queries), 1)


class SalesInventoryWebTest(SalesInventoryTest):
    def setUp(self):
        global web
        from app import web as web_module

        web = web_module
        super().setUp()
        self.product = self.create_product(
            stock=3,
            article="ARTICLE-WEB-BASE",
        )
        self.item = {
            "id": str(self.product["id"]),
            "name": self.product["display_name"],
            "article": self.product["excel_article"] or "",
            "code": "",
            "barcode": "",
            "brand": self.product["display_brand"],
            "category": self.product["display_category"],
            "stock": 3,
            "stock_display": "3",
        }
        self.manual_sales_path = self.temp_path / "manual_sales.json"
        self.overrides_path = (
            self.temp_path / "automatic_sales_overrides.json"
        )
        self.environment = mock.patch.dict(
            os.environ,
            {"CATALOG_DATABASE_PATH": str(self.database_path)},
        )
        self.patchers = [
            self.environment,
            mock.patch.object(
                web,
                "get_manual_sales_path",
                return_value=self.manual_sales_path,
            ),
            mock.patch.object(
                web,
                "get_automatic_sales_overrides_path",
                return_value=self.overrides_path,
            ),
            mock.patch.object(
                web,
                "get_excel_warehouse_items",
                return_value=[self.item],
            ),
            mock.patch.object(
                web,
                "get_warehouse_items",
                return_value=[self.item],
            ),
            mock.patch.object(
                web,
                "load_stock_operations",
                return_value=[],
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        super().tearDown()

    def add_sale(self, quantity=1, idempotency_key=""):
        return self.client.post(
            "/sales/manual/add",
            data={
                "created_at": "2026-07-29",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "product_name": self.product["display_name"],
                "product_brand": self.product["display_brand"],
                "product_category": self.product["display_category"],
                "quantity": str(quantity),
                "unit_price": "1000",
                "order_number": "125",
                "idempotency_key": idempotency_key,
            },
        )

    def test_manual_sale_post_is_idempotent_and_respects_current_stock(self):
        first = self.add_sale(quantity=2, idempotency_key="manual-repeat")
        repeated = self.add_sale(quantity=2, idempotency_key="manual-repeat")

        self.assertEqual((first.status_code, repeated.status_code), (302, 302))
        self.assertEqual(len(self.inventory.list_sales()), 1)
        self.assertEqual(self.stock(self.product["id"]), 1)

        insufficient = self.add_sale(quantity=2, idempotency_key="too-many")
        self.assertEqual(insufficient.status_code, 302)
        self.assertIn(
            "Сейчас доступно: 1 шт.",
            parse_qs(urlparse(insufficient.headers["Location"]).query)["message"][0],
        )
        self.assertEqual(len(self.inventory.list_sales()), 1)
        self.assertEqual(self.stock(self.product["id"]), 1)

    def test_manual_sale_uses_catalog_taxonomy_when_form_category_is_stale(self):
        response = self.client.post(
            "/sales/manual/add",
            data={
                "created_at": "2026-07-29",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "product_name": self.product["display_name"],
                "product_brand": "Устаревший бренд заказа",
                "product_category": "Устаревшая категория заказа",
                "brand_id": "999999",
                "category_id": "999999",
                "quantity": "1",
                "unit_price": "1000",
                "idempotency_key": "stale-order-taxonomy",
            },
        )
        self.assertEqual(response.status_code, 302)
        sale = self.inventory.list_sales()[0]
        self.assertEqual(sale["brand"], self.product["display_brand"])
        self.assertEqual(sale["category"], self.product["display_category"])
        self.assertEqual(self.stock(self.product["id"]), 2)

    def test_new_sale_form_does_not_offer_return_status(self):
        self.assertNotIn("returned", web.SALE_FORM_STATUS_LABELS)
        page = self.client.get("/app/sales?source=all")
        self.assertEqual(page.status_code, 200)
        form_fragment = page.get_data(as_text=True).split(
            'id="manualSaleForm"', 1
        )[-1].split("</form>", 1)[0]
        self.assertNotIn('data-brand="returned"', form_fragment)

    def create_managed_sale(
        self,
        source="Tictactoy",
        quantity=1,
        sale_id="sale-web",
    ):
        payload = self.payload(self.product, sale_id)
        payload.update({
            "created_at": "2026-08-04T14:14",
            "source": source,
            "order_number": "ORDER-{}".format(sale_id),
            "order_status": "completed",
        })
        return self.inventory.create_sale(
            payload,
            self.product["id"],
            quantity,
            1000,
        )

    def update_sale_form(self, sale, idempotency_key="", **changes):
        data = {
            "sale_id": sale["id"],
            "source": sale["source"],
            "product_id": sale["product_id"],
            "product_name": self.product["display_name"],
            "created_at": sale["created_at"],
            "quantity": str(sale["quantity"]),
            "unit_price": str(sale["unit_price"]),
            "order_number": sale.get("order_number") or "",
            "note": sale.get("note") or "",
        }
        data.update(changes)
        data = {
            key: value
            for key, value in data.items()
            if value is not None
        }
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self.client.post(
            "/sales/manual/update",
            data=data,
            headers=headers,
        )

    def cancel_sale_form(
        self, sale, reason="input_error", comment="", sale_type="manual"
    ):
        return self.client.post(
            "/sales/cancel",
            data={
                "sale_id": sale["id"],
                "sale_type": sale_type,
                "cancellation_reason": reason,
                "cancellation_comment": comment,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    def delete_sale_form(self, sale, sale_type="manual"):
        return self.client.post(
            "/sales/delete",
            data={"sale_id": sale["id"], "sale_type": sale_type},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    def create_channel_sale(self, source, **metadata):
        payload = self.payload(
            self.product,
            "sale-channel-{}".format(source.casefold()),
        )
        payload.update({
            "source": source,
            "created_at": "2026-08-04T14:14",
            "order_status": "completed",
            "metadata_marker": "сохранить",
            **metadata,
        })
        return self.inventory.create_sale(
            payload,
            self.product["id"],
            1,
            1000,
        )

    def assert_channel_information_edit_allowed(self, source, **metadata):
        sale = self.create_channel_sale(source, **metadata)
        movements_before = self.inventory.list_movements(self.product["id"])
        response = self.update_sale_form(sale, note="Изменено")

        self.assertEqual(response.status_code, 200)
        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["source"], source)
        self.assertEqual(stored.get("note"), "Изменено")
        self.assertEqual(stored["metadata_marker"], "сохранить")
        for key, value in metadata.items():
            self.assertEqual(stored[key], value)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )

    def test_tictactoy_edit_is_blocked(self):
        self.assert_channel_information_edit_allowed(
            "Tictactoy",
            delivery_cost=350,
            commission="Оплата по СБП (0)",
            track_number="TT-ТРЕК",
            country="Россия",
            region="Москва",
            city="Москва",
        )

    def test_wildberries_edit_is_blocked(self):
        self.assert_channel_information_edit_allowed(
            "Wildberries",
            sticker_number="WB-СТИКЕР",
        )

    def test_amazon_edit_is_blocked(self):
        self.assert_channel_information_edit_allowed(
            "Amazon",
            recipient_name="Иван Иванов",
            platform="Amazon.de",
            country="Германия",
            invoice_number="AMZ-ТРЕК",
        )

    def test_manual_update_is_blocked_for_all_channels(self):
        initial_stock = self.stock(self.product["id"])
        sales = [
            self.create_managed_sale(source, sale_id="sale-{}".format(index))
            for index, source in enumerate(
                ("Tictactoy", "Wildberries", "Amazon"),
                start=1,
            )
        ]
        movements_before = self.inventory.list_movements(self.product["id"])

        for sale in sales:
            with self.subTest(source=sale["source"]):
                response = self.update_sale_form(
                    sale,
                    note="QA {}".format(sale["source"]),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content_type, "application/json")
                self.assertTrue(response.get_json()["ok"])
                stored = self.inventory.get_sale(sale["id"])
                self.assertEqual(stored["id"], sale["id"])
                self.assertEqual(stored["source"], sale["source"])
                self.assertEqual(
                    stored.get("note"), "QA {}".format(sale["source"])
                )

        self.assertEqual(self.stock(self.product["id"]), initial_stock - 3)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )
        self.assertEqual(len(self.inventory.list_sales()), 3)
        page = self.client.get("/app/sales?source=all")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Редактировать данные продажи", page.get_data(as_text=True))

    def test_manual_update_quantity_is_blocked(self):
        sale = self.create_managed_sale(quantity=1)

        increased = self.update_sale_form(sale, quantity="2")
        self.assertEqual(increased.status_code, 409)
        self.assertEqual(self.stock(self.product["id"]), 2)

        sale = self.inventory.get_sale(sale["id"])
        decreased = self.update_sale_form(sale, quantity="1")
        self.assertEqual(decreased.status_code, 200)
        self.assertEqual(self.stock(self.product["id"]), 2)

        sale = self.inventory.get_sale(sale["id"])
        repeated = self.update_sale_form(sale, quantity="1")
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.stock(self.product["id"]), 2)
        movements = self.inventory.list_movements(self.product["id"])
        self.assertEqual(len(movements), 1)

    def test_completed_sale_price_and_editable_fields_persist_for_all_channels(self):
        for index, source in enumerate(
            ("Tictactoy", "Wildberries", "Amazon"), start=1
        ):
            payload = self.payload(
                self.product,
                "editable-channel-{}".format(index),
            )
            payload.update({
                "created_at": "2026-08-04T14:14",
                "source": source,
                "order_number": "OLD-{}".format(index),
                "original_unit_price": "1000",
                "discount_type": "percent",
                "discount_value": "10",
                "discount_reason": "Историческая скидка",
            })
            sale = self.inventory.create_sale(
                payload,
                self.product["id"],
                1,
                1000,
            )
            stock_before = self.stock(self.product["id"])
            movements_before = self.inventory.list_movements(self.product["id"])

            response = self.update_sale_form(
                sale,
                unit_price="777.25",
                order_status="shipped",
                order_number="NEW-{}".format(index),
                track_number="TRACK-{}".format(index),
                delivery_cost="345.67",
                country="Германия" if source == "Amazon" else "Россия",
                region="Москва",
                city="Москва",
                recipient_name="Новый получатель",
                payment_method="Карта",
                platform="Amazon.de",
                invoice_number="AMZ-TRACK",
                sticker_number="WB-STICKER",
                note="Обновлено {}".format(source),
                **(
                    {"commission": "Оплата по СБП (0)"}
                    if source == "Tictactoy" else {}
                )
            )

            self.assertEqual(response.status_code, 200, source)
            stored = self.inventory.get_sale(sale["id"])
            self.assertEqual(stored["unit_price"], 777.25, source)
            self.assertEqual(stored["total_amount"], 777.25, source)
            self.assertEqual(stored["order_status"], "shipped", source)
            self.assertEqual(stored["order_number"], "NEW-{}".format(index), source)
            self.assertEqual(stored["track_number"], "TRACK-{}".format(index), source)
            self.assertEqual(stored["delivery_cost"], 345.67, source)
            self.assertEqual(
                stored["country"],
                "Германия" if source == "Amazon" else "Россия",
                source,
            )
            self.assertEqual(stored["region"], "Москва", source)
            self.assertEqual(stored["city"], "Москва", source)
            self.assertEqual(stored["recipient_name"], "Новый получатель", source)
            self.assertEqual(stored["payment_method"], "Карта", source)
            self.assertEqual(stored["platform"], "Amazon.de", source)
            self.assertEqual(stored["invoice_number"], "AMZ-TRACK", source)
            self.assertEqual(stored["sticker_number"], "WB-STICKER", source)
            if source == "Tictactoy":
                self.assertEqual(stored["commission"], "Оплата по СБП (0)")
            self.assertEqual(stored["note"], "Обновлено {}".format(source), source)
            self.assertEqual(stored["original_unit_price"], "1000.00", source)
            self.assertEqual(stored["discount_type"], "percent", source)
            self.assertEqual(stored["discount_value"], "10.00", source)
            self.assertEqual(stored["discount_reason"], "Историческая скидка", source)
            self.assertEqual(self.stock(self.product["id"]), stock_before, source)
            self.assertEqual(
                self.inventory.list_movements(self.product["id"]),
                movements_before,
                source,
            )
            reopened = self.client.get("/api/v1/sales/{}".format(sale["id"]))
            self.assertEqual(reopened.status_code, 200, source)
            reopened_sale = reopened.get_json()["data"]
            self.assertEqual(reopened_sale["unit_price"], 777.25, source)
            self.assertEqual(reopened_sale["total_amount"], 777.25, source)

    def test_completed_sale_date_is_rejected_by_form_and_api(self):
        sale = self.create_managed_sale()
        stock_before = self.stock(self.product["id"])
        movements_before = self.inventory.list_movements(self.product["id"])

        form_response = self.update_sale_form(
            sale,
            created_at="2026-08-05T14:14",
            unit_price="700",
        )
        api_response = self.client.patch(
            "/api/v1/sales/{}".format(sale["id"]),
            json={"date": "2026-08-06T14:14", "unit_price": 600},
        )

        self.assertEqual(form_response.status_code, 409)
        self.assertEqual(api_response.status_code, 409)
        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["created_at"], "2026-08-04T14:14")
        self.assertEqual(stored["unit_price"], 1000)
        self.assertEqual(self.stock(self.product["id"]), stock_before)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )

    def test_repeated_http_update_with_same_key_stays_blocked(self):
        sale = self.create_managed_sale(quantity=1)
        first = self.update_sale_form(
            sale,
            idempotency_key="sale-edit:sale-web:qa",
            quantity="2",
        )
        second = self.update_sale_form(
            sale,
            idempotency_key="sale-edit:sale-web:qa",
            quantity="2",
        )

        self.assertEqual(first.status_code, 409)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.stock(self.product["id"]), 2)
        self.assertEqual(
            len(self.inventory.list_movements(self.product["id"])),
            1,
        )

    def test_manual_update_cannot_replace_product(self):
        replacement = self.create_product(
            stock=5,
            name="Часы Replacement",
            article="ARTICLE-REPLACEMENT",
        )
        sale = self.create_managed_sale(quantity=2)

        response = self.update_sale_form(
            sale,
            product_id=str(replacement["id"]),
            product_name=replacement["display_name"],
            quantity="3",
        )

        self.assertEqual(response.status_code, 409)
        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["product_id"], str(self.product["id"]))
        self.assertEqual(self.stock(self.product["id"]), 1)
        self.assertEqual(self.stock(replacement["id"]), 5)
        self.assertEqual(
            len(self.inventory.list_movements(self.product["id"])),
            1,
        )
        self.assertEqual(
            len(self.inventory.list_movements(replacement["id"])),
            0,
        )

    def test_manual_update_is_rejected_before_inventory_service(self):
        sale = self.create_managed_sale(quantity=1)
        stock_before = self.stock(self.product["id"])
        movements_before = self.inventory.list_movements(self.product["id"])

        with mock.patch.object(
            SalesInventory,
            "update_sale",
            side_effect=RuntimeError("forced failure"),
        ) as update_sale:
            response = self.update_sale_form(
                sale,
                quantity="2",
                note="Не должно сохраниться",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.content_type, "application/json")
        self.assertIn("изменить нельзя", response.get_json()["message"])
        update_sale.assert_not_called()
        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["quantity"], 1)
        self.assertEqual(stored.get("note") or "", "")
        self.assertEqual(self.stock(self.product["id"]), stock_before)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )

    def test_manual_update_never_reaches_stock_validation(self):
        sale = self.create_managed_sale(quantity=1)
        stock_before = self.stock(self.product["id"])
        movements_before = self.inventory.list_movements(self.product["id"])

        response = self.update_sale_form(sale, quantity="4")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.content_type, "application/json")
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("изменить нельзя", response.get_json()["message"])
        self.assertEqual(self.inventory.get_sale(sale["id"])["quantity"], 1)
        self.assertEqual(self.stock(self.product["id"]), stock_before)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )

    def test_legacy_manual_update_enforces_immutable_rule_atomically(self):
        legacy = {
            "id": "legacy-edit",
            "created_at": "2026-08-04T14:14",
            "source": "Tictactoy",
            "product_id": str(self.product["id"]),
            "product_name": self.product["display_name"],
            "brand": self.product["display_brand"],
            "category": self.product["display_category"],
            "quantity": 1,
            "unit_price": 1000,
            "note": "Старое",
            "inventory_managed": False,
        }
        web.save_manual_sales([legacy])

        allowed = self.update_sale_form(legacy, note="Новое")
        blocked = self.update_sale_form(
            {**legacy, "note": "Новое"},
            quantity="2",
            note="Не сохранять",
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(blocked.status_code, 409)
        stored = web.load_manual_sales()[0]
        self.assertEqual(stored["quantity"], 1)
        self.assertEqual(stored["note"], "Новое")

    def test_manual_update_without_source_is_still_blocked(self):
        sale = self.create_managed_sale(source="Amazon")
        response = self.update_sale_form(sale, source=None, note="No drift")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.inventory.get_sale(sale["id"])["source"],
            "Amazon",
        )
        self.assertEqual(
            self.inventory.get_sale(sale["id"])["note"],
            "No drift",
        )

    def test_sale_date_validation_is_python_36_compatible(self):
        self.assertEqual(
            web.validate_sale_form_date("2026-08-04T14:14"),
            "2026-08-04T14:14",
        )
        self.assertEqual(
            web.validate_sale_form_date("2026-08-04"),
            "2026-08-04",
        )
        self.assertEqual(
            web.validate_sale_form_date("2026-08-04T14:14:00+03:00"),
            "2026-08-04T14:14:00+03:00",
        )
        with self.assertRaisesRegex(ValueError, "корректную дату"):
            web.validate_sale_form_date("2026-02-30T14:14")

    def test_sale_editor_locks_protected_fields_in_frontend(self):
        template = (
            Path(web.app.root_path) / "templates" / "sales.html"
        ).read_text(encoding="utf-8")

        self.assertIn("openSaleEditor", template)
        self.assertIn("sales-row-edit", template)
        self.assertIn("sales-mobile-edit", template)
        self.assertIn("setProtectedSaleFieldsLocked(true)", template)
        self.assertIn('document.getElementById("created_at").readOnly = locked', template)
        self.assertEqual(template.count("data-sale-legacy-price-field"), 5)
        self.assertIn('? "Цена"', template)
        self.assertIn("saleFinalPrice.readOnly = !locked", template)
        self.assertIn(
            "Дата, бренд, категория, товар и количество защищены от изменения. "
            "Цену и остальные данные продажи можно редактировать",
            template,
        )
        self.assertIn("openCancellationModal", template)
        self.assertIn("Удалить отменённую запись?", template)

    def test_cancel_all_channels_without_external_api_calls(self):
        for index, source in enumerate(
            ("Tictactoy", "Wildberries", "Amazon"), start=1
        ):
            sale = self.create_managed_sale(
                source=source, sale_id="cancel-channel-{}".format(index)
            )
            stock_before = self.stock(self.product["id"])
            with mock.patch("app.web.requests.request") as external_request:
                response = self.cancel_sale_form(sale)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["message"], "Продажа отменена")
            self.assertEqual(self.stock(self.product["id"]), stock_before + 1)
            self.assertEqual(
                self.inventory.get_sale(sale["id"])["order_status"],
                "cancelled",
            )
            external_request.assert_not_called()

    def test_cancellation_reason_validation_and_other_comment(self):
        sale = self.create_managed_sale(sale_id="cancel-reason")
        missing = self.cancel_sale_form(sale, reason="")
        self.assertEqual(missing.status_code, 400)
        other_missing = self.cancel_sale_form(sale, reason="other")
        self.assertEqual(other_missing.status_code, 400)
        accepted = self.cancel_sale_form(
            sale, reason="other", comment="Неверно оформлено"
        )
        self.assertEqual(accepted.status_code, 200)
        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["cancellation_reason"], "Другое")
        self.assertEqual(stored["cancellation_comment"], "Неверно оформлено")

    def test_delete_requires_cancellation_and_is_soft(self):
        sale = self.create_managed_sale(sale_id="soft-delete")
        blocked = self.delete_sale_form(sale)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("Сначала отмените", blocked.get_json()["message"])
        self.cancel_sale_form(sale, reason="duplicate")
        stock_before = self.stock(self.product["id"])
        deleted = self.delete_sale_form(sale)
        repeated = self.delete_sale_form(sale)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.stock(self.product["id"]), stock_before)
        self.assertNotIn(
            "ORDER-soft-delete",
            self.client.get("/app/sales?source=all").get_data(as_text=True),
        )
        self.assertTrue(self.inventory.get_sale(sale["id"])["deleted_at"])

    def test_api_patch_is_blocked_and_cancel_is_separate_from_return(self):
        sale = self.create_managed_sale(sale_id="api-block")
        patched = self.client.patch(
            "/api/v1/sales/{}".format(sale["id"]),
            json={"quantity": 2},
        )
        self.assertEqual(patched.status_code, 409)
        self.assertIn("изменить нельзя", patched.get_json()["message"])
        cancelled = self.client.post(
            "/api/v1/sales/{}/cancel".format(sale["id"]),
            json={"reason": "customer_refused"},
        )
        self.assertEqual(cancelled.status_code, 200)
        movements = self.inventory.list_movements(self.product["id"])
        self.assertEqual(movements[0]["type"], "cancellation")
        self.assertNotIn("return", [item["type"] for item in movements])
        page = self.client.get("/app/sales?source=tictactoy&status=refusal")
        self.assertIn("Отказ", page.get_data(as_text=True))
        self.assertIn(
            "sale-status-badge--danger", page.get_data(as_text=True)
        )

    def test_actions_render_in_every_source_with_information_edit(self):
        for index, source in enumerate(
            ("Tictactoy", "Wildberries", "Amazon"), start=1
        ):
            self.create_managed_sale(
                source=source, sale_id="render-source-{}".format(index)
            )
        for source in ("all", "tictactoy", "wildberries", "amazon"):
            page = self.client.get("/app/sales?source={}".format(source))
            self.assertEqual(page.status_code, 200)
            text = page.get_data(as_text=True)
            self.assertIn("Редактировать данные продажи", text)
            self.assertIn("openSaleEditor", text)
            self.assertIn("Отменить продажу", text)

    def test_cancelled_row_has_delete_menu_and_marketplace_warning(self):
        sale = self.create_managed_sale(
            source="Wildberries", sale_id="cancelled-ui"
        )
        self.cancel_sale_form(sale, reason="duplicate")
        text = self.client.get(
            "/app/sales?source=wildberries&status=cancelled"
        ).get_data(as_text=True)
        self.assertIn("Отменён", text)
        self.assertIn("Удалить запись", text)
        self.assertIn(
            "Отмена действует только в ERP и не изменяет заказ на площадке.",
            text,
        )
        self.assertNotIn("Редактировать продажу", text)

    def test_legacy_sale_is_not_written_off_during_schema_migration(self):
        self.manual_sales_path.write_text(
            '[{"id":"legacy","product_id":"%s","quantity":1}]'
            % self.product["id"],
            encoding="utf-8",
        )
        before = self.stock(self.product["id"])

        CatalogDatabase(self.database_path).initialize()
        sales = web.load_manual_sales()

        self.assertEqual(self.stock(self.product["id"]), before)
        self.assertEqual(sales[0]["id"], "legacy")

    def test_archive_route_keeps_sale_in_regular_history_and_kpi(self):
        sale = self.create_managed_sale(sale_id="archive-web")
        response = self.client.post(
            "/sales/archive",
            data={
                "sale_id": sale["id"],
                "sale_type": "manual",
                "archived": "1",
                "return_source": "all",
            },
        )
        self.assertEqual(response.status_code, 302)
        active = self.client.get("/app/sales?source=all").get_data(as_text=True)
        legacy_archive_url = self.client.get(
            "/app/sales?source=all&view=archive"
        ).get_data(as_text=True)
        self.assertIn('data-sale-id="archive-web"', active)
        self.assertIn('data-sale-id="archive-web"', legacy_archive_url)
        self.assertNotIn("data-sales-archive-tab", active)
        self.assertIn('id="statSales"', active)
        self.assertIn(">1</span>", active)

        restored = self.client.post(
            "/sales/archive",
            data={
                "sale_id": sale["id"],
                "sale_type": "manual",
                "archived": "0",
                "return_source": "all",
            },
        )
        self.assertEqual(restored.status_code, 302)
        self.assertFalse(self.inventory.get_sale(sale["id"])["archived_at"])

    def test_today_filter_uses_moscow_date_of_performed_sale(self):
        from datetime import datetime as real_datetime, timedelta, timezone

        class FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(
                    2026, 8, 18, 12, 0,
                    tzinfo=timezone(timedelta(hours=3)),
                )
                return value if tz is None else value.astimezone(tz)

        sales = [
            {"id": "today", "created_at": "2026-08-17T21:30:00+00:00"},
            {"id": "yesterday", "created_at": "2026-08-17T20:30:00+00:00"},
        ]
        with mock.patch.object(web, "datetime", FixedDatetime):
            result = web.filter_sales_report_records(
                sales, {"today": "1", "source": ""},
            )
        self.assertEqual([sale["id"] for sale in result], ["today"])


if __name__ == "__main__":
    unittest.main()
