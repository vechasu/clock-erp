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

    def test_sale_and_return_routes_update_ui_stock_and_net_revenue(self):
        response = self.add_sale(quantity=2)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stock(self.product["id"]), 1)

        sale = SalesInventory(self.database).list_sales()[0]
        page = self.client.get("/sales").get_data(as_text=True)
        self.assertIn("Оформить возврат", page)
        self.assertIn("data-sale-id=\"{}\"".format(sale["id"]), page)

        response = self.client.post(
            "/sales/return",
            data={
                "sale_id": sale["id"],
                "return_quantity": "1",
                "return_reason": "Не подошло",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stock(self.product["id"]), 2)

        with web.app.test_request_context(
            "/sales/report?source=all"
        ):
            context = web.build_sales_report_context()
        self.assertEqual(context["gross_revenue"], 2000)
        self.assertEqual(context["returns_amount"], 1000)
        self.assertEqual(context["total_revenue"], 1000)
        record = context["sales"][0]
        self.assertEqual(record["order_status"], "partially_returned")
        self.assertEqual(record["returned_quantity"], 1)
        self.assertEqual(record["return_reason"], "Не подошло")

        page = self.client.get("/sales").get_data(as_text=True)
        self.assertIn("is-partially-returned", page)
        self.assertIn("Частичный возврат:", page)

    def test_second_full_return_is_rejected_without_stock_change(self):
        self.add_sale()
        sale_id = SalesInventory(self.database).list_sales()[0]["id"]
        first = self.client.post(
            "/sales/return",
            data={
                "sale_id": sale_id,
                "return_quantity": "1",
            },
        )
        second = self.client.post(
            "/sales/return",
            data={
                "sale_id": sale_id,
                "return_quantity": "1",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.stock(self.product["id"]), 3)
        page = self.client.get("/sales").get_data(as_text=True)
        self.assertIn("is-returned", page)
        self.assertIn("Возврат оформлен", page)

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
