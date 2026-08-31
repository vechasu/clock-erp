import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

from app.clients.wildberries_orders import WildberriesReadOnlyError


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "wb_fbs_sales_smoke.py"
SPEC = importlib.util.spec_from_file_location("wb_fbs_sales_smoke", str(SCRIPT_PATH))
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class FakeClient:
    def __init__(self, token, orders=None, error=None):
        self.token = token
        self.orders = [] if orders is None else orders
        self.error = error
        self.request_audit = []

    def get_new_orders(self):
        self.request_audit.append({
            "method": "GET",
            "service": "marketplace",
            "path": "/api/v3/orders/new",
        })
        if self.error:
            raise self.error
        return self.orders


class WildberriesFbsSalesSmokeContractTest(unittest.TestCase):
    def test_empty_fbs_order_list_is_successful_and_does_not_write(self):
        client = FakeClient("configured", orders=[])
        with (
            mock.patch.dict(os.environ, {
                "ERP_PRODUCTION_WB_FBS_SMOKE": "confirmed",
                "WB_API_TOKEN": "configured",
            }, clear=False),
            mock.patch.object(SMOKE, "WildberriesOrdersReadOnlyClient", return_value=client),
            mock.patch.object(SMOKE, "_http_body", return_value=b"public"),
            mock.patch.object(SMOKE, "_check_protected_assembly_route", return_value=302),
            mock.patch.object(SMOKE, "_check_assembly_contract"),
            mock.patch("builtins.print") as output,
        ):
            self.assertEqual(SMOKE.main(), 0)
        rendered = output.call_args[0][0]
        self.assertIn('"received": 0', rendered)
        self.assertIn('"wb_writes": 0', rendered)
        self.assertIn('"database_writes": 0', rendered)
        self.assertEqual(client.request_audit[0]["method"], "GET")

    def test_api_auth_and_availability_errors_fail_the_smoke(self):
        for code in ("WB_UNAUTHORIZED", "WB_FORBIDDEN", "WB_UNAVAILABLE"):
            with self.subTest(code=code):
                client = FakeClient(
                    "configured",
                    error=WildberriesReadOnlyError("safe", code=code),
                )
                with (
                    mock.patch.dict(os.environ, {
                        "ERP_PRODUCTION_WB_FBS_SMOKE": "confirmed",
                        "WB_API_TOKEN": "configured",
                    }, clear=False),
                    mock.patch.object(
                        SMOKE, "WildberriesOrdersReadOnlyClient", return_value=client
                    ),
                ):
                    with self.assertRaises(WildberriesReadOnlyError) as raised:
                        SMOKE.main()
                self.assertEqual(raised.exception.code, code)

    def test_assembly_contract_uses_authenticated_page_and_post_sync(self):
        SMOKE._check_assembly_contract()
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("_NoRedirect", source)
        self.assertIn('status != 302', source)
        self.assertNotIn("OrdersSnapshotStore", source)
        self.assertNotIn("synchronize_wildberries_orders", source)

    def test_deploy_includes_fbs_smoke_inside_stable_data_guard(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "deploy.sh").read_text(
            encoding="utf-8"
        )
        snapshot = source.rindex('DATA_SNAPSHOT_AFTER="$(stable_data_snapshot)"')
        smoke = source.rindex("scripts/wb_fbs_sales_smoke.py")
        self.assertLess(smoke, snapshot)


if __name__ == "__main__":
    unittest.main()
