import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.receipt_inventory import ReceiptInventory
from app.services.receipt_recovery import ReceiptRecovery
from scripts.recover_receipt_inventory import main as recovery_main


class ReceiptRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "catalog.db"
        self.database = CatalogDatabase(self.database_path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES ('recovery-batch', 'recovery-sha', 'recovery.xlsx', "
                "0, 0, 0, 0, 'active', ?, ?)",
                ("2026-07-31T08:00:00+00:00", "2026-07-31T08:00:00+00:00"),
            )
        self.product = ExcelProductCatalog(self.database).create_product(
            name="a.b.art Vintage Edge Brown",
            article="A.B.ART-BROWN",
            brand="A.B. Art",
            category="Очки",
            stock=0,
        )
        self.receipt = ReceiptInventory(self.database).create_receipt(
            {
                "id": "pr-2026-0002-id",
                "number": "PR-2026-0002",
                "receipt_date": "2026-07-31",
            },
            [{
                "product_id": self.product["id"],
                "quantity": 29,
                "purchase_price": 0,
            }],
            idempotency_key="pr-2026-0002-once",
        )
        self.instance_dir = self.root / "instance"
        self.instance_dir.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def stock(self):
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id = ?",
                (self.product["id"],),
            ).fetchone()["stock"]

    def test_existing_movement_is_a_noop_and_apply_is_audited(self):
        recovery = ReceiptRecovery(self.database, self.instance_dir)

        dry_run = recovery.inspect("PR-2026-0002")
        applied = recovery.apply("PR-2026-0002")
        repeated = recovery.apply("PR-2026-0002")

        self.assertEqual(dry_run["changes_required"], 0)
        self.assertTrue(dry_run["positions"][0]["movement_exists"])
        self.assertEqual(applied["changes_required"], 0)
        self.assertEqual(repeated["changes_required"], 0)
        self.assertEqual(self.stock(), 29)
        with self.database.connect() as connection:
            movement_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_stock_movements "
                "WHERE receipt_id = ? AND movement_type = 'receipt'",
                (self.receipt["id"],),
            ).fetchone()[0]
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM erp_receipt_recovery_audit "
                "WHERE receipt_id = ?",
                (self.receipt["id"],),
            ).fetchone()[0]
        self.assertEqual(movement_count, 1)
        self.assertEqual(audit_count, 2)

    def test_missing_movement_is_restored_once_with_backup(self):
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM catalog_stock_movements WHERE receipt_id = ?",
                (self.receipt["id"],),
            )
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 0 WHERE id = ?",
                (self.product["id"],),
            )
        output = StringIO()
        backup_dir = self.root / "backups"
        with redirect_stdout(output):
            exit_code = recovery_main([
                "--receipt-number",
                "PR-2026-0002",
                "--database",
                str(self.database_path),
                "--instance-dir",
                str(self.instance_dir),
                "--backup-dir",
                str(backup_dir),
                "--apply",
            ])
        result = json.loads(output.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["changes_required"], 0)
        self.assertEqual(self.stock(), 29)
        self.assertTrue(Path(result["backup"]).exists())
        ReceiptRecovery(self.database, self.instance_dir).apply(
            "PR-2026-0002"
        )
        self.assertEqual(self.stock(), 29)

    def test_pr_2026_0001_legacy_audit_does_not_change_stock(self):
        (self.instance_dir / "receipts.json").write_text(
            json.dumps([{
                "id": "legacy-pr-0001",
                "number": "PR-2026-0001",
                "status": "posted",
                "receipt_date": "2026-07-30",
                "positions": [{
                    "product_id": "moysklad-legacy-product",
                    "product_name": "test",
                    "brand": "test",
                    "category": "test",
                    "quantity": 1,
                }],
            }]),
            encoding="utf-8",
        )
        before = self.stock()

        report = ReceiptRecovery(
            self.database,
            self.instance_dir,
        ).inspect("PR-2026-0001")
        output = StringIO()
        with redirect_stdout(output):
            recovery_main([
                "--receipt-number",
                "PR-2026-0001",
                "--database",
                str(self.database_path),
                "--instance-dir",
                str(self.instance_dir),
                "--dry-run",
            ])

        self.assertEqual(report["source"], "legacy_json")
        self.assertEqual(report["mode"], "dry-run")
        self.assertFalse(report["positions"][0]["product_exists"])
        self.assertEqual(self.stock(), before)
        output.getvalue().encode("ascii")


if __name__ == "__main__":
    unittest.main()
