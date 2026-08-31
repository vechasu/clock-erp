import os
import tempfile
import unittest
from pathlib import Path

from scripts.configure_wb_secret import update_secret


class WildberriesSecretStorageTest(unittest.TestCase):
    def test_updates_existing_protected_environment_file_without_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "clock-erp.env"
            path.write_text("SERVICE_VAULT_KEY=placeholder\nWB_API_TOKEN=old\n", encoding="utf-8")
            os.chmod(str(path), 0o600)
            update_secret(path, "new-test-token", expected_uid=os.getuid())
            content = path.read_text(encoding="utf-8")
            self.assertEqual(content.count("WB_API_TOKEN="), 1)
            self.assertIn("WB_API_TOKEN=new-test-token\n", content)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_newline_in_secret(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "clock-erp.env"
            path.write_text("SERVICE_VAULT_KEY=placeholder\n", encoding="utf-8")
            os.chmod(str(path), 0o600)
            with self.assertRaises(RuntimeError):
                update_secret(path, "bad\nvalue", expected_uid=os.getuid())


if __name__ == "__main__":
    unittest.main()
