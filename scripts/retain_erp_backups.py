#!/usr/bin/env python3
"""Create Clock ERP backups and enforce bounded backup retention."""

import argparse
import datetime as dt
import fcntl
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path


LEGACY_DAILY_BACKUP = re.compile(r"^clock-erp-(\d{8})-(\d{6})\.tar\.gz$")
DAILY_BACKUP = re.compile(r"^clock-erp-daily-(\d{8})-(\d{6})\.tar\.gz$")
TEMP_BACKUP = re.compile(
    r"^clock-erp-temp-(\d{8})-(\d{6})-[A-Za-z0-9_.-]+\.tar\.gz$"
)
CATALOG_BACKUP = re.compile(
    r"^catalog-before-[A-Za-z0-9_.-]+-(\d{8})-(\d{6})"
    r"(?:-[A-Za-z0-9_.-]+)?\.db$"
)
ARCHIVED_RUNTIME_BACKUP = re.compile(r"^runtime-backups-(\d{8})-(\d{6})$")
IMAGE_IMPORT_BACKUP = re.compile(r"^catalog-images-(\d{8})-(\d{6})$")
MODEL_BACKFILL_BACKUP = re.compile(
    r"^catalog\.db-models-(\d{8})-(\d{6})-[A-Za-z0-9_.-]+\.bak$"
)
BITRIX_STOCK_BACKUP = re.compile(
    r"^(\d{8})-(\d{6})-[A-Za-z0-9_.-]+-before-bitrix-products$"
)
BRAND_IMAGE_BACKUP = re.compile(r"^brand-images-(\d{8})-(\d{6})$")
RUNTIME_DATABASE_BACKUP = re.compile(
    r"^[A-Za-z0-9_.-]+\.db\.backup-[A-Za-z0-9_.-]+$"
)
SQLITE_HEADER = b"SQLite format 3\x00"
GZIP_HEADER = b"\x1f\x8b"
TEMPORARY_RETENTION_DAYS = 3
OPERATIONAL_RETENTION_DAYS = 30


def _parse_timestamp(match):
    return dt.datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")


def _valid_tar_backup(path, require_runtime=True):
    try:
        with path.open("rb") as backup_file:
            if not backup_file.read(16).startswith(GZIP_HEADER):
                return False
        found_runtime = False
        with tarfile.open(str(path), "r:gz") as archive:
            for member in archive:
                name = member.name
                while name.startswith("./"):
                    name = name[2:]
                name = name.lstrip("/")
                if name == ".env" or name == "instance" or name.startswith("instance/"):
                    found_runtime = True
        return found_runtime or not require_runtime
    except (OSError, EOFError, tarfile.TarError):
        return False


def _is_valid_backup(path, kind):
    if path.is_symlink():
        return False
    if kind == "operational-directory":
        if not path.is_dir():
            return False
        databases = []
        for item in path.rglob("*"):
            if item.is_symlink():
                return False
            if item.is_file() and (
                item.suffix in (".db", ".bak")
                or RUNTIME_DATABASE_BACKUP.match(item.name)
            ):
                databases.append(item)
        return bool(databases) and all(
            _is_valid_backup(database, "temporary-sqlite")
            for database in databases
        )
    if not path.is_file():
        return False
    if kind == "daily":
        return _valid_tar_backup(path)
    if kind == "temporary-tar":
        return _valid_tar_backup(path, require_runtime=False)
    try:
        with path.open("rb") as backup_file:
            return backup_file.read(16) == SQLITE_HEADER
    except OSError:
        return False


def _discover(directory, pattern, kind):
    backups = []
    if not directory.is_dir():
        return backups
    for path in directory.iterdir():
        match = pattern.match(path.name)
        if match is None:
            continue
        try:
            timestamp = _parse_timestamp(match)
        except ValueError:
            continue
        backups.append((timestamp, path, _is_valid_backup(path, kind)))
    return backups


