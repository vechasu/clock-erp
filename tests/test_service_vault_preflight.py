import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from app.services.service_vault import ServiceVault
from scripts.migrate_services_vault import apply
from scripts.service_vault_preflight import VaultPreflightError, preflight


class ServiceVaultPreflightTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "services.db"
        self.environment = self.root / "clock-erp.env"
        self.key = Fernet.generate_key().decode("ascii")
        self.environment.write_text("SERVICE_VAULT_KEY={}\n".format(self.key), encoding="utf-8")
        os.chmod(str(self.environment), 0o600)
        apply(self.database)
        vault = ServiceVault(self.database, self.key)
        self.service_id = vault.create({
            "name": "Example", "url": "https://example.com", "description": "",
            "category": "sites", "icon": "globe", "accounts": [{
                "label": "Main", "login": "private-login", "password": "private-password",
            }], "permissions": [],
        }, {"id": 1, "role": "admin"})

    def tearDown(self):
        self.temporary.cleanup()

    def ciphertext(self):
        with sqlite3.connect(str(self.database)) as connection:
            return connection.execute(
                "SELECT login_encrypted,password_encrypted FROM service_accounts ORDER BY id"
            ).fetchall()

    def test_valid_existing_key_decrypts_copy_without_changing_ciphertext(self):
        before = self.ciphertext()
        report = preflight(self.environment, self.database, expected_uid=os.getuid())
        self.assertEqual(report["database_quick_check"], "ok")
        self.assertEqual(report["encrypted_fields"], 2)
        self.assertEqual(self.ciphertext(), before)

    def test_missing_or_invalid_key_fails_without_generating_one(self):
        self.environment.write_text("OTHER=value\n", encoding="utf-8")
        with self.assertRaises(VaultPreflightError):
            preflight(self.environment, self.database, expected_uid=os.getuid())
        self.assertEqual(self.environment.read_text(encoding="utf-8"), "OTHER=value\n")
        self.environment.write_text("SERVICE_VAULT_KEY=invalid\n", encoding="utf-8")
        with self.assertRaises(VaultPreflightError):
            preflight(self.environment, self.database, expected_uid=os.getuid())

    def test_mismatched_key_fails_without_changing_existing_data(self):
        before = self.ciphertext()
        wrong = Fernet.generate_key().decode("ascii")
        self.environment.write_text("SERVICE_VAULT_KEY={}\n".format(wrong), encoding="utf-8")
        with self.assertRaises(VaultPreflightError) as failure:
            preflight(self.environment, self.database, expected_uid=os.getuid())
        self.assertIn("cannot be decrypted", str(failure.exception))
        self.assertNotIn(wrong, str(failure.exception))
        self.assertEqual(self.ciphertext(), before)


if __name__ == "__main__":
    unittest.main()
