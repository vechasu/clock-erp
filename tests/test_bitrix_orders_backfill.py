import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from app.domain_schema_migrations import apply_domain_migrations
from app.services.orders_snapshot import OrdersSnapshotStore
from scripts import backfill_bitrix_orders


def raw_order(order_id, date):
    return {
        "id": str(order_id),
        "number": str(order_id),
        "date": date,
        "updated_at": date,
        "status": "D",
        "price": 1000,
        "currency": "RUB",
        "customer": "Клиент {}".format(order_id),
        "phone": "+7 900 000 00 00",
        "products": [{
            "id": "basket-{}".format(order_id),
            "product_id": "product-{}".format(order_id),
            "name": "Товар {}".format(order_id),
            "quantity": 1,
            "price": 1000,
        }],
    }


class FakeHistoryClient:
    def __init__(self, **_kwargs):
        pass

    def history_pages(self, _limit, start_cursor=0):
        self.start_cursor = start_cursor
        yield {
            "orders": [
                raw_order(3, "2026-08-03 12:00:00"),
                raw_order(2, "2026-08-02 12:00:00"),
            ],
            "count": 2,
            "has_more": True,
            "next_cursor": 2,
        }
        yield {
            "orders": [raw_order(1, "2026-08-01 12:00:00")],
            "count": 1,
            "has_more": False,
            "next_cursor": None,
        }


class BitrixOrdersBackfillTest(unittest.TestCase):
    def test_apply_is_resumable_idempotent_and_preserves_local_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "orders.db"
            apply_domain_migrations(database, "orders", "test")
            store = OrdersSnapshotStore(database)
            store.replace([{
                "id": "3", "number": "3", "created_at": "2026-08-03",
                "status": "N", "customer": "Старое имя",
            }], 1)
            with store.connection() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM orders_snapshot WHERE order_id='3'"
                ).fetchone()
                payload = json.loads(row["payload_json"])
                payload["erp_private_note"] = "не перезаписывать"
                payload["tracking"] = "ERP-TRACK"
                connection.execute(
                    "UPDATE orders_snapshot SET payload_json=? WHERE order_id='3'",
                    (json.dumps(payload),),
                )

            arguments = [
                "--database", str(database),
                "--catalog-database", str(root / "catalog.db"),
                "--backup-dir", str(root / "backups"),
                "--url", "https://example.test/orders-history",
                "--apply", "--restart",
            ]
            with mock.patch.object(
                backfill_bitrix_orders,
                "BitrixOrdersReadOnlyClient",
                FakeHistoryClient,
            ):
                first_output = io.StringIO()
                with redirect_stdout(first_output):
                    self.assertEqual(backfill_bitrix_orders.main(arguments), 0)
                second_output = io.StringIO()
                with redirect_stdout(second_output):
                    self.assertEqual(backfill_bitrix_orders.main(arguments), 0)

            first = json.loads(first_output.getvalue().split("=", 1)[1])
            second = json.loads(second_output.getvalue().split("=", 1)[1])
            self.assertEqual(first["bitrix_available"], 3)
            self.assertEqual(first["after"]["orders"], 3)
            self.assertEqual(first["after"]["duplicates"], 0)
            self.assertEqual(second["actions"]["skipped"], 3)
            self.assertEqual(second["actions"]["added"], 0)
            self.assertEqual(second["actions"]["updated"], 0)
            self.assertTrue(store.history_checkpoint()["complete"])
            self.assertEqual(
                store.get("3")["erp_private_note"], "не перезаписывать"
            )
            self.assertEqual(store.get("3")["customer"], "Клиент 3")
            self.assertEqual(store.get("3")["status"], "N")
            self.assertEqual(store.get("3")["tracking"], "ERP-TRACK")


if __name__ == "__main__":
    unittest.main()
