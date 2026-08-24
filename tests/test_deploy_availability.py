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
        self.assertIn('CATALOG_MIGRATION_REQUIRED=0', script)
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
        self.assertIn("DEPLOY_BLOCKED_DETAILS:", script)
        self.assertIn("SELECT s.id, COUNT(i.id) AS item_count", script)
        self.assertIn("COUNT(i.id)", script)
        self.assertIn(
            'if [[ "$CATALOG_MIGRATION_REQUIRED" == "1" && -f instance/catalog.db ]]',
            script,
        )
        self.assertIn("scripts/migration_preflight.py", script)
        self.assertNotIn("scripts/consolidate_global_categories.py", script)

    def test_daily_backup_and_disk_guard_run_before_code_update(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        backup_stage = script.index("BACKUP: retention, disk guard, daily backup")
        retention = script.index('--backup-root "$BACKUP_DIR"', backup_stage)
        disk_guard = script.index("check_backup_disk_usage", backup_stage)
        daily_backup = script.index("--create-daily", backup_stage)
        self.assertLess(retention, disk_guard)
        self.assertLess(disk_guard, daily_backup)
        self.assertIn('readonly MAX_BACKUP_DISK_USAGE=85', script)
        self.assertGreaterEqual(script.count("check_backup_disk_usage"), 3)
        self.assertNotIn('BACKUP_PATH="$BACKUP_DIR/clock-erp-', script)

    def test_failed_preflight_stops_before_application_update_and_restart(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        preflight = script.index("MIGRATION PREFLIGHT: stage release")
        migration_command = script.index("migration_preflight.py\" preflight")
        application_update = script.index("APPLICATION UPDATE: fast-forward")
        merge = script.index('git merge --ff-only "$FETCHED_COMMIT"')
        service_stop = script.index('systemctl stop "$SERVICE_NAME"', merge)
        self.assertLess(preflight, migration_command)
        self.assertLess(migration_command, application_update)
        self.assertLess(application_update, merge)
        self.assertLess(merge, service_stop)

    def test_production_migration_is_service_stopped_and_data_guarded(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        production = script.index("PRODUCTION MIGRATION: stop service")
        stop = script.index('systemctl stop "$SERVICE_NAME"', production)
        backup = script.index("CATALOG_ROLLBACK_BACKUP=", production)
        apply = script.index("migration_preflight.py apply", production)
        compare = script.index('DATA_SNAPSHOT_BEFORE" != "$DATA_SNAPSHOT_AFTER', production)
        start = script.index("SERVICE START: controlled start", production)
        self.assertLess(stop, backup)
        self.assertLess(backup, apply)
        self.assertLess(apply, compare)
        self.assertLess(compare, start)

    def test_legacy_migration_script_changes_are_fail_closed(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("UNREGISTERED_MIGRATION_CHANGE=1", script)
        self.assertIn(
            "changed legacy migration script is not registered in production preflight",
            script,
        )


if __name__ == "__main__":
    unittest.main()
