import datetime as dt
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.retain_erp_backups import discover_backups, retention_plan


class BackupRetentionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def full_backup(self, timestamp):
        path = self.root / "clock-erp-{}.tar.gz".format(timestamp.strftime("%Y%m%d-%H%M%S"))
        payload = b"backup"
        with tarfile.open(str(path), "w:gz") as backup:
            member = tarfile.TarInfo("./instance/catalog.db")
            member.size = len(payload)
            backup.addfile(member, io.BytesIO(payload))
        return path

    def test_keeps_one_daily_weekly_monthly_and_latest(self):
        now = dt.datetime(2026, 8, 21, 16, 0, 0)
        recent_old = self.full_backup(dt.datetime(2026, 8, 21, 10, 0, 0))
        recent_new = self.full_backup(dt.datetime(2026, 8, 21, 15, 0, 0))
        daily = self.full_backup(dt.datetime(2026, 8, 20, 15, 0, 0))
        weekly_old = self.full_backup(dt.datetime(2026, 8, 10, 10, 0, 0))
        weekly_new = self.full_backup(dt.datetime(2026, 8, 11, 10, 0, 0))
        monthly = self.full_backup(dt.datetime(2026, 7, 15, 10, 0, 0))
        expired = self.full_backup(dt.datetime(2026, 4, 15, 10, 0, 0))

        actions = retention_plan(discover_backups(self.root)["full"], now)
        by_path = {path: action for action, _timestamp, path in actions}

        self.assertEqual(by_path[recent_new], "KEEP")
        self.assertEqual(by_path[recent_old], "DELETE")
        self.assertEqual(by_path[daily], "KEEP")
        self.assertEqual(by_path[weekly_new], "KEEP")
        self.assertEqual(by_path[weekly_old], "DELETE")
        self.assertEqual(by_path[monthly], "KEEP")
        self.assertEqual(by_path[expired], "DELETE")

    def test_unknown_and_invalid_files_are_never_delete_candidates(self):
        unknown = self.root / "site-backup-20260821.tar.gz"
        unknown.write_bytes(b"site")
        invalid = self.root / "clock-erp-20260821-150000.tar.gz"
        invalid.write_bytes(b"not a backup")

        streams = discover_backups(self.root)
        actions = retention_plan(streams["full"], dt.datetime(2026, 8, 21, 16, 0, 0))

        self.assertNotIn(unknown, [path for _action, _timestamp, path in actions])
        self.assertEqual(actions[0][0], "SKIP_INVALID")

    def test_env_only_full_backup_is_valid(self):
        path = self.root / "clock-erp-20260821-150000.tar.gz"
        payload = b"configuration"
        with tarfile.open(str(path), "w:gz") as backup:
            member = tarfile.TarInfo("./.env")
            member.size = len(payload)
            backup.addfile(member, io.BytesIO(payload))

        actions = retention_plan(
            discover_backups(self.root)["full"],
            dt.datetime(2026, 8, 21, 16, 0, 0),
        )

        self.assertEqual(actions[0][0], "KEEP")


if __name__ == "__main__":
    unittest.main()
