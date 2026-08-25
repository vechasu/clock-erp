import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from app import web
from app.domain_schema_migrations import apply_domain_migrations
from app.catalog_db import CatalogDatabase
from app.clients.wildberries_orders import (
    WildberriesOrdersReadOnlyClient,
    WildberriesReadOnlyError,
)
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.shared_catalog import SharedCatalog
from app.services.wildberries_orders import (
    normalize_wildberries_order,
    synchronize_wildberries_orders,
)


def raw_order(identifier, order_uid="buyer-order", status="new", article="WB-ARTICLE"):
    return {
        "id": identifier,
        "orderUid": order_uid,
        "rid": "rid-{}".format(identifier),
        "nmId": 123456,
        "chrtId": 654321,
        "article": article,
        "skus": ["4600000000001"],
        "warehouseId": 777,
        "createdAt": "2026-08-24T10:30:00Z",
        "deliveryType": "fbs",
        "convertedFinalPrice": 129900,
        "supplierStatus": status,
        "wbStatus": "waiting",
    }


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class WildberriesClientTest(unittest.TestCase):
    def test_read_only_endpoint_auth_and_schema(self):
        session = mock.Mock()
        session.get.return_value = FakeResponse(200, {"orders": [raw_order(10)]})
        client = WildberriesOrdersReadOnlyClient("secret", session=session)

        self.assertEqual(client.get_new_orders()[0]["id"], 10)
        session.get.assert_called_once_with(
            "https://marketplace-api.wildberries.ru/api/v3/orders/new",
            headers={"Authorization": "secret", "Accept": "application/json"},
            timeout=(3.05, 15),
        )

    def test_absent_token_and_http_failures_are_safe(self):
        cases = (
            (None, "WB_NOT_CONFIGURED"),
            (401, "WB_UNAUTHORIZED"),
            (403, "WB_FORBIDDEN"),
            (429, "WB_RATE_LIMITED"),
            (500, "WB_SERVER_ERROR"),
        )
        for status, code in cases:
            with self.subTest(code=code):
                session = mock.Mock()
                session.get.return_value = FakeResponse(status, {})
                client = WildberriesOrdersReadOnlyClient(
                    "" if status is None else "secret", session=session
                )
                with self.assertRaises(WildberriesReadOnlyError) as raised:
                    client.get_new_orders()
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn("secret", str(raised.exception))

    def test_timeout_and_invalid_response_are_safe(self):
        session = mock.Mock()
        session.get.side_effect = requests.Timeout("token=secret")
        with self.assertRaises(WildberriesReadOnlyError) as raised:
            WildberriesOrdersReadOnlyClient("secret", session=session).get_new_orders()
        self.assertEqual(raised.exception.code, "WB_TIMEOUT")
        self.assertNotIn("secret", str(raised.exception))

        for payload in ([], {}, {"orders": {}}, ValueError("bad json")):
            session = mock.Mock()
            session.get.return_value = FakeResponse(200, payload)
            with self.assertRaises(WildberriesReadOnlyError) as raised:
                WildberriesOrdersReadOnlyClient("secret", session=session).get_new_orders()
            self.assertEqual(raised.exception.code, "WB_INVALID_RESPONSE")


class WildberriesStorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = OrdersSnapshotStore(Path(self.temporary.name) / "orders.db")
        apply_domain_migrations(self.store.path, "orders", "test")
        self.tictactoy = {
            "id": "100", "number": "TT-100", "status": "A",
            "customer": "Иван", "created_at": "2026-08-23T10:00:00Z",
            "products": [],
        }
        self.store.replace([self.tictactoy], 1000)

    def tearDown(self):
        self.temporary.cleanup()

    def sync(self, rows):
        client = mock.Mock()
        client.get_new_orders.return_value = rows
        return synchronize_wildberries_orders(
            client, self.store, synced_at="2026-08-24T11:00:00+00:00"
        )

    def test_import_is_idempotent_and_assembly_orders_are_not_grouped(self):
        first = self.sync([raw_order(200, "same"), raw_order(201, "same")])
        second = self.sync([raw_order(200, "same"), raw_order(201, "same")])

        self.assertEqual(first, {"received": 2, "added": 2, "updated": 0, "errors": 0})
        self.assertEqual(second, {"received": 2, "added": 0, "updated": 2, "errors": 0})
        self.assertEqual(self.store.count(), 3)
        self.assertEqual(self.store.get("wb:200")["order_uid"], "same")
        self.assertEqual(self.store.get("wb:201")["order_uid"], "same")
        self.assertIsNotNone(self.store.get("100"))

    def test_status_updates_and_bitrix_refresh_preserves_wb(self):
        self.sync([raw_order(200, status="new")])
        self.sync([raw_order(200, status="confirm")])
        self.assertEqual(self.store.get("wb:200")["supplier_status"], "confirm")

        self.store.replace([dict(self.tictactoy, customer="Мария")], 1001)
        self.assertEqual(self.store.get("wb:200")["wb_order_id"], "200")
        self.assertEqual(self.store.get("100")["customer"], "Мария")

    def test_source_search_status_and_pagination_are_server_side(self):
        self.sync([raw_order(200 + index, article="WB-{:02d}".format(index)) for index in range(25)])
        self.assertEqual(self.store.query({"source": "all"})["total"], 26)
        self.assertEqual(self.store.query({"source": "tictactoy"})["total"], 1)
        self.assertEqual(self.store.query({"source": "wildberries"})["total"], 25)
        combined = self.store.query({
            "source": "wildberries", "q": "WB-07", "page": 1, "page_size": 20,
        })
        self.assertEqual(combined["total"], 1)
        paged = self.store.query({"source": "wildberries", "page": 2, "page_size": 20})
        self.assertEqual(paged["total"], 25)
        self.assertEqual(len(paged["rows"]), 5)

    def test_unmapped_order_is_visible_and_sync_does_not_touch_catalog_or_mapping(self):
        catalog = CatalogDatabase(Path(self.temporary.name) / "catalog.db")
        catalog.initialize()
        before_stock = catalog.connect().execute(
            "SELECT COALESCE(SUM(stock), 0) FROM catalog_excel_products"
        ).fetchone()[0]
        self.sync([raw_order(200)])
        order = self.store.get("wb:200")
        self.assertEqual(order["products"][0]["product_id"], "")
        self.assertEqual(order["item_units"], 1)
        after_stock = catalog.connect().execute(
            "SELECT COALESCE(SUM(stock), 0) FROM catalog_excel_products"
        ).fetchone()[0]
        self.assertEqual(before_stock, after_stock)

    def test_manual_mapping_is_not_changed_by_resync(self):
        catalog = CatalogDatabase(Path(self.temporary.name) / "catalog.db")
        catalog.initialize()
        with catalog.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id, file_sha256, source_filename, "
                "row_count, total_stock, positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('batch-wb', 'sha-wb', 'wb.xlsx', 0, 0, 0, 0, 'active', ?, ?)",
                ("2026-08-24T10:00:00+00:00", "2026-08-24T10:00:00+00:00"),
            )
        product = ExcelProductCatalog(catalog).create_product(
            "WB Watch", article="WB-ARTICLE", brand="Test", category="Часы", stock=3
        )
        web.save_order_product_mapping("wb:200", "200", product["id"], catalog)
        self.sync([raw_order(200)])
        self.assertEqual(
            web.load_order_product_mappings("wb:200", catalog)["line:200"]["product_id"],
            str(product["id"]),
        )

    def test_unique_article_automatically_maps_wb_product(self):
        catalog = CatalogDatabase(Path(self.temporary.name) / "auto-catalog.db")
        catalog.initialize()
        with catalog.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id, file_sha256, source_filename, "
                "row_count, total_stock, positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('batch-auto', 'sha-auto', 'auto.xlsx', 0, 0, 0, 0, 'active', ?, ?)",
                ("2026-08-24T10:00:00+00:00", "2026-08-24T10:00:00+00:00"),
            )
        product = ExcelProductCatalog(catalog).create_product(
            "WB Watch", article="WB-ARTICLE", brand="Test", category="Часы", stock=3
        )
        order = normalize_wildberries_order(raw_order(200))
        context = web.build_order_product_mapping_context(
            order["products"], catalog=SharedCatalog(catalog)
        )
        mapped = context["line:200"]
        self.assertEqual(mapped["state"], "mapped")
        self.assertEqual(str(mapped["product"]["id"]), str(product["id"]))
        self.assertEqual(mapped["mapping_method"], "wb_barcode_or_article")

    def test_normalized_payload_contains_required_identifiers_and_no_sale_permission(self):
        order = normalize_wildberries_order(raw_order(200))
        for key in (
            "wb_order_id", "order_uid", "rid", "nm_id", "chrt_id",
            "article", "skus", "warehouse_id", "created_at", "delivery_type",
            "supplier_status", "wb_status",
        ):
            self.assertIn(key, order)
        state = web.build_order_sale_state(order, {})
        self.assertFalse(state["can_create_sale"])


class WildberriesRoutesTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "orders.db"
        apply_domain_migrations(self.path, "orders", "test")
        web.app.config.update(TESTING=True, AUTH_TESTING=False, ORDERS_SNAPSHOT_TESTING=True)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temporary.cleanup()

    def test_missing_token_does_not_break_tictactoy_orders(self):
        tictactoy = [{"id": "100", "number": "TT-100", "status": "A", "products": []}]
        with (
            mock.patch.dict("os.environ", {"ORDERS_DATABASE_PATH": str(self.path)}, clear=False),
            mock.patch.object(web, "get_orders", return_value=tictactoy),
            mock.patch.dict(web.ORDERS_CACHE, {"items": tictactoy, "loaded_at": 1000, "error": ""}, clear=True),
            mock.patch.object(web, "bulk_conducted_order_sales", return_value={}),
            mock.patch.dict("os.environ", {"WB_API_TOKEN": ""}, clear=False),
        ):
            sync_response = self.client.post("/api/orders/wildberries/sync")
            page_response = self.client.get("/app/orders?source=tictactoy")
        self.assertEqual(sync_response.status_code, 400)
        self.assertEqual(sync_response.get_json()["error"]["code"], "WB_NOT_CONFIGURED")
        self.assertEqual(page_response.status_code, 200)
        self.assertIn("TT-100", page_response.get_data(as_text=True))

    def test_wb_filter_and_read_only_card_render(self):
        store = OrdersSnapshotStore(self.path)
        store.upsert_wildberries([normalize_wildberries_order(raw_order(200))])
        catalog_path = Path(self.temporary.name) / "catalog.db"
        with (
            mock.patch.dict("os.environ", {
                "ORDERS_DATABASE_PATH": str(self.path),
                "CATALOG_DATABASE_PATH": str(catalog_path),
            }, clear=False),
            mock.patch.object(web, "get_orders", return_value=[]),
            mock.patch.dict(web.ORDERS_CACHE, {"items": [], "loaded_at": 0, "error": ""}, clear=True),
            mock.patch.object(web, "schedule_orders_refresh"),
            mock.patch.object(web, "schedule_order_item_unit_backfill"),
        ):
            list_response = self.client.get("/app/orders?source=wildberries")
            card_response = self.client.get("/order/wildberries/200?source=wildberries")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(card_response.status_code, 200)
        html = card_response.get_data(as_text=True)
        for value in ("Wildberries FBS", "Только чтение", "WB-ARTICLE", "4600000000001", "nmId: 123456"):
            self.assertIn(value, html)
        self.assertNotIn("Провести продажу", html)
        self.assertNotIn("Открыть в Bitrix", html)


if __name__ == "__main__":
    unittest.main()
