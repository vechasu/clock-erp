#!/usr/bin/env python3
"""Rehearse and apply catalog migrations using the production Python runtime."""

from __future__ import print_function

import argparse
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schema_migrations import (  # noqa: E402
    MIGRATIONS,
    MigrationError,
    apply_migrations,
    business_snapshot,
    integrity_report,
    require_integrity,
    schema_fingerprint,
    schema_structure,
    sqlite_backup,
    validate_known_sql_compatibility,
    verify_ledger,
    verify_complete_catalog_contract,
    verify_runtime_guard,
    verify_schema_contract,
    write_runtime_guard,
)


def connect(path, read_only=False):
    if read_only:
        return sqlite3.connect(
            "file:{}?mode=ro".format(Path(path).resolve()), uri=True
        )
    return sqlite3.connect(str(Path(path).resolve()))


def write_report(path, report):
    if not path:
        return
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(".{}.tmp".format(target.name))
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(target))


def ensure_runtime(expected):
    actual = sqlite3.sqlite_version
    if expected and actual != expected:
        raise MigrationError(
            "production SQLite mismatch: expected {}, runtime uses {}".format(
                expected, actual
            )
        )
    return {
        "python": sys.version.split()[0],
        "sqlite": actual,
    }


def block_network_egress():
    def blocked_connect(unused_socket, unused_address):
        raise OSError("catalog migration preflight blocks network egress")

    def blocked_getaddrinfo(*unused_args, **unused_kwargs):
        raise OSError("catalog migration preflight blocks DNS egress")

    socket.socket.connect = blocked_connect
    socket.getaddrinfo = blocked_getaddrinfo


def clean_expired_rehearsals(root, retention_days):
    root = Path(root).resolve()
    if not root.exists():
        return []
    cutoff = time.time() - max(1, int(retention_days)) * 86400
    removed = []
    for path in root.iterdir():
        if (
            path.is_dir()
            and path.name.startswith("rehearsal-")
            and path.stat().st_mtime < cutoff
        ):
            shutil.rmtree(str(path))
            removed.append(path.name)
    return removed


def unique_rehearsal_directory(root, app_commit):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(str(root), 0o700)
    prefix = "rehearsal-{}-".format(str(app_commit or "unknown")[:12])
    path = Path(__import__("tempfile").mkdtemp(prefix=prefix, dir=str(root)))
    os.chmod(str(path), 0o700)
    return path


def migration_run(path, app_commit, ddl_state):
    def observe(statement):
        ddl_state["last_ddl"] = statement

    return apply_migrations(
        path,
        app_commit=app_commit,
        ddl_observer=observe,
    )


def preflight(arguments):
    runtime = ensure_runtime(arguments.expected_sqlite_version)
    block_network_egress()
    scanned = validate_known_sql_compatibility(arguments.source_root)
    removed = clean_expired_rehearsals(
        arguments.rehearsal_root, arguments.retention_days
    )
    run_directory = unique_rehearsal_directory(
        arguments.rehearsal_root, arguments.app_commit
    )
    original_copy = run_directory / "catalog-original.db"
    rehearsal_copy = run_directory / "catalog-rehearsal.db"
    fresh_database = run_directory / "catalog-fresh.db"
    ddl_state = {"last_ddl": ""}
    report = {
        "status": "failed",
        "stage": "MIGRATION PREFLIGHT",
        "runtime": runtime,
        "app_commit": arguments.app_commit,
        "source_database": str(Path(arguments.database).resolve()),
        "run_directory": str(run_directory),
        "backup_path": str(original_copy),
        "rehearsal_path": str(rehearsal_copy),
        "scanned_sql_files": scanned,
        "expired_rehearsals_removed": removed,
        "migrations": [dict(migration) for migration in MIGRATIONS],
        "network_egress": 0,
    }
    try:
        sqlite_backup(
            arguments.database,
            original_copy,
            sqlite_binary=arguments.sqlite_binary,
        )
        shutil.copy2(str(original_copy), str(rehearsal_copy))
        os.chmod(str(rehearsal_copy), 0o600)
        with connect(rehearsal_copy) as connection:
            report["integrity_before"] = require_integrity(
                connection, "preflight-before"
            )
            business_before = business_snapshot(connection)
        first = migration_run(rehearsal_copy, arguments.app_commit, ddl_state)
        with connect(rehearsal_copy) as connection:
            first_structure = schema_structure(connection)
            first_fingerprint = schema_fingerprint(connection)
            first_business = business_snapshot(connection)
        second = migration_run(rehearsal_copy, arguments.app_commit, ddl_state)
        with connect(rehearsal_copy) as connection:
            second_structure = schema_structure(connection)
            second_fingerprint = schema_fingerprint(connection)
            second_business = business_snapshot(connection)
            report["integrity_after"] = require_integrity(
                connection, "preflight-after-repeat"
            )
            verify_ledger(connection)
            verify_schema_contract(connection)
            verify_complete_catalog_contract(connection)
        if first_structure != second_structure:
            raise MigrationError("migration repeat changed schema structure")
        if first_business != second_business:
            raise MigrationError("migration repeat changed business aggregates")
        if business_before != first_business:
            raise MigrationError("migration changed business aggregates")

        fresh_first = migration_run(
            fresh_database, arguments.app_commit, ddl_state
        )
        fresh_second = migration_run(
            fresh_database, arguments.app_commit, ddl_state
        )
        with connect(fresh_database) as connection:
            fresh_structure = schema_structure(connection)
            fresh_fingerprint = schema_fingerprint(connection)
            fresh_integrity = require_integrity(connection, "fresh-schema")
        if fresh_structure != second_structure:
            raise MigrationError(
                "fresh schema and upgraded production copy are not structurally equal"
            )
        report.update({
            "status": "passed",
            "business_before": business_before,
            "business_after": second_business,
            "first_run": first,
            "second_run": second,
            "fresh_first_run": fresh_first,
            "fresh_second_run": fresh_second,
            "schema_fingerprint_first": first_fingerprint,
            "schema_fingerprint_second": second_fingerprint,
            "schema_fingerprint_fresh": fresh_fingerprint,
            "schema_parity": True,
            "idempotent": True,
            "fresh_integrity": fresh_integrity,
        })
        write_report(arguments.report, report)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        report["last_ddl"] = ddl_state["last_ddl"]
        write_report(arguments.report, report)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 1


