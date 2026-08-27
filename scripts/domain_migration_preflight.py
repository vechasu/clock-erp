#!/usr/bin/env python3
"""Rehearse, apply and verify auth/orders/tasks migrations on production runtime."""

from __future__ import print_function

import argparse
import json
import multiprocessing
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain_schema_migrations import (  # noqa: E402
    DOMAIN_MIGRATIONS,
    DomainMigrationError,
    apply_domain_migrations,
    domain_snapshot,
    validate_auth_database,
    validate_orders_database,
    validate_tasks_database,
)
from app.schema_migrations import sqlite_backup  # noqa: E402


def _runtime(expected):
    actual = sqlite3.sqlite_version
    if expected and actual != expected:
        raise DomainMigrationError(
            "production SQLite mismatch: expected {}, runtime uses {}".format(
                expected, actual
            )
        )
    return {"python": sys.version.split()[0], "sqlite": actual}


def _write_report(path, report):
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


def _parallel_runner(path, kind, app_commit, queue):
    try:
        queue.put({"ok": True, "result": apply_domain_migrations(
            path, kind, app_commit=app_commit
        )})
    except Exception as error:
        queue.put({
            "ok": False,
            "error": "{}: {}".format(type(error).__name__, error),
        })


def _parallel_pair(path, kind, app_commit):
    queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_parallel_runner,
            args=(str(path), kind, app_commit, queue),
        )
        for unused in range(2)
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=90) for unused in processes]
    for process in processes:
        process.join(90)
        if process.is_alive():
            process.terminate()
            process.join()
            raise DomainMigrationError("parallel migration runner timed out")
    if not all(item.get("ok") for item in results):
        raise DomainMigrationError("parallel migration failed: {}".format(results))
    return results


def _block_network():
    def blocked_connect(unused_socket, unused_address):
        raise OSError("domain migration preflight blocks network egress")

    def blocked_getaddrinfo(*unused_args, **unused_kwargs):
        raise OSError("domain migration preflight blocks DNS egress")

    socket.socket.connect = blocked_connect
    socket.getaddrinfo = blocked_getaddrinfo


