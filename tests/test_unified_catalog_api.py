import base64
import json
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigD//2Q=="
)


class UnifiedCatalogApiTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "catalog.db"
        self.receipts_path = self.root / "receipts.json"
        self.operations_path = self.root / "stock_operations.json"
        self.manual_sales_path = self.root / "manual_sales.json"
        self.overrides_path = self.root / "automatic_sales_overrides.json"
        self.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(self.database_path)},
        )
        self.environment.start()
        self.patchers = [
            mock.patch.object(
                web,
                "get_receipts_path",
                return_value=self.receipts_path,
            ),
            mock.patch.object(
                web,
                "get_stock_operations_path",
                return_value=self.operations_path,
            ),
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
        ]
        for patcher in self.patchers:
            patcher.start()
        database = CatalogDatabase(self.database_path)
        ExcelProductBatchService(database).apply(
            [{
                "excel_row": 2,
                "excel_name": "Служебная карточка",
                "excel_brand": "Служебный бренд",
                "excel_article": "SEED",
                "article_quality": "code_like",
                "category": "Служебная категория",
                "stock": 0.0,
                "stock_valid": True,
                "cell": "A-1",
                "product_id": None,
                "match_status": "not_found",
                "match_method": "test",
                "confidence": 0,
                "alternatives": [],
            }],
            "c" * 64,
            "unified.xlsx",
        )
        self.product = ExcelProductCatalog(database).create_product(
            name="Casio A168",
            article="A168",
            brand="Casio",
            category="Наручные часы",
            stock=0,
        )
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()
        self.moysklad_patch = mock.patch.object(web, "MoySkladClient")
        self.moysklad_class = self.moysklad_patch.start()
        self.remote = self.moysklad_class.return_value
        self.remote.find_product_by_code.return_value = None
        self.remote.create_product.return_value = {"id": "ms-casio-a168"}
        self.remote.create_stock_enter_many.return_value = {
            "id": "enter-1",
            "name": "ПР-1",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }
        self.remote.update_stock_enter_many.return_value = {
            "id": "enter-1",
            "name": "ПР-1",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }
        self.remote.delete_stock_enter.return_value = True
        self.remote.product_has_images.return_value = False
        self.remote.upload_product_image.return_value = True

    def tearDown(self):
        self.moysklad_patch.stop()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.environment.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp.cleanup()

    def stock(self):
        return ExcelProductCatalog(
            CatalogDatabase(self.database_path)
        ).get_product(self.product["id"])["stock"]

    def receipt_payload(self, quantity=10):
        return {
            "receipt_date": "2026-07-30",
            "note": "Интеграционный тест",
            "idempotency_key": "receipt-api-once",
            "positions": [{
                "product_id": str(self.product["id"]),
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "quantity": quantity,
                "purchase_price": 500,
            }],
        }

    def multipart_receipt(
            self,
            quantity=1,
            note="",
            image=None,
            filename="watch.png",
            mimetype="image/png",
            idempotency_key="receipt-multipart-once",
            submit_mode="close"):
        data = {
            "receipt_date": "2026-07-30",
            "note": note,
            "submit_mode": submit_mode,
            "positions": json.dumps([{
                "product_id": str(self.product["id"]),
                "quantity": quantity,
            }]),
        }
        if image is not None:
            data["product_image"] = (
                BytesIO(image),
                filename,
                mimetype,
            )
        return self.client.post(
            "/api/v1/receipts",
            data=data,
            content_type="multipart/form-data",
            headers={"Idempotency-Key": idempotency_key},
        )

    def test_full_api_flow_uses_one_card_and_one_stock_ledger(self):
        from app.services.supplies import SupplyEngine
        engine=SupplyEngine(CatalogDatabase(self.database_path))
        product=engine.resolve_bitrix({'external_product_id':'test-a168','name':'Casio A168','external_sku':'A168','stock':47})
        self.assertEqual(product['id'],self.product['id'])
        draft=engine.create('Integration',items=[{'product_id':product['id'],'quantity':10}])
        self.assertEqual(self.stock(),0)
        engine.post(draft['id'])
        engine.post(draft['id'])
        self.assertEqual(self.stock(),10)
        sale=self.client.post('/api/v1/sales',json={'created_at':'2026-07-30','source':'Tictactoy','product_id':str(product['id']),'quantity':3,'unit_price':1000,'order_number':'ORDER-API-1'})
        self.assertEqual(sale.status_code,201)
        self.assertEqual(self.stock(),7)
        sale_id=sale.get_json()['data']['id']
        cancelled=self.client.post('/api/v1/sales/'+sale_id+'/cancel',json={'reason':'input_error'})
        self.assertEqual(cancelled.status_code,200)
        self.assertEqual(self.stock(),10)
        self.assertEqual(self.client.patch('/api/v1/receipts/supplies/'+draft['id'],json={'title':'x','items':[]}).status_code,422)
        self.assertEqual(self.client.delete('/api/v1/receipts/supplies/'+draft['id']).status_code,422)
        movements=self.client.get('/api/v1/products/{}/movements'.format(product['id'])).get_json()['data']
        self.assertEqual({m['type'] for m in movements},{'receipt','sale','cancellation'})
        rows=self.client.get('/api/v1/receipts/movements').get_json()['data']
        self.assertEqual({m['source_type'] for m in rows},{'supply','sale_cancellation'})
        self.moysklad_class.assert_not_called()


    def test_shared_catalog_stock_statistics_follow_inventory_lifecycle(self):
        def brand_stock():
            options = self.client.get(
                "/api/v1/catalog/options?type=brand&limit=100"
            ).get_json()["data"]
            return next(
                item for item in options
                if item["id"] == self.product["brand_id"]
            )["stock_total"]

        self.assertEqual(brand_stock(), 0)
        from app.services.supplies import SupplyEngine
        engine=SupplyEngine(CatalogDatabase(self.database_path))
        product=engine.resolve_bitrix({'external_product_id':'test-a168','name':'Casio A168','external_sku':'A168','stock':47})
        receipt=engine.create('Statistics',items=[{'product_id':product['id'],'quantity':10}])
        engine.post(receipt['id'])
        self.assertEqual(brand_stock(), 10)

        sale = self.client.post(
            "/api/v1/sales",
            json={
                "created_at": "2026-07-30",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "quantity": 3,
                "unit_price": 1000,
                "order_number": "ORDER-STOCK-STATS",
            },
        ).get_json()["data"]
        self.assertEqual(brand_stock(), 7)

        self.client.post(
            "/api/v1/sales/{}/cancel".format(sale["id"]),
            json={"reason": "input_error"},
        )
        self.client.delete("/api/v1/sales/{}".format(sale["id"]))
        self.assertEqual(brand_stock(), 10)
        self.assertEqual(self.client.delete("/api/v1/receipts/supplies/{}".format(receipt["id"])).status_code,422)
        self.assertEqual(brand_stock(), 10)

    def test_receipt_requires_positive_integer_quantity(self):
        from app.services.supplies import SupplyEngine
        engine=SupplyEngine(CatalogDatabase(self.database_path))
        product=engine.resolve_bitrix({'external_product_id':'test-a168','name':'Casio A168','external_sku':'A168','stock':47})
        d=engine.create('Validation')
        for quantity in (1.5,'1,5',0,-1,'text',True,None):
            with self.subTest(quantity=quantity):
                r=self.client.patch('/api/v1/receipts/supplies/'+d['id'],json={'title':'Validation','items':[{'product_id':product['id'],'quantity':quantity}]})
                self.assertEqual(r.status_code,422)
                self.assertEqual(self.stock(),0)



    def test_unmapped_bitrix_product_supply_never_creates_moysklad_objects(self):
        from app.services.supplies import SupplyEngine
        engine=SupplyEngine(CatalogDatabase(self.database_path))
        product=engine.resolve_bitrix({'external_product_id':'new-bitrix','name':'New Bitrix','external_sku':'NEW-BITRIX','stock':47})
        self.assertEqual(product['stock'],0)
        d=engine.create('Local only',items=[{'product_id':product['id'],'quantity':6}])
        result=engine.post(d['id'])
        self.assertEqual(result['items'][0]['stock_after'],6)
        self.moysklad_class.assert_not_called()


    def test_manual_product_with_distinct_article_starts_without_stock(self):
        response = self.client.post(
            "/api/v1/products",
            json={
                "name": "  casio a168 ",
                "article": "ANOTHER",
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "stock": 0,
            },
        )

        self.assertEqual(response.status_code, 201)
        product = response.get_json()["data"]
        self.assertEqual(product["article"], "ANOTHER")
        self.assertEqual(product["stock"], 0)
        self.moysklad_class.assert_not_called()













    def test_products_sales_and_receipts_use_the_same_catalog_ids(self):
        query = (
            "?brand_id={}&category_id={}&q=A168"
            .format(self.product["brand_id"], self.product["category_id"])
        )
        shared = self.client.get(
            "/api/v1/catalog/options?type=product&limit=50&"
            + query.lstrip("?")
        )
        receipt = self.client.get(
            "/api/v1/receipts/catalog?limit=50&" + query.lstrip("?")
        )

        self.assertEqual(shared.status_code, 200)
        self.assertEqual(receipt.status_code, 200)
        shared_product = shared.get_json()["data"][0]
        receipt_product = receipt.get_json()["data"][0]
        self.assertEqual(
            (
                shared_product["id"],
                shared_product["brand_id"],
                shared_product["category_id"],
            ),
            (
                receipt_product["id"],
                receipt_product["brand_id"],
                receipt_product["category_id"],
            ),
        )
        self.assertEqual(shared_product["id"], str(self.product["id"]))

        with mock.patch.object(
            web,
            "get_warehouse_items",
            return_value=[{
                "id": "receipt-only",
                "name": "Отдельный товар прихода",
                "brand": "Локальный бренд",
                "category": "Локальная категория",
            }],
        ):
            isolated = self.client.get(
                "/api/v1/receipts/catalog?q=receipt-only"
            ).get_json()
        self.assertEqual(isolated["data"], [])

    def test_new_catalog_values_are_immediately_searchable_in_all_sections(self):
        brand_response = self.client.post(
            "/api/v1/brands",
            json={"name": "Orient"},
        )
        self.assertEqual(brand_response.status_code, 201)
        brand = brand_response.get_json()["data"]

        category_response = self.client.post(
            "/api/v1/categories",
            json={
                "name": "Механические часы",
                "brand_id": brand["id"],
            },
        )
        self.assertEqual(category_response.status_code, 201)
        category = category_response.get_json()["data"]

        product = ExcelProductCatalog(CatalogDatabase(self.database_path)).create_product(
            name="Orient Bambino", article="FAC00009N0", brand_id=brand["id"],
            category_id=category["id"], stock=0,
        )

        brand_search = self.client.get(
            "/api/v1/catalog/options?type=brand&q=O"
        ).get_json()["data"]
        category_search = self.client.get(
            "/api/v1/catalog/options?type=category&q=М&brand_id={}"
            .format(brand["id"])
        ).get_json()["data"]
        product_search = self.client.get(
            "/api/v1/catalog/options?type=product&q=O"
            "&brand_id={}&category_id={}".format(
                brand["id"],
                category["id"],
            )
        ).get_json()["data"]

        self.assertIn(brand["id"], [item["id"] for item in brand_search])
        self.assertEqual(
            [item["id"] for item in category_search],
            [category["id"]],
        )
        self.assertEqual(
            (
                product_search[0]["id"],
                product_search[0]["brand_id"],
                product_search[0]["category_id"],
            ),
            (
                str(product["id"]),
                brand["id"],
                category["id"],
            ),
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/catalog/options?type=category"
                "&brand_id={}".format(self.product["brand_id"])
            ).status_code,
            200,
        )
        incompatible = self.client.get(
            "/api/v1/catalog/options?type=product"
            "&brand_id={}&category_id={}".format(
                self.product["brand_id"],
                category["id"],
            )
        ).get_json()["data"]
        self.assertEqual(incompatible, [])

    def test_new_brand_reuses_global_category_for_product_and_receipt(self):
        brand_response = self.client.post(
            "/api/v1/brands",
            json={"name": "Global Category Brand"},
        )
        self.assertEqual(brand_response.status_code, 201)
        brand = brand_response.get_json()["data"]

        category_options = self.client.get(
            "/api/v1/catalog/options?type=category&brand_id={}".format(
                brand["id"]
            )
        ).get_json()["data"]
        self.assertIn(
            self.product["category_id"],
            [item["id"] for item in category_options],
        )

        product = ExcelProductCatalog(CatalogDatabase(self.database_path)).create_product(
            name="Global Category Product", article="GLOBAL-CATEGORY-1",
            brand_id=brand["id"], category_id=self.product["category_id"], stock=0,
        )
        self.assertEqual(product["brand_id"], brand["id"])
        self.assertEqual(
            product["category_id"],
            self.product["category_id"],
        )

        from app.services.supplies import SupplyEngine
        engine=SupplyEngine(CatalogDatabase(self.database_path))
        linked=engine.resolve_bitrix({'external_product_id':'global-category','name':'Global Category Product','external_sku':'GLOBAL-CATEGORY-1','stock':47})
        receipt=engine.create('Global category',items=[{'product_id':linked['id'],'quantity':1}])
        receipt_position=receipt['items'][0]
        self.assertEqual(receipt_position['brand_id'],brand['id'])
        self.assertEqual(receipt_position['category_id'],self.product['category_id'])

    def test_product_editor_persists_global_category_without_stock_movement(self):
        brand = self.client.post(
            "/api/v1/brands",
            json={"name": "ArmA"},
        ).get_json()["data"]
        catalog = ExcelProductCatalog(CatalogDatabase(self.database_path))
        product = catalog.create_product(
            name="ARMA Nubuck Brown (22 мм)",
            article="arma-nubuck-brown-22-mm",
            brand_id=brand["id"],
            category="",
            stock=999,
        )
        with CatalogDatabase(self.database_path).connect() as connection:
            movements_before = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_manual_stock_operations "
                "WHERE product_id = ?",
                (product["id"],),
            ).fetchone()[0]

        saved = self.client.patch(
            "/api/v1/products/{}".format(product["id"]),
            json={
                "brand_id": brand["id"],
                "category_id": self.product["category_id"],
                "model": "",
            },
        )

        self.assertEqual(saved.status_code, 200)
        payload = saved.get_json()["data"]
        self.assertEqual(payload["brand_id"], brand["id"])
        self.assertEqual(payload["category_id"], self.product["category_id"])
        self.assertEqual(payload["stock"], 999)
        persisted = catalog.get_product(product["id"])
        self.assertEqual(persisted["category_id"], self.product["category_id"])
        self.assertIsNone(persisted["model_id"])
        with CatalogDatabase(self.database_path).connect() as connection:
            movements_after = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_manual_stock_operations "
                "WHERE product_id = ?",
                (product["id"],),
            ).fetchone()[0]
            linked = connection.execute(
                "SELECT 1 FROM erp_brand_categories "
                "WHERE brand_id = ? AND category_id = ?",
                (brand["id"], self.product["category_id"]),
            ).fetchone()
        self.assertEqual(movements_after, movements_before)
        self.assertIsNotNone(linked)

        invalid = self.client.patch(
            "/api/v1/products/{}".format(product["id"]),
            json={"category_id": 999999},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["code"], "CATEGORY_NOT_FOUND")
        self.assertEqual(catalog.get_product(product["id"])["category_id"],
                         self.product["category_id"])

    def test_created_category_can_be_selected_and_saved_by_canonical_id(self):
        brand = self.client.post(
            "/api/v1/brands", json={"name": "ArmA"}
        ).get_json()["data"]
        category_response = self.client.post(
            "/api/v1/categories",
            json={"brand_id": brand["id"], "name": "Аксессуары ArmA"},
        )
        self.assertEqual(category_response.status_code, 201)
        category = category_response.get_json()["data"]
        saved = self.client.patch(
            "/api/v1/products/{}".format(self.product["id"]),
            json={
                "brand_id": brand["id"],
                "category_id": category["id"],
            },
        )
        self.assertEqual(saved.status_code, 200)
        reread = self.client.get(
            "/api/v1/products/{}".format(self.product["id"])
        ).get_json()["data"]
        self.assertEqual(
            (reread["brand_id"], reread["category_id"]),
            (brand["id"], category["id"]),
        )

    def test_category_creation_is_global_and_normalized(self):
        brand = self.client.post(
            "/api/v1/brands",
            json={"name": "Duplicate Guard Brand"},
        ).get_json()["data"]

        duplicate_response = self.client.post(
            "/api/v1/categories",
            json={
                "brand_id": brand["id"],
                "name": "  нАРУЧНЫЕ ЧАСЫ  ",
            },
        )
        self.assertEqual(duplicate_response.status_code, 409)
        duplicate = duplicate_response.get_json()
        self.assertEqual(duplicate["code"], "CATEGORY_ALREADY_EXISTS")
        self.assertEqual(
            duplicate["fields"]["existing"]["id"],
            self.product["category_id"],
        )

        with CatalogDatabase(self.database_path).connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM erp_categories "
                "WHERE normalized_name = ?",
                ("наручные часы",),
            ).fetchone()[0]
        self.assertEqual(count, 1)

        repeated_spaces = self.client.post(
            "/api/v1/categories",
            json={
                "brand_id": brand["id"],
                "name": "Наручные     часы",
            },
        )
        self.assertEqual(repeated_spaces.status_code, 409)
        self.assertEqual(
            repeated_spaces.get_json()["fields"]["existing"]["id"],
            self.product["category_id"],
        )

    def test_brand_creation_normalizes_repeated_spaces(self):
        created = self.client.post(
            "/api/v1/brands",
            json={"name": "Maxim   Watch"},
        )
        duplicate = self.client.post(
            "/api/v1/brands",
            json={"name": "  maxim watch  "},
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["data"]["name"], "Maxim Watch")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(
            duplicate.get_json()["data"]["id"],
            created.get_json()["data"]["id"],
        )
        self.assertFalse(duplicate.get_json()["meta"]["created"])

    def test_category_options_prioritize_used_then_offer_global_values(self):
        other_brand = self.client.post(
            "/api/v1/brands",
            json={"name": "Other Category Brand"},
        ).get_json()["data"]
        other_category = self.client.post(
            "/api/v1/categories",
            json={
                "brand_id": other_brand["id"],
                "name": "Настольные часы",
            },
        ).get_json()["data"]

        options = self.client.get(
            "/api/v1/catalog/options?type=category&brand_id={}".format(
                self.product["brand_id"]
            )
        ).get_json()["data"]
        option_ids = [item["id"] for item in options]
        self.assertEqual(option_ids[0], self.product["category_id"])
        self.assertIn(other_category["id"], option_ids)
        self.assertTrue(options[0]["used_by_brand"])
        self.assertFalse(
            next(
                item for item in options
                if item["id"] == other_category["id"]
            )["used_by_brand"]
        )

    def test_visible_sections_use_base_layout_and_retired_ui_redirects_safely(self):
        redirects = {
            "/products": "/app/products",
            "/receipt": "/app/receipts",
            "/repair": "/app/repairs",
            "/stock-operations": "/app/products",
        }
        for path, target in redirects.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], target)

        adapter = web.app.url_map.bind("")
        aliases = {
            "/warehouse": "warehouse_page",
            "/app/products": "warehouse_page",
            "/sales": "sales_page",
            "/app/sales": "sales_page",
            "/receipts": "receipts_page",
            "/app/receipts": "receipts_page",
            "/app/repairs": "repair_page",
            "/settings": "settings_page",
            "/app/settings": "settings_page",
        }
        for path, endpoint in aliases.items():
            with self.subTest(path=path):
                self.assertEqual(adapter.match(path)[0], endpoint)


if __name__ == "__main__":
    unittest.main()
