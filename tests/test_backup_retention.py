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
    apply_plan,
    archive_runtime_backups,
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

    def test_nested_inventory_snapshot_uses_temporary_retention(self):
        directory = self.root / "temporary" / "inventory-scope-migrations"
        directory.mkdir(parents=True)
        snapshot = directory / (
            "catalog-before-inventory-scopes-20260818-155959-644266.db"
        )
        with sqlite3.connect(str(snapshot)) as connection:
            connection.execute("CREATE TABLE inventory (id INTEGER)")

        temporary = discover_backups(self.root)["temporary"]

        self.assertEqual(len(temporary), 1)
        self.assertEqual(temporary[0][0], dt.datetime(2026, 8, 18, 15, 59, 59))
        self.assertEqual(temporary[0][1], snapshot)
        self.assertTrue(temporary[0][2])

    def test_operational_policy_expires_after_thirty_days(self):
        now = dt.datetime(2026, 8, 21, 16, 0, 0)
        operational = self.root / "model-backfill"
        operational.mkdir()
        recent = operational / "catalog.db-models-20260722-160000-new.bak"
        expired = operational / "catalog.db-models-20260722-155959-old.bak"
        for path in (recent, expired):
            with sqlite3.connect(str(path)) as connection:
                connection.execute("CREATE TABLE products (id INTEGER)")

        actions = retention_plan(
            discover_backups(self.root)["operational"],
            now,
            policy="operational",
        )
        by_path = {path: action for action, _timestamp, path in actions}
        self.assertEqual(by_path[recent], "KEEP")
        self.assertEqual(by_path[expired], "DELETE")

        apply_plan(actions, self.root, apply_changes=True)
        self.assertTrue(recent.is_file())
        self.assertFalse(expired.exists())

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

    def test_daily_creation_excludes_nested_runtime_backups(self):
        project = Path(self.temp.name) / "project"
        instance = project / "instance"
        nested = instance / "backups" / "old"
        nested.mkdir(parents=True)
        (nested / "catalog.db").write_bytes(b"old")
        (instance / "catalog.db.backup-strap-20260723").write_bytes(b"old")
        with sqlite3.connect(str(instance / "catalog.db")) as connection:
            connection.execute("CREATE TABLE products (id INTEGER)")

        backup = create_backup(
            project,
            self.root,
            dt.datetime(2026, 8, 21, 3, 17, 0),
            "daily",
            apply_changes=True,
        )

        with tarfile.open(str(backup), "r:gz") as archive:
            names = archive.getnames()
        self.assertIn("instance/catalog.db", names)
        self.assertNotIn("instance/backups/old/catalog.db", names)
        self.assertNotIn("instance/catalog.db.backup-strap-20260723", names)

    def test_runtime_backups_are_moved_to_managed_archive(self):
        project = Path(self.temp.name) / "project"
        instance = project / "instance"
        nested = instance / "backups" / "old"
        nested.mkdir(parents=True)
        with sqlite3.connect(str(nested / "catalog.db")) as connection:
            connection.execute("CREATE TABLE products (id INTEGER)")
        strap = instance / "catalog.db.backup-strap-20260723"
        with sqlite3.connect(str(strap)) as connection:
            connection.execute("CREATE TABLE products (id INTEGER)")
        now = dt.datetime(2026, 8, 21, 3, 17, 0)

        target = archive_runtime_backups(
            project, self.root, now, apply_changes=True
        )

        self.assertFalse((instance / "backups").exists())
        self.assertFalse(strap.exists())
        self.assertTrue((target / "backups" / "old" / "catalog.db").is_file())
        self.assertTrue((target / strap.name).is_file())
        operational = discover_backups(self.root)["operational"]
        self.assertEqual(operational[0][1], target)
        self.assertTrue(operational[0][2])

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
