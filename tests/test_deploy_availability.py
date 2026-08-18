import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy.sh"


class DeployAvailabilityTest(unittest.TestCase):
    def test_deploy_script_is_valid_shell(self):
        completed = subprocess.run(
            ["bash", "-n", str(DEPLOY_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_code_only_deploy_uses_graceful_gunicorn_reload(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('DATABASE_MIGRATION_REQUIRED=0', script)
        self.assertIn(
            'systemctl kill --kill-who=main --signal=HUP "$SERVICE_NAME"',
            script,
        )
        self.assertNotIn('\nsystemctl restart "$SERVICE_NAME"\nSERVICE_STOPPED=0\n', script)

    def test_database_migration_is_blocked_during_active_inventory(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "SELECT COUNT(*) FROM erp_inventory_sessions WHERE status = 'active';",
            script,
        )
        self.assertIn("DEPLOY_BLOCKED:", script)
        self.assertIn(
            'if [[ "$DATABASE_MIGRATION_REQUIRED" == "1" && -f instance/catalog.db ]]',
            script,
        )


if __name__ == "__main__":
    unittest.main()
