import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import requests

from app.clients.bitrix_orders import (
    BitrixOrdersReadOnlyClient,
    BitrixReadOnlyError,
    match_items,
    normalize_order,
)
from scripts import bitrix_orders_dry_run


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class BitrixOrdersReadOnlyClientTest(unittest.TestCase):
    def make_client(self, **kwargs):
        return BitrixOrdersReadOnlyClient(
            orders_url="https://example.test/orders",
            order_url="https://example.test/order",
            **kwargs,
        )

    def test_endpoints_must_be_configured_explicitly(self):
        with self.assertRaisesRegex(BitrixReadOnlyError, "configured explicitly"):
            BitrixOrdersReadOnlyClient()

        with self.assertRaisesRegex(BitrixReadOnlyError, "must use HTTPS"):
            BitrixOrdersReadOnlyClient(
                orders_url="http://example.test/orders",
                order_url="https://example.test/order",
            )

    def test_fetch_uses_get_only_and_limits_detail_requests(self):
        session = FakeSession([
            FakeResponse(payload={"orders": [{"id": 1}, {"id": 2}]}),
            FakeResponse(payload={"order": {"id": 1}}),
        ])
        client = self.make_client(session=session, max_retries=0)

        result = client.get_latest_orders(limit=1)

        self.assertEqual(result["orders"], [{"id": 1}])
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(all(call[1]["timeout"] == (3.05, 15) for call in session.calls))

    def test_optional_token_is_sent_only_as_authorization_header(self):
        session = FakeSession([FakeResponse(payload={"orders": []})])
        client = self.make_client(
            token="placeholder-read-only-token", session=session, max_retries=0
        )

        client.get_latest_orders(limit=1)

        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://example.test/orders")
        self.assertNotIn("placeholder-read-only-token", url)
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer placeholder-read-only-token",
        )

    def test_timeout_error_never_exposes_configured_url(self):
        secret_url = "https://example.test/private-password?token=secret-placeholder"
        client = BitrixOrdersReadOnlyClient(
            orders_url=secret_url,
            order_url="https://example.test/order",
            session=FakeSession([requests.Timeout("network detail")]),
            max_retries=0,
        )

        with self.assertRaises(BitrixReadOnlyError) as raised:
            client.get_latest_orders(limit=1)

        message = str(raised.exception)
        self.assertNotIn("password", message)
        self.assertNotIn("private-token", message)
        self.assertNotIn("secret", message)
        self.assertIn("Timeout", message)

    def test_transient_status_is_retried_with_fake_session(self):
        session = FakeSession([
            FakeResponse(status_code=503, headers={"Retry-After": "0"}),
            FakeResponse(payload={"orders": []}),
        ])
        client = self.make_client(session=session, max_retries=1)

        with mock.patch("app.clients.bitrix_orders.time.sleep") as sleep:
            result = client.get_latest_orders(limit=1)

        self.assertEqual(result["orders"], [])
        self.assertEqual(len(session.calls), 2)
        sleep.assert_called_once_with(0)

    def test_invalid_json_and_shape_are_safe_errors(self):
        invalid_json = self.make_client(
            session=FakeSession([FakeResponse(json_error=ValueError("raw body"))]),
            max_retries=0,
        )
        invalid_shape = self.make_client(
            session=FakeSession([FakeResponse(payload=[])]),
            max_retries=0,
        )

        with self.assertRaisesRegex(BitrixReadOnlyError, "non-JSON"):
            invalid_json.get_latest_orders(limit=1)
        with self.assertRaisesRegex(BitrixReadOnlyError, "unexpected JSON structure"):
            invalid_shape.get_latest_orders(limit=1)


