#!/usr/bin/env python3
"""Apply bounded retention to known Clock ERP automatic backup streams."""

import argparse
import datetime as dt
import fcntl
import os
import re
import tarfile
from pathlib import Path


FULL_BACKUP = re.compile(r"^clock-erp-(\d{8})-(\d{6})\.tar\.gz$")
CATALOG_BACKUP = re.compile(
    r"^catalog-before-[A-Za-z0-9_.-]+-(\d{8})-(\d{6})\.db$"
)
SQLITE_HEADER = b"SQLite format 3\x00"
GZIP_HEADER = b"\x1f\x8b"


def _month_offset(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    return (month_index // 12, month_index % 12 + 1)


def _parse_timestamp(match):
    return dt.datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")


def _is_valid_backup(path, stream):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as backup_file:
            header = backup_file.read(16)
        if stream == "full":
            if not header.startswith(GZIP_HEADER):
                return False
            with tarfile.open(str(path), "r:gz") as archive:
                for index, member in enumerate(archive):
                    name = member.name
                    while name.startswith("./"):
                        name = name[2:]
                    name = name.lstrip("/")
                    if name == ".env" or name == "instance" or name.startswith("instance/"):
                        return True
                    if index >= 20:
                        break
            return False
        return header == SQLITE_HEADER
    except (OSError, EOFError, tarfile.TarError):
        return False


def discover_backups(backup_root):
    streams = {"full": [], "catalog-migration": []}
    candidates = (
        ("full", backup_root, FULL_BACKUP),
        ("catalog-migration", backup_root / "catalog-migrations", CATALOG_BACKUP),
    )
    for stream, directory, pattern in candidates:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            match = pattern.match(path.name)
            if match is None:
                continue
            try:
                timestamp = _parse_timestamp(match)
            except ValueError:
                continue
            streams[stream].append((timestamp, path, _is_valid_backup(path, stream)))
    return streams


def retention_plan(backups, now):
    ordered = sorted(backups, key=lambda item: (item[0], str(item[1])), reverse=True)
    keep = set()
    valid = [item for item in ordered if item[2]]
    if valid:
        keep.add(valid[0][1])

    daily_cutoff = now.date() - dt.timedelta(days=6)
    weekly_cutoff = now.date() - dt.timedelta(days=27)
    retained_months = {_month_offset(now, offset) for offset in (0, -1, -2)}
    daily = set()
    weekly = set()
    monthly = set()

    for timestamp, path, is_valid in ordered:
        if not is_valid or timestamp > now:
            keep.add(path)
            continue
        backup_date = timestamp.date()
        if backup_date >= daily_cutoff:
            key = backup_date
            if key not in daily:
                daily.add(key)
                keep.add(path)
            continue
        if backup_date >= weekly_cutoff:
            iso = backup_date.isocalendar()
            key = (iso[0], iso[1])
            if key not in weekly:
                weekly.add(key)
                keep.add(path)
            continue
        key = (timestamp.year, timestamp.month)
        if key in retained_months and key not in monthly:
            monthly.add(key)
            keep.add(path)

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


def apply_plan(actions, backup_root, apply_changes):
    root = backup_root.resolve()
    counts = {}
    bytes_deleted = 0
    bytes_selected = 0
    for action, timestamp, path in actions:
        counts[action] = counts.get(action, 0) + 1
        size = path.stat().st_size if path.exists() else 0
        print("{}|{}|{}|{}".format(action, timestamp.isoformat(), size, path))
        if action != "DELETE" or not apply_changes:
            if action == "DELETE":
                bytes_selected += size
            continue
        resolved = path.resolve()
        if root not in resolved.parents:
            raise RuntimeError("Refusing to delete outside backup root: {}".format(path))
        path.unlink()
        bytes_deleted += size
    print(
        "SUMMARY|mode={}|keep={}|delete={}|skip_invalid={}|bytes_selected={}|bytes_deleted={}".format(
            "apply" if apply_changes else "dry-run",
            counts.get("KEEP", 0) + counts.get("KEEP_FUTURE", 0),
            counts.get("DELETE", 0),
            counts.get("SKIP_INVALID", 0),
            bytes_selected + bytes_deleted,
            bytes_deleted,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    arguments = parser.parse_args()

    backup_root = arguments.backup_root.resolve()
    if not backup_root.is_dir():
        parser.error("backup root does not exist: {}".format(backup_root))
    lock_path = backup_root / ".retention.lock"
    lock_file = lock_path.open("a+")
    os.chmod(str(lock_path), 0o600)
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        parser.error("another retention process is already running")
    now = (
        dt.datetime.strptime(arguments.now, "%Y-%m-%dT%H:%M:%S")
        if arguments.now
        else dt.datetime.now()
    )
    streams = discover_backups(backup_root)
    for stream in sorted(streams):
        print("STREAM|{}".format(stream))
        actions = retention_plan(streams[stream], now)
        apply_plan(actions, backup_root, arguments.apply)


if __name__ == "__main__":
    main()