def _discover_recursive(directory, pattern, kind):
    backups = []
    if not directory.is_dir():
        return backups
    for path in directory.rglob("*"):
        match = pattern.match(path.name)
        if match is None:
            continue
        try:
            timestamp = _parse_timestamp(match)
        except ValueError:
            continue
        backups.append((timestamp, path, _is_valid_backup(path, kind)))
    return backups


def discover_backups(backup_root):
    daily = _discover(backup_root, LEGACY_DAILY_BACKUP, "daily")
    daily.extend(_discover(backup_root / "daily", DAILY_BACKUP, "daily"))

    temporary = _discover(backup_root / "temporary", TEMP_BACKUP, "temporary-tar")
    temporary.extend(
        _discover_recursive(
            backup_root / "temporary", CATALOG_BACKUP, "temporary-sqlite"
        )
    )
    # This legacy directory is produced by the deploy migration and is confirmed
    # to contain Clock ERP SQLite snapshots. Unknown backup locations are ignored.
    temporary.extend(
        _discover(
            backup_root / "catalog-migrations",
            CATALOG_BACKUP,
            "temporary-sqlite",
        )
    )
    operational = _discover(
        backup_root / "archived-runtime",
        ARCHIVED_RUNTIME_BACKUP,
        "operational-directory",
    )
    operational.extend(
        _discover(
            backup_root / "image-imports",
            IMAGE_IMPORT_BACKUP,
            "operational-directory",
        )
    )
    operational.extend(
        _discover(
            backup_root / "model-backfill",
            MODEL_BACKFILL_BACKUP,
            "temporary-sqlite",
        )
    )
    operational.extend(
        _discover(
            backup_root / "bitrix-stock-sync",
            BITRIX_STOCK_BACKUP,
            "operational-directory",
        )
    )
    operational.extend(
        _discover(backup_root, BRAND_IMAGE_BACKUP, "operational-directory")
    )
    return {
        "daily": daily,
        "temporary": temporary,
        "operational": operational,
    }


def retention_plan(backups, now, policy="daily"):
    ordered = sorted(backups, key=lambda item: (item[0], str(item[1])), reverse=True)
    keep = set()
    if policy == "daily":
        cutoff = now.date() - dt.timedelta(days=6)
        retained_days = set()
        for timestamp, path, is_valid in ordered:
            if not is_valid or timestamp > now:
                keep.add(path)
                continue
            backup_date = timestamp.date()
            if backup_date >= cutoff and backup_date not in retained_days:
                retained_days.add(backup_date)
                keep.add(path)
    elif policy in ("temporary", "operational"):
        retention_days = (
            TEMPORARY_RETENTION_DAYS
            if policy == "temporary"
            else OPERATIONAL_RETENTION_DAYS
        )
        cutoff = now - dt.timedelta(days=retention_days)
        for timestamp, path, is_valid in ordered:
            if not is_valid or timestamp > now or timestamp >= cutoff:
                keep.add(path)
    else:
        raise ValueError("unknown retention policy: {}".format(policy))

    actions = []
    for timestamp, path, is_valid in ordered:
        if not is_valid:
            action = "SKIP_INVALID"
        elif timestamp > now:
            action = "KEEP_FUTURE"
        elif path in keep:
            action = "KEEP"
        else:
            action = "DELETE"
        actions.append((action, timestamp, path))
    return actions


def _path_size(path):
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def apply_plan(actions, backup_root, apply_changes):
    root = backup_root.resolve()
    counts = {}
    bytes_deleted = 0
    bytes_selected = 0
    for action, timestamp, path in actions:
        counts[action] = counts.get(action, 0) + 1
        size = _path_size(path) if path.exists() else 0
        print("{}|{}|{}|{}".format(action, timestamp.isoformat(), size, path))
        if action == "DELETE":
            bytes_selected += size
        if action != "DELETE" or not apply_changes:
            continue
        resolved = path.resolve()
        if root not in resolved.parents:
            raise RuntimeError("refusing to delete outside backup root: {}".format(path))
        if path.is_dir():
            shutil.rmtree(str(path))
        else:
            path.unlink()
        bytes_deleted += size
    print(
        "SUMMARY|mode={}|keep={}|delete={}|skip_invalid={}|bytes_selected={}|bytes_deleted={}".format(
            "apply" if apply_changes else "dry-run",
            counts.get("KEEP", 0) + counts.get("KEEP_FUTURE", 0),
            counts.get("DELETE", 0),
            counts.get("SKIP_INVALID", 0),
            bytes_selected,
            bytes_deleted,
        )
    )


