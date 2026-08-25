#!/usr/bin/env python3
"""Run the unittest suite with external network name resolution disabled."""

from __future__ import print_function

import argparse
import ipaddress
import socket
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain_schema_migrations import apply_domain_migrations  # noqa: E402
from app.catalog_db import CatalogDatabase  # noqa: E402
from app.schema_migrations import apply_migrations  # noqa: E402


ORIGINAL_GETADDRINFO = socket.getaddrinfo
ORIGINAL_CATALOG_INITIALIZE = CatalogDatabase.initialize


def initialize_test_catalog(database, allow_schema_changes=False):
    """Explicit test harness migration; production runtime stays read-only."""
    if str(database.path) == ":memory:":
        raise RuntimeError("tests must use a file-backed migrated catalog database")
    path = Path(database.path).resolve()
    needs_migration = not path.is_file()
    if not needs_migration:
        try:
            import sqlite3
            connection = sqlite3.connect(str(path))
            try:
                needs_migration = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='erp_migration_ledger'"
                ).fetchone() is None
            finally:
                connection.close()
        except sqlite3.Error:
            needs_migration = False
    if needs_migration:
        apply_migrations(path, app_commit="test-suite")
    return ORIGINAL_CATALOG_INITIALIZE(database)


def local_host(host):
    value = str(host or "").strip().strip("[]")
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def guarded_getaddrinfo(host, *args, **kwargs):
    if not local_host(host):
        raise OSError("external network disabled during backend tests")
    return ORIGINAL_GETADDRINFO(host, *args, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--verbosity", type=int, default=2)
    arguments = parser.parse_args()
    socket.getaddrinfo = guarded_getaddrinfo
    CatalogDatabase.initialize = initialize_test_catalog
    apply_domain_migrations(PROJECT_ROOT / "instance" / "auth.db", "auth", "test-suite")
    apply_domain_migrations(PROJECT_ROOT / "instance" / "orders.db", "orders", "test-suite")
    suite = unittest.defaultTestLoader.discover(
        arguments.start_directory,
        pattern=arguments.pattern,
    )
    result = unittest.TextTestRunner(
        verbosity=arguments.verbosity,
    ).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
