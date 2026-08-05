import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import (
    InsufficientStockError,
    ReturnConflictError,
    SalesInventory,
)


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

    def test_update_quantity_uses_stock_delta_and_rolls_back(self):
        product = self.create_product(stock=5)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
        )
        payload = {**sale, "quantity": 3}

        updated = self.inventory.update_sale(
            "sale-1", payload, 3, 1000, idempotency_key="update-1",
        )
        self.assertEqual(updated["quantity"], 3)
        self.assertEqual(self.stock(product["id"]), 2)

        def fail(_connection):
            raise RuntimeError("forced rollback")

        with self.assertRaises(RuntimeError):
            self.inventory.update_sale(
                "sale-1", {**updated, "quantity": 4}, 4, 1000,
                idempotency_key="update-2", failure_hook=fail,
            )
        self.assertEqual(self.stock(product["id"]), 2)
        self.assertEqual(self.inventory.get_sale("sale-1")["quantity"], 3)

    def test_update_quantity_applies_only_delta_and_repeated_values_are_noop(self):
        product = self.create_product(stock=5)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 1, 1000,
        )

        increased = self.inventory.update_sale(
            "sale-1", {**sale, "quantity": 2}, 2, 1000,
        )
        self.assertEqual(self.stock(product["id"]), 3)

        decreased = self.inventory.update_sale(
            "sale-1", {**increased, "quantity": 1}, 1, 1000,
        )
        self.assertEqual(self.stock(product["id"]), 4)

        repeated = self.inventory.update_sale(
            "sale-1", {**decreased, "quantity": 1}, 1, 1000,
        )
        movements = self.inventory.list_movements(product["id"])
        self.assertEqual(repeated["quantity"], 1)
        self.assertEqual(self.stock(product["id"]), 4)
        self.assertEqual(len(movements), 3)
        self.assertEqual(
            [movement["diff"] for movement in reversed(movements)],
            [-1, -1, 1],
        )

    def test_update_quantity_three_to_one_and_three_to_five_uses_only_delta(self):
        product = self.create_product(stock=10)
        sale = self.inventory.create_sale(
            self.payload(product), product["id"], 3, 1000,
        )
        self.assertEqual(self.stock(product["id"]), 7)

        decreased = self.inventory.update_sale(
            "sale-1", {**sale, "quantity": 1}, 1, 1000,
        )
        self.assertEqual(self.stock(product["id"]), 9)

        increased = self.inventory.update_sale(
            "sale-1", {**decreased, "quantity": 5}, 5, 1000,
        )
        self.assertEqual(increased["quantity"], 5)
        self.assertEqual(self.stock(product["id"]), 5)
        self.assertEqual(
            [item["diff"] for item in reversed(
                self.inventory.list_movements(product["id"])
            )],
            [-3, 2, -4],
        )

    def test_replace_with_insufficient_stock_rolls_back_both_products(self):
        old_product = self.create_product(stock=4)
        new_product = self.create_product(
            stock=1, name="Часы Limited", article="ARTICLE-2",
        )
        sale = self.inventory.create_sale(
            self.payload(old_product), old_product["id"], 2, 1000,
        )
        old_movements = self.inventory.list_movements(old_product["id"])

        with self.assertRaises(InsufficientStockError):
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

        updated = self.inventory.update_sale(
            "sale-1", replacement, 3, 1000, idempotency_key="replace-1",
        )
        self.assertEqual(updated["product_id"], str(new_product["id"]))
        self.assertEqual(self.stock(old_product["id"]), 4)
        self.assertEqual(self.stock(new_product["id"]), 2)

        returned = self.inventory.update_sale(
            "sale-1", {**updated, "order_status": "returned"}, 3, 1000,
            idempotency_key="status-returned",
        )
        self.assertEqual(returned["status"], "returned")
        self.assertEqual(self.stock(new_product["id"]), 5)

        shipped = self.inventory.update_sale(
            "sale-1", {**returned, "order_status": "shipped"}, 3, 1000,
            idempotency_key="status-shipped",
        )
        self.assertEqual(shipped["order_status"], "shipped")
        self.assertEqual(self.stock(new_product["id"]), 2)

    def test_delete_restores_stock_once_and_hides_sale(self):
        product = self.create_product(stock=3)
        self.inventory.create_sale(
            self.payload(product), product["id"], 2, 1000,
        )

        first = self.inventory.delete_sale(
            "sale-1", idempotency_key="sale-delete:sale-1",
        )
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