def _backup_sqlite_database(source, destination):
    with sqlite3.connect(str(source)) as source_connection:
        backup = getattr(source_connection, "backup", None)
        if callable(backup):
            with sqlite3.connect(str(destination)) as destination_connection:
                backup(destination_connection)
            return

    sqlite_binary = shutil.which("sqlite3")
    if not sqlite_binary:
        raise RuntimeError(
            "SQLite backup requires the sqlite3 CLI on this Python version"
        )
    subprocess.run(
        [sqlite_binary, str(source), ".backup '{}'".format(
            str(destination).replace("'", "''")
        )],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _runtime_copy_ignore(instance):
    def ignore(directory, names):
        if Path(directory) != instance:
            return []
        return [
            name for name in names
            if name == "backups"
            or RUNTIME_DATABASE_BACKUP.match(name)
            or name.endswith((".db-journal", ".db-wal", ".db-shm"))
        ]
    return ignore


def _copy_runtime_data(project_root, staging):
    copied = False
    env_path = project_root / ".env"
    if env_path.is_file():
        shutil.copy2(str(env_path), str(staging / ".env"))
        copied = True

    instance = project_root / "instance"
    if instance.is_dir():
        staged_instance = staging / "instance"
        shutil.copytree(
            str(instance),
            str(staged_instance),
            symlinks=True,
            ignore=_runtime_copy_ignore(instance),
        )
        for source in instance.glob("*.db"):
            destination = staged_instance / source.name
            for suffix in ("", "-journal", "-wal", "-shm"):
                candidate = Path(str(destination) + suffix)
                if candidate.exists() or candidate.is_symlink():
                    candidate.unlink()
            _backup_sqlite_database(source, destination)
            with sqlite3.connect(str(destination)) as destination_connection:
                result = destination_connection.execute(
                    "PRAGMA quick_check"
                ).fetchone()
                if not result or result[0] != "ok":
                    raise sqlite3.DatabaseError(
                        "SQLite backup quick_check failed: {}".format(source)
                    )
        copied = True
    if not copied:
        raise RuntimeError("no .env or instance directory found in {}".format(project_root))


def archive_runtime_backups(project_root, backup_root, now, apply_changes=False):
    instance = project_root / "instance"
    if not instance.is_dir():
        return None
    sources = []
    nested_backups = instance / "backups"
    if nested_backups.exists() or nested_backups.is_symlink():
        sources.append(nested_backups)
    sources.extend(
        path for path in instance.iterdir()
        if RUNTIME_DATABASE_BACKUP.match(path.name)
    )
    if not sources:
        print("ARCHIVE_SKIPPED|no runtime backup artifacts")
        return None
    for source in sources:
        if source.is_symlink():
            raise RuntimeError("refusing to archive symlink: {}".format(source))
        if source.is_dir() and any(item.is_symlink() for item in source.rglob("*")):
            raise RuntimeError(
                "refusing to archive directory containing symlinks: {}".format(source)
            )

    archive_root = backup_root / "archived-runtime"
    target = archive_root / "runtime-backups-{}".format(
        now.strftime("%Y%m%d-%H%M%S")
    )
    print(
        "{}|{}|{}".format(
            "ARCHIVE" if apply_changes else "WOULD_ARCHIVE",
            len(sources),
            target,
        )
    )
    if not apply_changes:
        return target
    if target.exists():
        raise RuntimeError("runtime backup archive already exists: {}".format(target))
    archive_root.mkdir(parents=True, exist_ok=True)
    os.chmod(str(archive_root), 0o700)
    target.mkdir(mode=0o700)
    try:
        for source in sources:
            shutil.move(str(source), str(target / source.name))
    except Exception:
        for archived in target.iterdir():
            shutil.move(str(archived), str(instance / archived.name))
        target.rmdir()
        raise
    print("ARCHIVED|{}|{}".format(len(sources), target))
    return target


def _safe_label(value):
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.")
    if not label:
        raise ValueError("temporary snapshot label is empty")
    return label[:80]


def create_backup(project_root, backup_root, now, kind, label=None, apply_changes=False):
    if kind == "daily":
        existing = [
            item for item in discover_backups(backup_root)["daily"]
            if item[0].date() == now.date() and item[2]
        ]
        if existing:
            newest = max(existing, key=lambda item: item[0])
            print("CREATE_SKIPPED|daily backup already exists|{}".format(newest[1]))
            return newest[1]
        directory = backup_root / "daily"
        filename = "clock-erp-daily-{}.tar.gz".format(now.strftime("%Y%m%d-%H%M%S"))
    else:
        directory = backup_root / "temporary"
        filename = "clock-erp-temp-{}-{}.tar.gz".format(
            now.strftime("%Y%m%d-%H%M%S"), _safe_label(label or "snapshot")
        )
    target = directory / filename
    if not apply_changes:
        print("WOULD_CREATE|{}|{}".format(kind, target))
        return target

    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(str(directory), 0o700)
    staging = Path(tempfile.mkdtemp(prefix=".backup-stage.", dir=str(backup_root)))
    pending = directory / ("." + filename + ".pending")
    try:
        _copy_runtime_data(project_root, staging)
        with tarfile.open(str(pending), "w:gz") as archive:
            for name in (".env", "instance"):
                source = staging / name
                if source.exists():
                    archive.add(str(source), arcname=name, recursive=True)
        os.chmod(str(pending), 0o600)
        if not _valid_tar_backup(pending):
            raise RuntimeError("created archive failed validation: {}".format(pending))
        pending.replace(target)
        print("CREATED|{}|{}|{}".format(kind, target.stat().st_size, target))
        return target
    finally:
        if pending.exists():
            pending.unlink()
        shutil.rmtree(str(staging), ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--archive-runtime-backups", action="store_true")
    creation = parser.add_mutually_exclusive_group()
    creation.add_argument("--create-daily", action="store_true")
    creation.add_argument("--create-temporary", metavar="LABEL")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    arguments = parser.parse_args()

    backup_root = arguments.backup_root.resolve()
    if not backup_root.is_dir():
        parser.error("backup root does not exist: {}".format(backup_root))
    if (
        arguments.create_daily
        or arguments.create_temporary
        or arguments.archive_runtime_backups
    ) and not arguments.project_root:
        parser.error("--project-root is required when creating a backup")

    lock_path = backup_root / ".retention.lock"
    lock_file = lock_path.open("a+")
    os.chmod(str(lock_path), 0o600)
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        parser.error("another backup/retention process is already running")

    now = (
        dt.datetime.strptime(arguments.now, "%Y-%m-%dT%H:%M:%S")
        if arguments.now
        else dt.datetime.now()
    )
    if arguments.archive_runtime_backups:
        archive_runtime_backups(
            arguments.project_root.resolve(),
            backup_root,
            now,
            apply_changes=arguments.apply,
        )

    streams = discover_backups(backup_root)
    for stream in ("daily", "temporary", "operational"):
        print("STREAM|{}".format(stream))
        actions = retention_plan(streams[stream], now, policy=stream)
        apply_plan(actions, backup_root, arguments.apply)

    if arguments.create_daily:
        create_backup(
            arguments.project_root.resolve(), backup_root, now, "daily",
            apply_changes=arguments.apply,
        )
    elif arguments.create_temporary:
        create_backup(
            arguments.project_root.resolve(), backup_root, now, "temporary",
            label=arguments.create_temporary, apply_changes=arguments.apply,
        )


if __name__ == "__main__":
    main()
