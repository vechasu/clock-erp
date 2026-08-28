import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.auth import AuthStore
from app.domain_schema_migrations import apply_domain_migrations
from app.schema_migrations import apply_migrations
from app.services.orders_snapshot import OrdersSnapshotStore
from app.sms_migrations import migrate_database as migrate_sms


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuntimeDDLGateTest(unittest.TestCase):
    def test_checked_in_inventory_matches_runtime_debt(self):
        completed = subprocess.run(
            [sys.executable, "scripts/check_runtime_ddl.py", "--json"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["tracked_runtime_containers"], 1)
        self.assertEqual(report["tracked_ensure_functions"], 0)
        self.assertEqual(report["tracked_legacy_scripts"], 6)
        self.assertEqual(report["tracked_migration_modules"], 7)

    def test_new_runtime_ddl_container_is_detected(self):
        from scripts import check_runtime_ddl

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unexpected.py"
            path.write_text(
                "def request_handler(connection):\n"
                "    connection.execute(\"CREATE TABLE hidden(id INTEGER)\")\n",
                encoding="utf-8",
            )
            self.assertEqual(
                check_runtime_ddl.ddl_containers(path), {"request_handler"}
            )

    def test_parallel_startup_without_sentinel_preserves_schema_and_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.db"
            orders = root / "orders.db"
            auth = root / "auth.db"
            sms = root / "sms.db"
            apply_migrations(catalog, app_commit="runtime-ddl-test")
            apply_domain_migrations(orders, "orders", "runtime-ddl-test")
            apply_domain_migrations(auth, "auth", "runtime-ddl-test")
            migrate_sms(sms)
            OrdersSnapshotStore(orders).initialize()
            AuthStore(auth)
            environment = dict(os.environ)
            environment.update({
                "ERP_SECRET_KEY": "runtime-ddl-test-secret-key-00000000000000000000",
                "BITRIX_LOGIN": "",
                "BITRIX_PASSWORD": "",
                "BITRIX_CATALOG_TOKEN": "",
                "MOYSKLAD_TOKEN": "",
            })
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_runtime_ddl.py",
                    "--copy-root", str(root),
                    "--workers", "2",
                    "--expected-python", sys.version.split()[0],
                ],
                cwd=str(PROJECT_ROOT),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=60,
            )
            self.assertEqual(
                completed.returncode, 0, completed.stderr + completed.stdout
            )
            report = json.loads(completed.stdout)
            self.assertTrue(report["all_workers_ok"])
            self.assertTrue(report["schema_unchanged"])
            self.assertTrue(report["ledger_unchanged"])
            self.assertTrue(report["business_data_unchanged"])
            self.assertTrue(report["network_egress_blocked"])
            for worker in report["workers"]:
                statements = worker["statements"]
                self.assertFalse(any(
                    statement.lstrip().upper().startswith(
                        ("CREATE ", "ALTER ", "DROP ", "REINDEX ")
                    )
                    for statement in statements
                ), statements)


if __name__ == "__main__":
    unittest.main()