class BitrixOrdersDryRunSafetyTest(unittest.TestCase):
    def test_catalog_matching_uses_one_mocked_get_request(self):
        response = FakeResponse(payload={"rows": [{"id": "product-1"}]})
        with mock.patch.dict("os.environ", {"MOYSKLAD_TOKEN": "placeholder"}), mock.patch.object(
            bitrix_orders_dry_run.requests, "get", return_value=response
        ) as request_get:
            rows, warning = bitrix_orders_dry_run.get_catalog()

        self.assertEqual(rows, [{"id": "product-1"}])
        self.assertIsNone(warning)
        request_get.assert_called_once()
        self.assertEqual(request_get.call_args.kwargs["params"], {"limit": 1000})

    def test_main_requires_explicit_network_permission(self):
        with mock.patch("sys.argv", ["bitrix_orders_dry_run.py"]), mock.patch.object(
            bitrix_orders_dry_run, "build_report"
        ) as build_report:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bitrix_orders_dry_run.main()

        build_report.assert_not_called()

    def test_build_report_does_not_create_or_change_files(self):
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def get_latest_orders(self, limit):
                return {
                    "orders": [{"id": "1", "products": []}],
                    "requested_limit": limit,
                    "server_list_count": 1,
                    "server_honored_limit": True,
                    "pagination": {},
                    "request_count": 2,
                }

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            before = list(root.rglob("*"))
            with mock.patch.object(bitrix_orders_dry_run, "PROJECT_ROOT", root), mock.patch.object(
                bitrix_orders_dry_run, "BitrixOrdersReadOnlyClient", FakeClient
            ):
                report = bitrix_orders_dry_run.build_report(1, include_catalog=False)
            after = list(root.rglob("*"))

        self.assertEqual(before, after)
        self.assertEqual(report["writes_performed"], 0)
        self.assertEqual(report["inventory_changes_performed"], 0)


class BitrixOrderNormalizationTest(unittest.TestCase):
    def test_price_types_are_kept_separate(self):
        order = normalize_order({
            "id": 7,
            "price": "180.00",
            "currency": "RUB",
            "products": [{
                "id": 11,
                "name": "Test",
                "quantity": "2",
                "base_price": "100",
                "price": "90",
                "purchase_price": "50",
            }],
        })
        item = order["items"][0]
        self.assertEqual(item["original_unit_price"], 100.0)
        self.assertEqual(item["sale_unit_price"], 90.0)
        self.assertEqual(item["discount_per_unit"], 10.0)
        self.assertEqual(item["purchase_unit_price"], 50.0)
        self.assertEqual(item["line_total"], 180.0)
        self.assertEqual(item["line_total_source"], "computed_sale_price_times_quantity")

    def test_missing_purchase_price_is_not_inferred(self):
        order = normalize_order({
            "products": [{"name": "Test", "quantity": 1, "price": 90}],
        })
        self.assertIsNone(order["items"][0]["purchase_unit_price"])


class BitrixProductMatchingTest(unittest.TestCase):
    def setUp(self):
        self.products = [
            {
                "id": "1", "externalCode": "xml-1",
                "article": "sku-1", "name": "Watch",
            },
            {
                "id": "2", "externalCode": "xml-2",
                "article": "sku-2", "name": "Clock",
            },
        ]

    def test_matching_priority_is_xml_then_sku_then_name(self):
        rows = match_items([
            {
                "bitrix_product_id": "", "xml_id": "xml-1",
                "sku": "sku-2", "name": "Clock",
            },
            {
                "bitrix_product_id": "", "xml_id": "",
                "sku": "sku-2", "name": "Watch",
            },
            {
                "bitrix_product_id": "", "xml_id": "",
                "sku": "", "name": "Watch",
            },
        ], self.products)
        self.assertEqual(
            [row["match_method"] for row in rows],
            ["xml_id", "sku", "exact_name"],
        )
        self.assertEqual(
            [row["moysklad_product_id"] for row in rows], ["1", "2", "1"]
        )

    def test_ambiguous_name_requires_mapping(self):
        products = self.products + [{"id": "3", "name": "Watch"}]
        row = match_items([{
            "bitrix_product_id": "", "xml_id": "", "sku": "", "name": "Watch"
        }], products)[0]
        self.assertEqual(row["match_status"], "requires_mapping")
        self.assertEqual(row["match_method"], "ambiguous_exact_name")


if __name__ == "__main__":
    unittest.main()