class SalesInventoryWebTest(SalesInventoryTest):
    def setUp(self):
        global web
        from app import web as web_module

        web = web_module
        super().setUp()
        self.product = self.create_product(stock=3)
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

    def add_sale(self, quantity=1):
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
            },
        )

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

    def assert_channel_metadata_preserved(self, source, **metadata):
        sale = self.create_channel_sale(source, **metadata)
        movements_before = self.inventory.list_movements(self.product["id"])
        response = self.update_sale_form(sale, note="Изменено")

        self.assertEqual(response.status_code, 200)
        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["source"], source)
        self.assertEqual(stored["note"], "Изменено")
        self.assertEqual(stored["metadata_marker"], "сохранить")
        for key, value in metadata.items():
            self.assertEqual(stored[key], value)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )

    def test_tictactoy_edit_preserves_channel_metadata(self):
        self.assert_channel_metadata_preserved(
            "Tictactoy",
            delivery_cost=350,
            commission="Оплата по СБП (0)",
            track_number="TT-ТРЕК",
            country="Россия",
            region="Москва",
            city="Москва",
        )

    def test_wildberries_edit_preserves_channel_metadata(self):
        self.assert_channel_metadata_preserved(
            "Wildberries",
            sticker_number="WB-СТИКЕР",
        )

    def test_amazon_edit_preserves_channel_metadata(self):
        self.assert_channel_metadata_preserved(
            "Amazon",
            recipient_name="Иван Иванов",
            platform="Amazon.de",
            country="Германия",
            invoice_number="AMZ-ТРЕК",
        )

    def test_manual_update_returns_json_and_preserves_inventory_for_all_channels(self):
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
                self.assertEqual(response.get_json(), {
                    "ok": True,
                    "message": "Изменения сохранены",
                })
                stored = self.inventory.get_sale(sale["id"])
                self.assertEqual(stored["id"], sale["id"])
                self.assertEqual(stored["source"], sale["source"])
                self.assertEqual(stored["note"], "QA {}".format(sale["source"]))

        self.assertEqual(self.stock(self.product["id"]), initial_stock - 3)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )
        self.assertEqual(len(self.inventory.list_sales()), 3)
        page = self.client.get("/app/sales?source=all")
        self.assertEqual(page.status_code, 200)
        page_text = page.get_data(as_text=True)
        for source in ("Tictactoy", "Wildberries", "Amazon"):
            self.assertIn("QA {}".format(source), page_text)

    def test_manual_update_quantity_is_delta_based_and_idempotent(self):
        sale = self.create_managed_sale(quantity=1)

        increased = self.update_sale_form(sale, quantity="2")
        self.assertEqual(increased.status_code, 200)
        self.assertEqual(self.stock(self.product["id"]), 1)

        sale = self.inventory.get_sale(sale["id"])
        decreased = self.update_sale_form(sale, quantity="1")
        self.assertEqual(decreased.status_code, 200)
        self.assertEqual(self.stock(self.product["id"]), 2)

        sale = self.inventory.get_sale(sale["id"])
        repeated = self.update_sale_form(sale, quantity="1")
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.stock(self.product["id"]), 2)
        movements = self.inventory.list_movements(self.product["id"])
        self.assertEqual(len(movements), 3)
        self.assertEqual(
            [movement["diff"] for movement in reversed(movements)],
            [-1, -1, 1],
        )

    def test_repeated_http_update_with_same_key_does_not_repeat_stock_change(self):
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

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.stock(self.product["id"]), 1)
        self.assertEqual(
            len(self.inventory.list_movements(self.product["id"])),
            2,
        )

    def test_manual_update_replaces_product_atomically(self):
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

        self.assertEqual(response.status_code, 200)
        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["product_id"], str(replacement["id"]))
        self.assertEqual(self.stock(self.product["id"]), 3)
        self.assertEqual(self.stock(replacement["id"]), 2)
        self.assertEqual(
            len(self.inventory.list_movements(self.product["id"])),
            2,
        )
        self.assertEqual(
            len(self.inventory.list_movements(replacement["id"])),
            1,
        )

    def test_manual_update_error_is_json_and_rolls_back(self):
        sale = self.create_managed_sale(quantity=1)
        stock_before = self.stock(self.product["id"])
        movements_before = self.inventory.list_movements(self.product["id"])

        with mock.patch.object(
            SalesInventory,
            "update_sale",
            side_effect=RuntimeError("forced failure"),
        ):
            response = self.update_sale_form(
                sale,
                quantity="2",
                note="Не должно сохраниться",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json(), {
            "ok": False,
            "message": "Изменения не сохранены. Остаток не изменён.",
        })
        stored = self.inventory.get_sale(sale["id"])
        self.assertEqual(stored["quantity"], 1)
        self.assertEqual(stored.get("note") or "", "")
        self.assertEqual(self.stock(self.product["id"]), stock_before)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )

    def test_manual_update_insufficient_stock_is_json_and_rolls_back(self):
        sale = self.create_managed_sale(quantity=1)
        stock_before = self.stock(self.product["id"])
        movements_before = self.inventory.list_movements(self.product["id"])

        response = self.update_sale_form(sale, quantity="4")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.content_type, "application/json")
        self.assertFalse(response.get_json()["ok"])
        self.assertIn(
            "Недостаточно товара на складе",
            response.get_json()["message"],
        )
        self.assertEqual(self.inventory.get_sale(sale["id"])["quantity"], 1)
        self.assertEqual(self.stock(self.product["id"]), stock_before)
        self.assertEqual(
            self.inventory.list_movements(self.product["id"]),
            movements_before,
        )

    def test_manual_update_preserves_source_when_source_field_is_missing(self):
        sale = self.create_managed_sale(source="Amazon")
        response = self.update_sale_form(sale, source=None, note="No drift")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.inventory.get_sale(sale["id"])["source"],
            "Amazon",
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

    def test_sale_editor_keeps_values_and_server_errors_in_the_open_modal(self):
        template = (
            Path(web.app.root_path) / "templates" / "sales.html"
        ).read_text(encoding="utf-8")

        self.assertIn('const sourceKey = sale.source_key || "tictactoy";', template)
        self.assertIn('saleDateValue + "T00:00"', template)
        self.assertIn('setSaleFormError(\n                error.message', template)
        self.assertIn('manualSaleModal.classList.add("is-open")', template)
        self.assertIn("if (saleEditSavePending)", template)
        self.assertIn('"Idempotency-Key": saleEditIdempotencyKey', template)
        self.assertIn(
            '"Не удалось сохранить изменения: ошибка сервера"',
            template,
        )
        self.assertNotIn(
            'catch (error) {\n            closeManualSaleModal()',
            template,
        )

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


if __name__ == "__main__":
    unittest.main()
