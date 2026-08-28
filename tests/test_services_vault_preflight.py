import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

from app.services.service_vault import ServiceVault
from scripts.migrate_services_vault import apply as apply_migration
from scripts.preflight_service_vault import main, validate


class ServicesVaultPreflightTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "services.db"
        apply_migration(self.database)
        self.key = Fernet.generate_key().decode("ascii")
        self.user = {"id": 1, "role": "admin"}
        ServiceVault(self.database, self.key).create({
            "accounts": [{
                "label": "Protected",
                "login": "preflight-login",
                "password": "preflight-password",
            }],
            "category": "infrastructure",
            "icon": "lock",
            "name": "Preflight",
            "permissions": [],
            "url": "https://preflight.example",
        }, self.user)

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_key_roundtrip_and_existing_ciphertext(self):
        with mock.patch.dict(os.environ, {"SERVICE_VAULT_KEY": self.key}):
            result = validate(self.database)
        self.assertEqual(result["roundtrip"], "pass")
        self.assertEqual(result["database"], "readable")
        self.assertEqual(result["services"], 1)
        self.assertEqual(result["encrypted_fields"], 2)

    def test_wrong_key_fails_without_disclosure(self):
        wrong_key = Fernet.generate_key().decode("ascii")
        stdout = io.StringIO()
        stderr = io.StringIO()
        arguments = [
            "preflight_service_vault.py", "--database", str(self.database),
        ]
        with mock.patch.dict(os.environ, {"SERVICE_VAULT_KEY": wrong_key}), \
                mock.patch("sys.argv", arguments), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            result = main()
        self.assertEqual(result, 1)
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(self.key, combined)
        self.assertNotIn(wrong_key, combined)
        self.assertNotIn("preflight-login", combined)
        self.assertNotIn("preflight-password", combined)

    def test_missing_database_still_validates_key_format_and_roundtrip(self):
        missing = Path(self.temporary.name) / "missing.db"
        with mock.patch.dict(os.environ, {"SERVICE_VAULT_KEY": self.key}):
            result = validate(missing, allow_missing=True)
        self.assertEqual(result["database"], "missing")
        with mock.patch.dict(os.environ, {"SERVICE_VAULT_KEY": "invalid"}):
            with self.assertRaises(Exception):
                validate(missing, allow_missing=True)


if __name__ == "__main__":
    unittest.main()
