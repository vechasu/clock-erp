import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from app.catalog_db import CatalogDatabase
from app.clients.wildberries_orders import (
    WildberriesReadOnlyClient,
    WildberriesReadOnlyError,
    mask_secret,
)
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.wildberries_matching import (
    build_matching_report,
    load_erp_product_index,
)


class Response:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.payload = {} if payload is None else payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class WildberriesTransportTest(unittest.TestCase):
    def client(self, session=None, **kwargs):
        return WildberriesReadOnlyClient(
            "test-token-value",
            session=session or mock.Mock(),
            sleep=mock.Mock(),
            **kwargs
        )

    def assert_code(self, status, code):
        session = mock.Mock()
        session.get.return_value = Response(status)
        with self.assertRaises(WildberriesReadOnlyError) as raised:
            self.client(session=session, max_retries=0).ping("common")
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("test-token-value", str(raised.exception))

    def test_missing_and_wrong_token_are_safe(self):
        client = WildberriesReadOnlyClient("", session=mock.Mock())
        with self.assertRaises(WildberriesReadOnlyError) as raised:
            client.ping("common")
        self.assertEqual(raised.exception.code, "WB_NOT_CONFIGURED")
        self.assert_code(401, "WB_UNAUTHORIZED")

    def test_403_is_distinct_and_safe(self):
        self.assert_code(403, "WB_FORBIDDEN")

    def test_429_retries_get_and_honors_retry_after(self):
        session = mock.Mock()
        session.get.side_effect = [
            Response(429, headers={"Retry-After": "0"}),
            Response(200, {"TS": "now", "Status": "OK"}),
        ]
        sleeper = mock.Mock()
        client = WildberriesReadOnlyClient(
            "test-token-value", session=session, sleep=sleeper, max_retries=1
        )
        self.assertEqual(client.ping("common")["Status"], "OK")
        self.assertEqual(session.get.call_count, 2)
        sleeper.assert_called_once_with(0.0)

    def test_5xx_retries_only_safe_get(self):
        session = mock.Mock()
        session.get.side_effect = [
            Response(503),
            Response(200, {"TS": "now", "Status": "OK"}),
        ]
        client = self.client(session=session, max_retries=1)
        self.assertEqual(client.ping("common")["Status"], "OK")
        self.assertEqual([item["method"] for item in client.request_audit], ["GET", "GET"])

    def test_exhausted_5xx_has_clear_error(self):
        self.assert_code(500, "WB_SERVER_ERROR")

    def test_timeout_is_retried_and_masked(self):
        session = mock.Mock()
        session.get.side_effect = requests.Timeout("test-token-value")
        client = self.client(session=session, max_retries=1)
        with self.assertRaises(WildberriesReadOnlyError) as raised:
            client.ping("common")
        self.assertEqual(raised.exception.code, "WB_TIMEOUT")
        self.assertNotIn("test-token-value", str(raised.exception))
        self.assertEqual(session.get.call_count, 2)

    def test_secret_is_masked_in_repr_errors_and_logs(self):
        logger = mock.Mock(spec=logging.Logger)
        session = mock.Mock()
        session.get.return_value = Response(503)
        client = self.client(
            session=session, max_retries=1, logger=logger
        )
        with self.assertRaises(WildberriesReadOnlyError):
            client.ping("common")
        rendered_logs = repr(logger.method_calls)
        self.assertNotIn("test-token-value", repr(client))
        self.assertNotIn("test-token-value", rendered_logs)
        self.assertEqual(mask_secret("test-token-value"), "[REDACTED]")

    def test_read_only_guarantee_blocks_all_write_verbs_before_network(self):
        session = mock.Mock()
        client = self.client(session=session)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                with self.assertRaises(WildberriesReadOnlyError) as raised:
                    client.request_json(method, "marketplace", "/api/v3/orders/new")
                self.assertEqual(raised.exception.code, "WB_READ_ONLY_GUARANTEE")
        session.get.assert_not_called()

    def test_post_only_content_and_stock_reads_are_explicitly_blocked(self):
        client = self.client()
        for callback in (
            client.content_cards_unavailable,
            client.marketplace_stocks_unavailable,
        ):
            with self.assertRaises(WildberriesReadOnlyError) as raised:
                callback()
            self.assertEqual(raised.exception.code, "WB_POST_READ_BLOCKED")

    def test_redirects_are_disabled_and_origin_is_exact(self):
        session = mock.Mock()
        session.get.return_value = Response(200, {"TS": "now", "Status": "OK"})
        self.client(session=session).ping("common")
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        with self.assertRaises(WildberriesReadOnlyError):
            WildberriesReadOnlyClient(
                "token", marketplace_base_url="https://example.test"
            )


class WildberriesMatchingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "catalog.db"
        database = CatalogDatabase(self.path)
        database.initialize()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches "
                "(id,file_sha256,source_filename,row_count,total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('wb-batch','wb-sha','wb.xlsx',0,0,0,0,'active','2026-01-01','2026-01-01')"
            )
        catalog = ExcelProductCatalog(database)
        self.matched = catalog.create_product(
            "Matched watch", article="WB-ONE", brand="Test", category="Watch", stock=1
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_report_has_matched_ambiguous_and_not_found_without_writes(self):
        index = load_erp_product_index(self.path)
        index["wb-dup"] = [
            {"id": "20", "name": "Duplicate A", "article": "WB-DUP", "barcode": ""},
            {"id": "21", "name": "Duplicate B", "article": "WB-DUP", "barcode": ""},
        ]
        report = build_matching_report(
            [
                {"nmID": 1, "vendorCode": "WB-ONE", "sizes": [{"discountedPrice": 99.9}]},
                {"nmID": 2, "vendorCode": "WB-DUP", "sizes": [{"discountedPrice": 120}]},
                {"nmID": 3, "vendorCode": "WB-MISSING", "sizes": []},
            ],
            [{"nmId": 1, "supplierArticle": "WB-ONE", "quantity": 4}],
            [],
            index,
        )
        self.assertEqual([row["status"] for row in report], ["matched", "ambiguous", "not_found"])
        self.assertEqual(report[0]["erp_product_id"], str(self.matched["id"]))
        self.assertEqual(report[0]["stock"], 4)
        self.assertEqual(report[0]["price"], 99.9)


if __name__ == "__main__":
    unittest.main()