def apply(arguments):
    if not arguments.service_stopped:
        raise MigrationError(
            "production migration requires --service-stopped"
        )
    runtime = ensure_runtime(arguments.expected_sqlite_version)
    scanned = validate_known_sql_compatibility(arguments.source_root)
    ddl_state = {"last_ddl": ""}
    report = {
        "status": "failed",
        "stage": "PRODUCTION MIGRATION",
        "runtime": runtime,
        "app_commit": arguments.app_commit,
        "database": str(Path(arguments.database).resolve()),
        "scanned_sql_files": scanned,
    }
    try:
        with connect(arguments.database) as connection:
            report["integrity_before"] = require_integrity(
                connection, "production-before"
            )
            before = business_snapshot(connection)
        first = migration_run(
            arguments.database, arguments.app_commit, ddl_state
        )
        second = migration_run(
            arguments.database, arguments.app_commit, ddl_state
        )
        with connect(arguments.database) as connection:
            report["integrity_after"] = require_integrity(
                connection, "production-after"
            )
            after = business_snapshot(connection)
            verify_ledger(connection)
            verify_schema_contract(connection)
            verify_complete_catalog_contract(connection)
        if before != after:
            raise MigrationError(
                "production migration changed business aggregates"
            )
        guard = write_runtime_guard(
            arguments.database, arguments.app_commit
        )
        report.update({
            "status": "passed",
            "business_before": before,
            "business_after": after,
            "first_run": first,
            "second_run": second,
            "idempotent": True,
            "runtime_guard": guard,
        })
        write_report(arguments.report, report)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        report["last_ddl"] = ddl_state["last_ddl"]
        write_report(arguments.report, report)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 1


def verify(arguments):
    runtime = ensure_runtime(arguments.expected_sqlite_version)
    guarded = verify_runtime_guard(arguments.database)
    with connect(arguments.database, read_only=True) as connection:
        verify_ledger(connection)
        verify_schema_contract(connection)
        verify_complete_catalog_contract(connection)
        report = {
            "status": "passed",
            "stage": "POST-DEPLOY INTEGRITY",
            "runtime": runtime,
            "runtime_guard": guarded,
            "integrity": require_integrity(connection, "post-deploy"),
            "schema_fingerprint": schema_fingerprint(connection),
            "business": business_snapshot(connection),
        }
    write_report(arguments.report, report)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


def static_check(arguments):
    runtime = ensure_runtime(arguments.expected_sqlite_version)
    paths = validate_known_sql_compatibility(arguments.source_root)
    report = {
        "status": "passed",
        "stage": "SQL COMPATIBILITY",
        "runtime": runtime,
        "scanned_sql_files": paths,
    }
    write_report(arguments.report, report)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


def parser():
    command = argparse.ArgumentParser()
    command.add_argument(
        "mode", choices=("preflight", "apply", "verify", "static-check")
    )
    command.add_argument("--database")
    command.add_argument("--source-root", default=str(PROJECT_ROOT))
    command.add_argument("--app-commit", default="")
    command.add_argument("--expected-sqlite-version", default="")
    command.add_argument("--sqlite-binary", default="sqlite3")
    command.add_argument("--rehearsal-root")
    command.add_argument("--retention-days", type=int, default=7)
    command.add_argument("--report")
    command.add_argument("--service-stopped", action="store_true")
    return command


def main():
    arguments = parser().parse_args()
    if arguments.mode in ("preflight", "apply", "verify") and not arguments.database:
        raise SystemExit("--database is required")
    if arguments.mode == "preflight" and not arguments.rehearsal_root:
        raise SystemExit("--rehearsal-root is required")
    try:
        if arguments.mode == "preflight":
            return preflight(arguments)
        if arguments.mode == "apply":
            return apply(arguments)
        if arguments.mode == "verify":
            return verify(arguments)
        return static_check(arguments)
    except Exception as error:
        print(
            "{}: {}".format(type(error).__name__, error),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
