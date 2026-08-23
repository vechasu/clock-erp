import datetime as dt
import io
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.retain_erp_backups import (
    _backup_sqlite_database,
    create_backup,
    discover_backups,
    retention_plan,
)


class BackupRetentionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "backups"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def full_backup(self, timestamp, directory=None, prefix="clock-erp"):
        directory = directory or self.root
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "{}-{}.tar.gz".format(
            prefix, timestamp.strftime("%Y%m%d-%H%M%S")
        )
        payload = b"backup"
        with tarfile.open(str(path), "w:gz") as backup:
            member = tarfile.TarInfo("./instance/catalog.db")
            member.size = len(payload)
            backup.addfile(member, io.BytesIO(payload))
        return path

    def test_daily_policy_keeps_one_per_day_for_seven_calendar_days(self):
        now = dt.datetime(2026, 8, 21, 16, 0, 0)
        same_day_old = self.full_backup(dt.datetime(2026, 8, 21, 10, 0, 0))
        same_day_new = self.full_backup(dt.datetime(2026, 8, 21, 15, 0, 0))
        within_window = self.full_backup(dt.datetime(2026, 8, 15, 15, 0, 0))
        expired = self.full_backup(dt.datetime(2026, 8, 14, 23, 59, 59))

        actions = retention_plan(discover_backups(self.root)["daily"], now)
        by_path = {path: action for action, _timestamp, path in actions}

        self.assertEqual(by_path[same_day_new], "KEEP")
        self.assertEqual(by_path[same_day_old], "DELETE")
        self.assertEqual(by_path[within_window], "KEEP")
        self.assertEqual(by_path[expired], "DELETE")

    def test_temporary_policy_expires_after_exactly_three_days(self):
        now = dt.datetime(2026, 8, 21, 16, 0, 0)
        temporary = self.root / "temporary"
        recent = self.full_backup(
            dt.datetime(2026, 8, 18, 16, 0, 0), temporary, "clock-erp-temp"
        )
        recent_target = temporary / "clock-erp-temp-20260818-160000-import.tar.gz"
        recent.rename(recent_target)
        recent = recent_target
        expired = self.full_backup(
            dt.datetime(2026, 8, 18, 15, 59, 59), temporary, "clock-erp-temp"
        )
        expired_target = temporary / "clock-erp-temp-20260818-155959-sync.tar.gz"
        expired.rename(expired_target)
        expired = expired_target

        actions = retention_plan(
            discover_backups(self.root)["temporary"], now, policy="temporary"
        )
        by_path = {path: action for action, _timestamp, path in actions}
        self.assertEqual(by_path[recent], "KEEP")
        self.assertEqual(by_path[expired], "DELETE")

    def test_unknown_and_invalid_files_are_never_delete_candidates(self):
        unknown = self.root / "site-backup-20260821.tar.gz"
        unknown.write_bytes(b"site")
        invalid = self.root / "clock-erp-20260821-150000.tar.gz"
        invalid.write_bytes(b"not a backup")

        streams = discover_backups(self.root)
        actions = retention_plan(streams["daily"], dt.datetime(2026, 8, 21, 16, 0, 0))

        self.assertNotIn(unknown, [path for _action, _timestamp, path in actions])
        self.assertEqual(actions[0][0], "SKIP_INVALID")

    def test_daily_creation_is_idempotent_and_sqlite_is_valid(self):
        project = Path(self.temp.name) / "project"
        instance = project / "instance"
        instance.mkdir(parents=True)
        database = instance / "catalog.db"
        with sqlite3.connect(str(database)) as connection:
            connection.execute("CREATE TABLE products (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO products DEFAULT VALUES")
        now = dt.datetime(2026, 8, 21, 3, 17, 0)

        first = create_backup(project, self.root, now, "daily", apply_changes=True)
        second = create_backup(
            project, self.root, now.replace(hour=18), "daily", apply_changes=True
        )

        self.assertEqual(first, second)
        self.assertTrue(first.is_file())
        self.assertEqual(len(discover_backups(self.root)["daily"]), 1)
        with tarfile.open(str(first), "r:gz") as backup:
            self.assertIn("instance/catalog.db", backup.getnames())

    def test_legacy_python_uses_sqlite_cli_backup(self):
        class LegacyConnection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        source = Path(self.temp.name) / "source.db"
        destination = Path(self.temp.name) / "destination.db"
        with mock.patch(
            "scripts.retain_erp_backups.sqlite3.connect",
            return_value=LegacyConnection(),
        ), mock.patch(
            "scripts.retain_erp_backups.shutil.which",
            return_value="/usr/bin/sqlite3",
        ), mock.patch(
            "scripts.retain_erp_backups.subprocess.run"
        ) as run:
            _backup_sqlite_database(source, destination)

        run.assert_called_once_with(
            [
                "/usr/bin/sqlite3",
                str(source),
                ".backup '{}'".format(destination),
            ],
            check=True,
            stdout=mock.ANY,
            stderr=mock.ANY,
        )


if __name__ == "__main__":
    unittest.main()
