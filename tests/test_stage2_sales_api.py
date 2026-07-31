import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductBatchService, ExcelProductCatalog


def product_result():
    return {
        "excel_row": 2,
        "excel_name": "Casio G-Shock",
        "excel_brand": "Casio",
        "excel_article": "GA-2100",
        "article_quality": "code_like",
        "category": "Часы",
        "stock": 5.0,
        "stock_valid": True,
        "cell": "A-1",
        "product_id": None,
        "match_status": "not_found",
        "match_method": "test",
        "confidence": 0,
        "alternatives": [],
    }


class Stage2SalesApiTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "catalog.db"
        self.manual_path = self.root / "manual_sales.json"
        self.overrides_path = self.root / "automatic_sales_overrides.json"
        self.operations_path = self.root / "stock_operations.json"
        self.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(self.database_path)},
        )
        self.environment.start()
        self.patchers = [
            mock.patch.object(
                web,
                "get_manual_sales_path",
                return_value=self.manual_path,
            ),
            mock.patch.object(
                web,
                "get_automatic_sales_overrides_path",
                return_value=self.overrides_path,
            ),
            mock.patch.object(
                web,
                "get_stock_operations_path",
                return_value=self.operations_path,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        database = CatalogDatabase(self.database_path)
        ExcelProductBatchService(database).apply(
            [product_result()],
            "b" * 64,
            "sales.xlsx",
        )
        self.product = ExcelProductCatalog(database).list_products()["items"][0]
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.environment.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp.cleanup()

    def create_sale(self, quantity=2):
        return self.client.post(
            "/api/sales",
            json={
                "created_at": "2026-07-30",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "quantity": quantity,
                "unit_price": 1000,
                "order_number": "ORDER-1",
                "note": "API sale",
            },
        )

    def stock(self):
        return ExcelProductCatalog(
            CatalogDatabase(self.database_path)
        ).get_product(self.product["id"])["stock"]

    def test_create_list_catalog_patch_and_return_are_transactional(self):
        catalog = self.client.get("/api/sales/catalog").get_json()
        self.assertEqual(catalog["data"][0]["id"], str(self.product["id"]))

        created = self.create_sale()
        self.assertEqual(created.status_code, 201)
        sale = created.get_json()["data"]
        self.assertTrue(sale["inventory_managed"])
        self.assertEqual(self.stock(), 3)

        listing = self.client.get(
            "/api/v1/sales?q=order-1&source=tictactoy"
            "&sort_by=total_amount&sort_dir=desc&page_size=1"
        ).get_json()
        self.assertEqual(listing["meta"]["total"], 1)
        self.assertEqual(listing["meta"]["total_pages"], 1)
        self.assertEqual(listing["meta"]["totals"]["revenue"], 2000)
        self.assertEqual(listing["data"][0]["note"], "API sale")

        aliases = self.client.get(
            "/api/v1/sales?search=order-1&source=tictactoy"
            "&sort=total_amount&order=desc&page_size=1"
        ).get_json()
        self.assertEqual(aliases["meta"]["total"], 1)

        updated = self.client.patch(
            "/api/sales/{}".format(sale["id"]),
            json={
                "created_at": "2026-07-30",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "quantity": 2,
                "unit_price": 1250,
                "order_number": "ORDER-1",
                "note": "Updated",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["data"]["total_amount"], 2500)
        self.assertEqual(self.stock(), 3)

        returned = self.client.post(
            "/api/sales/{}/returns".format(sale["id"]),
            json={"quantity": 1, "reason": "Не подошло"},
        )
        self.assertEqual(returned.status_code, 201)
        self.assertEqual(returned.get_json()["data"]["returned_quantity"], 1)
        self.assertEqual(self.stock(), 4)

        blocked = self.client.delete("/api/sales/{}".format(sale["id"]))
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.get_json()["code"], "SALE_NOT_EDITABLE")

    def test_insufficient_stock_and_invalid_payload_are_structured(self):
        insufficient = self.create_sale(quantity=6)
        self.assertEqual(insufficient.status_code, 409)
        self.assertEqual(insufficient.get_json()["code"], "INSUFFICIENT_STOCK")
        self.assertEqual(self.stock(), 5)

        invalid = self.client.post(
            "/api/sales",
            json={
                "created_at": "bad",
                "product_id": str(self.product["id"]),
                "quantity": 0,
                "unit_price": 0,
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["code"], "SALE_VALIDATION_FAILED")

    def test_location_catalog_is_available_for_cascading_selects(self):
        with mock.patch.object(
            web,
            "get_tictactoy_location_catalog",
            return_value={"Россия": {"Москва": ["Москва"]}},
        ):
            response = self.client.get("/api/sales/locations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["data"]["Россия"]["Москва"],
            ["Москва"],
        )

    def test_legacy_and_automatic_sales_keep_source_specific_delete(self):
        self.manual_path.write_text(
            json.dumps([{
                "id": "legacy-1",
                "created_at": "2026-07-29",
                "source": "Amazon",
                "product_id": str(self.product["id"]),
                "product_name": "Casio G-Shock",
                "brand": "Casio",
                "category": "Часы",
                "quantity": 1,
                "unit_price": 900,
                "order_number": "OZ-1",
            }]),
            encoding="utf-8",
        )
        self.operations_path.write_text(
            json.dumps([{
                "id": "automatic-1",
                "created_at": "2026-07-28",
                "source": "Заказ Битрикс",
                "type": "writeoff",
                "product_id": str(self.product["id"]),
                "product_name": "Casio G-Shock",
                "quantity": 1,
                "order_number": "BX-1",
            }]),
            encoding="utf-8",
        )
        listing = self.client.get("/api/sales?source=all").get_json()
        self.assertEqual(
            {item["sale_type"] for item in listing["data"]},
            {"manual", "automatic"},
        )

        legacy_deleted = self.client.delete("/api/sales/legacy-1")
        automatic_deleted = self.client.delete("/api/sales/automatic-1")
        self.assertEqual(legacy_deleted.status_code, 200)
        self.assertEqual(automatic_deleted.status_code, 200)
        self.assertEqual(
            self.client.get("/api/sales?source=all").get_json()["meta"]["total"],
            0,
        )

    def test_unchanged_sales_sources_are_built_once_for_repeated_pages(self):
        web._cached_api_sales_records.cache_clear()
        with mock.patch.object(
            web,
            "load_manual_sales",
            wraps=web.load_manual_sales,
        ) as load_manual_sales:
            first = self.client.get("/api/v1/sales?page=1&page_size=1")
            second = self.client.get("/api/v1/sales?page=2&page_size=1")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        load_manual_sales.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