def preflight(arguments):
    runtime = _runtime(arguments.expected_sqlite_version)
    _block_network()
    root = Path(arguments.rehearsal_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(str(root), 0o700)
    run_directory = Path(tempfile.mkdtemp(
        prefix="domain-rehearsal-{}-".format(arguments.app_commit[:12] or "unknown"),
        dir=str(root),
    ))
    os.chmod(str(run_directory), 0o700)
    report = {
        "status": "failed",
        "stage": "DOMAIN MIGRATION PREFLIGHT",
        "runtime": runtime,
        "app_commit": arguments.app_commit,
        "run_directory": str(run_directory),
        "network_egress": 0,
        "migrations": DOMAIN_MIGRATIONS,
    }
    try:
        for kind, source in (
            ("auth", Path(arguments.auth_database).resolve()),
            ("orders", Path(arguments.orders_database).resolve()),
            ("tasks", Path(arguments.tasks_database).resolve()),
        ):
            original = run_directory / "{}-original.db".format(kind)
            sequential = run_directory / "{}-sequential.db".format(kind)
            parallel = run_directory / "{}-parallel.db".format(kind)
            fresh = run_directory / "{}-fresh.db".format(kind)
            if source.is_file():
                sqlite_backup(source, original, sqlite_binary=arguments.sqlite_binary)
                shutil.copy2(str(original), str(sequential))
                shutil.copy2(str(original), str(parallel))
                before = domain_snapshot(original, kind)
            else:
                before = {"kind": kind, "latest_migration": None,
                          "checksum": None, "schema_fingerprint": None,
                          "business_counts": {"tasks": 0}}
            first = apply_domain_migrations(
                sequential, kind, app_commit=arguments.app_commit
            )
            second = apply_domain_migrations(
                sequential, kind, app_commit=arguments.app_commit
            )
            after = domain_snapshot(sequential, kind)
            concurrent = _parallel_pair(parallel, kind, arguments.app_commit)
            parallel_after = domain_snapshot(parallel, kind)
            fresh_first = apply_domain_migrations(
                fresh, kind, app_commit=arguments.app_commit
            )
            fresh_second = apply_domain_migrations(
                fresh, kind, app_commit=arguments.app_commit
            )
            if before["business_counts"] != after["business_counts"]:
                raise DomainMigrationError(
                    "{} migration changed business counts".format(kind)
                )
            if after["business_counts"] != parallel_after["business_counts"]:
                raise DomainMigrationError(
                    "{} parallel migration changed business counts".format(kind)
                )
            if first["schema_fingerprint"] != second["schema_fingerprint"]:
                raise DomainMigrationError("{} repeat changed schema".format(kind))
            if first["schema_fingerprint"] != fresh_first["schema_fingerprint"]:
                raise DomainMigrationError(
                    "{} fresh/upgraded semantic schema mismatch".format(kind)
                )
            if fresh_first != fresh_second:
                raise DomainMigrationError("{} fresh repeat changed report".format(kind))
            report[kind] = {
                "before": before,
                "first": first,
                "second": second,
                "after": after,
                "parallel": concurrent,
                "parallel_after": parallel_after,
                "fresh": fresh_first,
                "schema_parity": True,
                "business_data_unchanged": True,
            }
        report["status"] = "passed"
        _write_report(arguments.report, report)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        _write_report(arguments.report, report)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 1


def apply(arguments):
    if not arguments.service_stopped:
        raise DomainMigrationError("domain migrations require --service-stopped")
    runtime = _runtime(arguments.expected_sqlite_version)
    before = {
        "auth": domain_snapshot(arguments.auth_database, "auth"),
        "orders": domain_snapshot(arguments.orders_database, "orders"),
        "tasks": (domain_snapshot(arguments.tasks_database, "tasks")
                  if Path(arguments.tasks_database).is_file() else
                  {"kind": "tasks", "latest_migration": None, "checksum": None,
                   "schema_fingerprint": None, "business_counts": {"tasks": 0}}),
    }
    first = {
        "auth": apply_domain_migrations(
            arguments.auth_database, "auth", arguments.app_commit
        ),
        "orders": apply_domain_migrations(
            arguments.orders_database, "orders", arguments.app_commit
        ),
        "tasks": apply_domain_migrations(
            arguments.tasks_database, "tasks", arguments.app_commit
        ),
    }
    second = {
        "auth": apply_domain_migrations(
            arguments.auth_database, "auth", arguments.app_commit
        ),
        "orders": apply_domain_migrations(
            arguments.orders_database, "orders", arguments.app_commit
        ),
        "tasks": apply_domain_migrations(
            arguments.tasks_database, "tasks", arguments.app_commit
        ),
    }
    after = {
        "auth": domain_snapshot(arguments.auth_database, "auth"),
        "orders": domain_snapshot(arguments.orders_database, "orders"),
        "tasks": domain_snapshot(arguments.tasks_database, "tasks"),
    }
    for kind in ("auth", "orders", "tasks"):
        if before[kind]["business_counts"] != after[kind]["business_counts"]:
            raise DomainMigrationError(
                "production {} migration changed business counts".format(kind)
            )
    report = {
        "status": "passed", "stage": "DOMAIN PRODUCTION MIGRATION",
        "runtime": runtime, "before": before, "first": first,
        "second": second, "after": after, "idempotent": True,
    }
    _write_report(arguments.report, report)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


def verify(arguments):
    runtime = _runtime(arguments.expected_sqlite_version)
    validate_auth_database(arguments.auth_database)
    validate_orders_database(arguments.orders_database)
    validate_tasks_database(arguments.tasks_database)
    report = {
        "status": "passed", "stage": "DOMAIN POST-DEPLOY INTEGRITY",
        "runtime": runtime,
        "auth": domain_snapshot(arguments.auth_database, "auth"),
        "orders": domain_snapshot(arguments.orders_database, "orders"),
        "tasks": domain_snapshot(arguments.tasks_database, "tasks"),
    }
    _write_report(arguments.report, report)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


def parser():
    command = argparse.ArgumentParser()
    command.add_argument("mode", choices=("preflight", "apply", "verify"))
    command.add_argument("--auth-database", required=True)
    command.add_argument("--orders-database", required=True)
    command.add_argument("--tasks-database", required=True)
    command.add_argument("--app-commit", default="")
    command.add_argument("--expected-sqlite-version", default="")
    command.add_argument("--sqlite-binary", default="sqlite3")
    command.add_argument("--rehearsal-root")
    command.add_argument("--report")
    command.add_argument("--service-stopped", action="store_true")
    return command


def main():
    arguments = parser().parse_args()
    if arguments.mode == "preflight" and not arguments.rehearsal_root:
        raise SystemExit("--rehearsal-root is required")
    try:
        if arguments.mode == "preflight":
            return preflight(arguments)
        if arguments.mode == "apply":
            return apply(arguments)
        return verify(arguments)
    except Exception as error:
        print("{}: {}".format(type(error).__name__, error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
