import threading
import unittest
from unittest import mock

from app.clients.moysklad import MoySkladClient


class MoySkladReceiptParallelismTest(unittest.TestCase):
    def test_organization_and_store_are_loaded_in_parallel(self):
        client = MoySkladClient()
        barrier = threading.Barrier(2)

        def organization():
            barrier.wait(timeout=2)
            return {"meta": {"href": "organization"}}

        def store():
            barrier.wait(timeout=2)
            return {"meta": {"href": "store"}}

        with mock.patch.object(
            client,
            "get_default_organization",
            side_effect=organization,
        ), mock.patch.object(
            client,
            "get_default_store",
            side_effect=store,
        ):
            payload = client.build_stock_enter_payload(
                positions=[{
                    "product_id": "remote-product",
                    "quantity": 1,
                    "purchase_price": 0,
                }],
                reason="Production timeout regression",
                moment="2026-07-30",
            )

        self.assertEqual(
            payload["organization"]["meta"]["href"],
            "organization",
        )
        self.assertEqual(
            payload["store"]["meta"]["href"],
            "store",
        )
        self.assertEqual(payload["positions"][0]["quantity"], 1)


if __name__ == "__main__":
    unittest.main()
