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
        receipt_response = self.client.post(
            "/api/v1/receipts",
            json=self.receipt_payload(),
        )
        self.assertEqual(receipt_response.status_code, 201)
        receipt = receipt_response.get_json()["data"]
        self.assertEqual(self.stock(), 10)
        self.remote.create_product.assert_called_once()
        remote_positions = (
            self.remote.create_stock_enter_many.call_args[1]["positions"]
        )
        self.assertEqual(
            remote_positions[0]["product_id"],
            "ms-casio-a168",
        )

        repeated = self.client.post(
            "/api/v1/receipts",
            json=self.receipt_payload(),
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.get_json()["data"]["id"], receipt["id"])
        self.remote.create_stock_enter_many.assert_called_once()
        self.assertEqual(self.stock(), 10)

        sale_response = self.client.post(
            "/api/v1/sales",
            json={
                "created_at": "2026-07-30",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "quantity": 3,
                "unit_price": 1000,
                "order_number": "ORDER-API-1",
            },
        )
        self.assertEqual(sale_response.status_code, 201)
        sale = sale_response.get_json()["data"]
        self.assertEqual(self.stock(), 7)

        edited = self.client.patch(
            "/api/v1/sales/{}".format(sale["id"]),
            json={
                "created_at": "2026-07-30",
                "source": "Tictactoy",
                "product_id": str(self.product["id"]),
                "brand_id": self.product["brand_id"],
                "category_id": self.product["category_id"],
                "quantity": 2,
                "unit_price": 1000,
                "order_number": "ORDER-API-1",
            },
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(self.stock(), 8)

        cancelled = self.client.delete(
            "/api/v1/sales/{}".format(sale["id"])
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.stock(), 10)

        changed_payload = self.receipt_payload(quantity=6)
        changed_payload["idempotency_key"] = "receipt-update-once"
        changed_receipt = self.client.patch(
            "/api/v1/receipts/{}".format(receipt["id"]),
            json=changed_payload,
        )
        self.assertEqual(changed_receipt.status_code, 200)
        self.assertEqual(self.stock(), 6)

        movements = self.client.get(
            "/api/v1/products/{}/movements".format(self.product["id"])
        )
        self.assertEqual(movements.status_code, 200)
        movement_rows = movements.get_json()["data"]
        self.assertTrue(
            any(item.get("sale_id") == sale["id"] for item in movement_rows)
        )
        self.assertTrue(
            any(
                item.get("receipt_id") == receipt["id"]
                for item in movement_rows
            )
        )
        self.assertEqual(
            {item["type"] for item in movement_rows},
            {"receipt", "sale", "manual_adjustment", "cancellation"},
        )

        renamed = self.client.patch(
            "/api/v1/brands/{}".format(self.product["brand_id"]),
            json={"name": "Casio Japan"},
        )
        self.assertEqual(renamed.status_code, 200)
        receipt_listing = self.client.get("/api/v1/receipts").get_json()
        self.assertEqual(receipt_listing["data"][0]["brand"], "Casio Japan")

    def test_unmapped_bitrix_product_is_created_in_moysklad_before_receipt(self):
        now = "2026-07-30T12:00:00+00:00"
        with CatalogDatabase(self.database_path).connect() as connection:
            connection.execute(
                "INSERT INTO catalog_products ("
                "name, article, barcode, brand, active, external_source, "
                "external_product_id, external_xml_id, payload_hash, "
                "normalized_payload_json, created_at, updated_at, "
                "first_synced_at, last_synced_at"
                ") VALUES (?, ?, ?, ?, 1, 'bitrix', ?, ?, ?, '{}', ?, ?, ?, ?)",
                (
                    "Under Pressure II Orange",
                    "under-pressure-ii-orange",
                    "",
                    "666 Barcelona",
                    "743",
                    "743",
                    "b" * 64,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            bitrix_product_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]

        product = ExcelProductCatalog(
            CatalogDatabase(self.database_path)
        ).create_product(
            name="Under Pressure II Orange",
            brand="666 Barcelona",
            category="Наручные часы",
            stock=0,
        )
        with CatalogDatabase(self.database_path).connect() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET "
                "source_key = 'bitrix:743', "
                "bitrix_catalog_product_id = ?, "
                "bitrix_external_product_id = '743' "
                "WHERE id = ?",
                (bitrix_product_id, product["id"]),
            )

        self.remote.create_product.return_value = {
            "id": "ms-under-pressure-orange",
        }
        response = self.client.post(
            "/api/v1/receipts",
            json={
                "receipt_date": "2026-07-30",
                "note": "",
                "idempotency_key": "receipt-bitrix-unmapped",
                "positions": [{
                    "product_id": str(product["id"]),
                    "brand_id": product["brand_id"],
                    "category_id": product["category_id"],
                    "quantity": 55,
                    "purchase_price": 0,
                }],
            },
        )

        self.assertEqual(response.status_code, 201)
        self.remote.find_product_by_code.assert_called_once_with(
            "VECHASU-{}".format(product["id"])
        )
        self.remote.create_product.assert_called_once_with(
            name="Under Pressure II Orange",
            code="VECHASU-{}".format(product["id"]),
            article=None,
        )
        remote_positions = (
            self.remote.create_stock_enter_many.call_args.kwargs["positions"]
        )
        self.assertEqual(
            remote_positions[0]["product_id"],
            "ms-under-pressure-orange",
        )
        linked = web.SharedCatalog(
            CatalogDatabase(self.database_path)
        ).get_product(product["id"])
        self.assertEqual(
            linked["moysklad_product_id"],
            "ms-under-pressure-orange",
        )
        self.assertEqual(
            ExcelProductCatalog(
                CatalogDatabase(self.database_path)
            ).get_product(product["id"])["stock"],
            55,
        )

    def test_product_duplicate_returns_existing_card(self):
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

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["code"], "PRODUCT_ALREADY_EXISTS")
        self.assertEqual(
            payload["fields"]["existing"]["id"],
            str(self.product["id"]),
        )

    def test_multipart_receipt_without_photo_updates_stock_and_history(self):
        response = self.multipart_receipt(
            quantity=1,
            note="",
            idempotency_key="receipt-no-photo",
        )

        self.assertEqual(response.status_code, 201)
        receipt = response.get_json()["data"]
        self.assertEqual(receipt["note"], "")
        self.assertEqual(self.stock(), 1)
        self.assertEqual(len(web.load_receipts()), 1)
        movements = self.client.get(
            "/api/v1/products/{}/movements".format(self.product["id"])
        ).get_json()["data"]
        self.assertTrue(
            any(
                item.get("receipt_id") == receipt["id"]
                and item["diff"] == 1
                for item in movements
            )
        )
        self.remote.upload_product_image.assert_not_called()

    def test_previous_data_url_photo_payload_remains_accepted(self):
        payload = self.receipt_payload(quantity=1)
        payload["idempotency_key"] = "receipt-data-url"
        payload["product_image"] = {
            "name": "watch.png",
            "data_url": (
                "data:image/png;base64,"
                + base64.b64encode(
                    b"\x89PNG\r\n\x1a\nlegacy-data-url"
                ).decode("ascii")
            ),
        }

        response = self.client.post(
            "/api/v1/receipts",
            json=payload,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stock(), 1)
        self.remote.upload_product_image.assert_called_once()

    def test_document_creation_and_image_check_start_in_parallel(self):
        barrier = threading.Barrier(2)
        document = {
            "id": "enter-parallel",
            "name": "ПР-PARALLEL",
            "meta": {"uuidHref": "https://example.test/enter-parallel"},
        }

        def create_document(**_kwargs):
            barrier.wait(timeout=2)
            return document

        def inspect_images(_product_id):
            barrier.wait(timeout=2)
            return False

        self.remote.create_stock_enter_many.side_effect = create_document
        self.remote.product_has_images.side_effect = inspect_images
        response = self.multipart_receipt(
            image=b"\x89PNG\r\n\x1a\nparallel",
            idempotency_key="receipt-parallel-remote",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stock(), 1)
        self.remote.upload_product_image.assert_called_once()

    def test_receipt_request_initializes_shared_catalog_schema_once(self):
        original_initialize_schema = CatalogDatabase._initialize_schema
        initialize_calls = []

        def tracked_initialize_schema(database):
            initialize_calls.append(database)
            return original_initialize_schema(database)

        with mock.patch.object(
            CatalogDatabase,
            "_initialize_schema",
            tracked_initialize_schema,
        ):
            response = self.multipart_receipt(
                idempotency_key="receipt-one-catalog-initialize",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(initialize_calls), 1)
        self.assertTrue(initialize_calls[0].cache_initialization)

    def test_multipart_receipt_saves_png_jpeg_and_comment(self):
        fixtures = (
            (
                b"\x89PNG\r\n\x1a\n" + b"png-data",
                "watch.png",
                "image/png",
            ),
            (
                b"\xff\xd8\xff" + b"jpeg-data",
                "watch.jpg",
                "image/jpeg",
            ),
        )

        for index, (content, filename, mimetype) in enumerate(fixtures):
            response = self.multipart_receipt(
                quantity=2,
                note="Фото и комментарий {}".format(index),
                image=content,
                filename=filename,
                mimetype=mimetype,
                idempotency_key="receipt-photo-{}".format(index),
            )
            self.assertEqual(response.status_code, 201)
            payload = response.get_json()
            self.assertEqual(
                payload["data"]["note"],
                "Фото и комментарий {}".format(index),
            )
            self.assertEqual(
                payload["meta"]["image_message"],
                "Фото товара добавлено.",
            )

        self.assertEqual(self.stock(), 4)
        self.assertEqual(
            [call.args[1] for call in self.remote.upload_product_image.call_args_list],
            ["watch.png", "watch.jpg"],
        )
        self.assertTrue(
            self.remote.upload_product_image.call_args_list[0].args[2]
            .startswith(b"\x89PNG\r\n\x1a\n")
        )
        self.assertTrue(
            self.remote.upload_product_image.call_args_list[1].args[2]
            .startswith(b"\xff\xd8\xff")
        )

    def test_create_next_mode_is_idempotent_and_does_not_double_stock(self):
        first = self.multipart_receipt(
            quantity=3,
            note="Следующий приход",
            image=b"\x89PNG\r\n\x1a\nnext",
            idempotency_key="receipt-create-next",
            submit_mode="create_next",
        )
        repeated = self.multipart_receipt(
            quantity=3,
            note="Следующий приход",
            image=b"\x89PNG\r\n\x1a\nnext",
            idempotency_key="receipt-create-next",
            submit_mode="create_next",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(
            first.get_json()["data"]["id"],
            repeated.get_json()["data"]["id"],
        )
        self.assertEqual(self.stock(), 3)
        self.remote.create_stock_enter_many.assert_called_once()
        self.remote.upload_product_image.assert_called_once()
        self.assertEqual(len(web.load_receipts()), 1)

    def test_invalid_multipart_inputs_leave_no_partial_receipt(self):
        response = self.multipart_receipt(
            quantity=2,
            note="Недопустимый файл",
            image=b"not-an-image",
            filename="watch.txt",
            mimetype="text/plain",
            idempotency_key="receipt-invalid-image",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["message"],
            "Недопустимый формат изображения. Поддерживаются JPEG и PNG.",
        )
        oversized = self.multipart_receipt(
            quantity=2,
            image=(
                b"\x89PNG\r\n\x1a\n"
                + b"x" * web.PRODUCT_IMAGE_MAX_BYTES
            ),
            idempotency_key="receipt-oversized-image",
        )
        missing_quantity = self.multipart_receipt(
            quantity=0,
            idempotency_key="receipt-missing-quantity",
        )

        self.assertEqual(oversized.status_code, 422)
        self.assertEqual(
            oversized.get_json()["message"],
            "Файл слишком большой. Максимальный размер — 3 МБ.",
        )
        self.assertEqual(missing_quantity.status_code, 422)
        self.assertIn(
            "Количество",
            missing_quantity.get_json()["message"],
        )
        self.assertEqual(self.stock(), 0)
        self.assertEqual(web.load_receipts(), [])
        self.remote.create_stock_enter_many.assert_not_called()

    def test_local_persistence_failure_rolls_back_stock_files_and_remote(self):
        with mock.patch.object(
            web,
            "save_stock_operations",
            side_effect=[RuntimeError("forced persistence failure"), None],
        ):
            response = self.multipart_receipt(
                quantity=5,
                note="Транзакционный тест",
                idempotency_key="receipt-persistence-failure",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.get_json()["code"],
            "RECEIPT_PERSISTENCE_FAILED",
        )
        self.assertEqual(self.stock(), 0)
        self.assertEqual(web.load_receipts(), [])
        self.assertEqual(web.load_stock_operations(), [])
        self.assertIsNone(
            web.ReceiptInventory().get_receipt_by_idempotency(
                "receipt-persistence-failure"
            )
        )
        self.remote.delete_stock_enter.assert_called_once_with("enter-1")

    def test_image_upload_failure_rolls_back_before_local_persistence(self):
        self.remote.upload_product_image.return_value = False
        response = self.multipart_receipt(
            quantity=2,
            note="Ошибка фото",
            image=b"\x89PNG\r\n\x1a\nbroken-remote",
            idempotency_key="receipt-image-failure",
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json()["code"],
            "PRODUCT_IMAGE_UPLOAD_FAILED",
        )
        self.assertEqual(self.stock(), 0)
        self.assertEqual(web.load_receipts(), [])
        self.remote.delete_stock_enter.assert_called_once_with("enter-1")

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

        product_response = self.client.post(
            "/api/v1/products",
            json={
                "name": "Orient Bambino",
                "article": "FAC00009N0",
                "brand_id": brand["id"],
                "category_id": category["id"],
                "stock": 0,
            },
        )
        self.assertEqual(product_response.status_code, 201)
        product = product_response.get_json()["data"]

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

    def test_visible_sections_keep_the_approved_legacy_entrypoints(self):
        products = self.client.get("/products")
        sales = self.client.get("/sales")
        receipts = self.client.get("/receipts")

        self.assertEqual(products.status_code, 302)
        self.assertIn("/warehouse", products.headers["Location"])
        self.assertEqual(sales.status_code, 200)
        self.assertIn(b'class="sales-page"', sales.data)
        self.assertEqual(receipts.status_code, 200)
        self.assertIn(b'class="receipts-page"', receipts.data)
        self.assertNotIn(b'<div id="root"></div>', sales.data)
        self.assertNotIn(b'<div id="root"></div>', receipts.data)
        for response in (sales, receipts):
            self.assertIn(
                b"/static/js/catalog-combobox.js",
                response.data,
            )
            self.assertIn(
                b'data-shared-catalog-kind="brand"',
                response.data,
            )
            self.assertIn(
                b'data-shared-catalog-kind="category"',
                response.data,
            )
            self.assertIn(
                b'data-shared-catalog-kind="product"',
                response.data,
            )

        warehouse = self.client.get("/warehouse?open_add=1")
        self.assertEqual(warehouse.status_code, 200)
        self.assertIn(b'class="warehouse-page"', warehouse.data)
        self.assertIn(
            b'data-shared-catalog-kind="brand"',
            warehouse.data,
        )
        self.assertIn(
            b'data-shared-catalog-kind="category"',
            warehouse.data,
        )

        repair = self.client.get("/repair")
        self.assertEqual(repair.status_code, 200)
        self.assertNotIn(b'<div id="root"></div>', repair.data)


if __name__ == "__main__":
    unittest.main()
