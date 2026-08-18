import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import SalesInventory
from app.services.shared_catalog import SharedCatalog


class OrderTictactoySaleTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp_directory.name) / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id, file_sha256, source_filename, "
                "row_count, total_stock, positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('batch-order', 'sha-order', 'order.xlsx', 0, 0, 0, 0, "
                "'active', '2026-08-18T00:00:00+00:00', '2026-08-18T00:00:00+00:00')"
            )
        catalog = ExcelProductCatalog(self.database)
        self.watch = catalog.create_product(
            name="Bradley Steel", article="BRADLEY-STEEL", brand="Bradley",
            category="Часы", stock=5,
        )
        self.strap = catalog.create_product(
            name="Ремешок Bradley", article="STRAP-1", brand="Bradley",
            category="Ремешки", stock=3,
        )
        self.shared = SharedCatalog(self.database)
        self.inventory = SalesInventory(self.database)
        self.order = {
            "id": "18593", "number": "18593", "status": "A",
            "customer": "Иван Иванов", "order_total": 17400,
            "created_at": "2026-08-18 10:00",
            "products": [
                {"id": "line-1", "product_id": "bx-watch", "name": "Часы", "quantity": 2, "price": 7500},
                {"id": "line-2", "product_id": "bx-strap", "name": "Ремешок", "quantity": 1, "price": 2400},
            ],
        }
        self.mappings = {
            "bx-watch": {"product_id": str(self.watch["id"])},
            "bx-strap": {"product_id": str(self.strap["id"])},
        }

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp_directory.cleanup()

    def patches(self, order=None, mappings=None):
        selected = self.order if order is None else order
        return (
            mock.patch.object(web, "get_orders", return_value=[selected]),
            mock.patch.object(web, "get_order", return_value=selected),
            mock.patch.object(web, "load_product_mappings", return_value=self.mappings if mappings is None else mappings),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "SalesInventory", return_value=self.inventory),
            mock.patch.object(web, "load_stock_operations", return_value=[]),
        )

    def render_order(self, query=""):
        patches = self.patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            return self.client.get("/order/18593" + query)

    def conduct(self, order=None, mappings=None):
        patches = self.patches(order, mappings)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            return self.client.post("/order/18593/stock-writeoff", data={"csrf_token": "test-token"})

    def test_shared_catalog_finds_bradley_and_new_products_without_restart(self):
        found = self.shared.list_products(query="Bradley Steel")
        self.assertEqual([item["id"] for item in found], [str(self.watch["id"])])
        created = ExcelProductCatalog(self.database).create_product(
            name="Bradley Steel New", article="BRADLEY-NEW", brand="Bradley",
            category="Часы", stock=0,
        )
        self.assertIn(str(created["id"]), [item["id"] for item in self.shared.list_products(query="BRADLEY-NEW")])

    def test_dialog_and_shared_cascade_are_rendered(self):
        html = self.render_order("?open_sale=1").get_data(as_text=True)
        self.assertIn('id="orderSaleModal"', html)
        self.assertIn('data-auto-open="1"', html)
        self.assertIn('data-shared-catalog-kind="brand"', html)
        self.assertIn('data-shared-catalog-kind="category"', html)
        self.assertIn('data-shared-catalog-kind="product"', html)
        self.assertIn("Bradley Steel", html)
        self.assertIn("Нет в наличии", Path("app/static/js/catalog-combobox.js").read_text())
        self.assertIn('name="csrf_token"', html)
        self.assertIn('type="button" data-close-sale-dialog', html)

    def test_successful_bitrix_confirmation_opens_dialog_but_does_not_sell(self):
        with (
            mock.patch.object(web, "update_order_status", return_value={"status": "ok"}),
            mock.patch.object(web, "get_orders", return_value=[self.order]),
            mock.patch.object(web, "SalesInventory", return_value=self.inventory),
            mock.patch.object(web, "load_stock_operations", return_value=[]),
        ):
            response = self.client.post("/order/18593/status", data={"status": "A"})
        self.assertEqual(parse_qs(urlsplit(response.location).query)["open_sale"], ["1"])
        self.assertEqual(self.inventory.list_sales(), [])

    def test_bitrix_confirmation_error_does_not_open_dialog(self):
        with (
            mock.patch.object(web, "update_order_status", return_value={
                "status": "error", "message": "Bitrix недоступен",
            }),
            mock.patch.object(web, "get_orders", return_value=[self.order]),
        ):
            response = self.client.post("/order/18593/status", data={"status": "A"})
        query = parse_qs(urlsplit(response.location).query)
        self.assertNotIn("open_sale", query)
        self.assertEqual(query["message"], ["Bitrix недоступен"])

    def test_success_is_one_local_sale_with_two_lines_and_exact_redirect(self):
        response = self.conduct()
        self.assertEqual(urlsplit(response.location).path, "/sales")
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query["source"], ["tictactoy"])
        self.assertEqual(query["order_number"], ["18593"])
        rows = self.inventory.list_sales()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["id"] for row in rows}), 1)
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"], 3)
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.strap["id"])["stock"], 2)
        report = web.build_sales_report_records(
            warehouse_items=[], operations=[], stored_manual_sales=rows,
            automatic_overrides={},
        )
        self.assertEqual(web.calculate_sales_kpis(report)["sales_count"], 1)

    def test_repeated_post_is_idempotent(self):
        first = self.conduct()
        second = self.conduct()
        self.assertEqual(urlsplit(first.location).path, "/sales")
        self.assertIn("уже проведена", parse_qs(urlsplit(second.location).query)["message"][0])
        self.assertEqual(len({row["id"] for row in self.inventory.list_sales()}), 1)
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"], 3)

    def test_all_validation_errors_are_returned_before_stock_change(self):
        order = dict(self.order)
        order["status"] = "N"
        order["products"] = [
            {"product_id": "missing", "name": "Нет связи", "quantity": 1},
            {"product_id": "bx-watch", "name": "Bradley Steel", "quantity": 6},
        ]
        response = self.conduct(order=order)
        message = parse_qs(urlsplit(response.location).query)["message"][0]
        self.assertIn("Сначала подтвердите заказ", message)
        self.assertIn("Нет связи", message)
        self.assertIn("требуется 6, доступно 5", message)
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"], 5)
        self.assertEqual(self.inventory.list_sales(), [])

    def test_duplicate_product_lines_use_aggregate_stock(self):
        order = dict(self.order)
        order["products"] = [
            {"product_id": "bx-watch", "name": "A", "quantity": 3},
            {"product_id": "bx-watch", "name": "B", "quantity": 3},
        ]
        response = self.conduct(order=order)
        self.assertIn("требуется 6, доступно 5", parse_qs(urlsplit(response.location).query)["message"][0])
        self.assertEqual(ExcelProductCatalog(self.database).get_product(self.watch["id"])["stock"], 5)

    def test_zero_stock_product_is_visible_but_cannot_be_conducted(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 0 WHERE id = ?",
                (self.watch["id"],),
            )
        self.assertEqual(self.shared.list_products(query="Bradley Steel")[0]["stock"], 0)
        response = self.conduct()
        self.assertIn("доступно 0", parse_qs(urlsplit(response.location).query)["message"][0])
        self.assertEqual(self.inventory.list_sales(), [])

    def test_mapping_is_stable_by_product_id_after_rename_and_archive_blocks_sale(self):
        catalog = ExcelProductCatalog(self.database)
        catalog.update_product(self.watch["id"], name="Переименованные часы")
        context = web.build_order_product_mapping_context(
            self.order["products"], mappings=self.mappings, catalog=self.shared,
        )
        self.assertEqual(context["bx-watch"]["product"]["name"], "Переименованные часы")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 0 WHERE id = ?",
                (self.watch["id"],),
            )
        catalog.archive_product(self.watch["id"])
        context = web.build_order_product_mapping_context(
            self.order["products"], mappings=self.mappings, catalog=self.shared,
        )
        self.assertEqual(context["bx-watch"]["state"], "archived")
        response = self.conduct()
        self.assertIn("архивирован", parse_qs(urlsplit(response.location).query)["message"][0])

    def test_mapping_endpoint_rejects_mismatched_cascade(self):
        product = self.shared.get_product(self.watch["id"])
        patches = self.patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = self.client.post("/order/18593/product-map", data={
                "bitrix_product_id": "bx-watch", "product_id": product["id"],
                "brand_id": "wrong", "category_id": product["category_id"],
            })
        self.assertIn(
            "не относится",
            parse_qs(urlsplit(response.location).query)["message"][0],
        )

    def test_mapping_endpoint_rejects_arbitrary_text_and_wrong_category(self):
        product = self.shared.get_product(self.watch["id"])
        patches = self.patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            arbitrary = self.client.post("/order/18593/product-map", data={
                "bitrix_product_id": "bx-watch", "product_id": "Bradley Steel",
                "brand_id": product["brand_id"], "category_id": product["category_id"],
            })
            wrong_category = self.client.post("/order/18593/product-map", data={
                "bitrix_product_id": "bx-watch", "product_id": product["id"],
                "brand_id": product["brand_id"], "category_id": "wrong",
            })
        self.assertIn("не найден", parse_qs(urlsplit(arbitrary.location).query)["message"][0])
        self.assertIn("категории", parse_qs(urlsplit(wrong_category.location).query)["message"][0])

    def test_mapping_saves_distinct_bitrix_and_erp_ids_and_preserves_legacy(self):
        product = self.shared.get_product(self.watch["id"])
        existing = {"legacy-bx": {"moysklad_product_id": "legacy-ms"}}
        saved = {}
        with (
            mock.patch.object(web, "get_order", return_value=self.order),
            mock.patch.object(web, "SharedCatalog", return_value=self.shared),
            mock.patch.object(web, "load_product_mappings", return_value=existing),
            mock.patch.object(web, "save_product_mappings", side_effect=lambda rows: saved.update(rows)),
            mock.patch.object(web, "AuditJournal"),
        ):
            response = self.client.post("/order/18593/product-map", data={
                "bitrix_product_id": "bx-watch", "product_id": product["id"],
                "brand_id": product["brand_id"], "category_id": product["category_id"],
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved["bx-watch"]["product_id"], str(self.watch["id"]))
        self.assertEqual(saved["bx-watch"]["bitrix_product_id"], "bx-watch")
        self.assertEqual(saved["bx-watch"]["bitrix_order_line_id"], "line-1")
        self.assertEqual(saved["legacy-bx"]["moysklad_product_id"], "legacy-ms")

    def test_refusal_status_is_blocked_until_completed_sale_is_cancelled(self):
        self.conduct()
        with (
            mock.patch.object(web, "SalesInventory", return_value=self.inventory),
            mock.patch.object(web, "load_stock_operations", return_value=[]),
            mock.patch.object(web, "update_order_status") as update,
        ):
            response = self.client.post("/order/18593/status", data={"status": "C"})
        update.assert_not_called()
        self.assertIn("Сначала откройте продажу", parse_qs(urlsplit(response.location).query)["message"][0])


if __name__ == "__main__":
    unittest.main()
