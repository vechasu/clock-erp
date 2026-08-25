#!/usr/bin/env python3
"""Run the unittest suite with external network name resolution disabled."""

from __future__ import print_function

import argparse
import ipaddress
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

for secret_name in (
    "MOYSKLAD_TOKEN",
    "BITRIX_LOGIN",
    "BITRIX_PASSWORD",
    "BITRIX_CATALOG_TOKEN",
    "BITRIX_ORDERS_TOKEN",
    "BITRIX_ORDER_COMMENTS_TOKEN",
    "UPDATE_ORDER_STATUS_TOKEN",
    "WB_API_TOKEN",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
):
    os.environ[secret_name] = ""
os.environ["ERP_TEST_MODE"] = "1"
for proxy_name in (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
):
    os.environ.pop(proxy_name, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain_schema_migrations import apply_domain_migrations  # noqa: E402
from app.catalog_db import CatalogDatabase  # noqa: E402
from app.schema_migrations import apply_migrations  # noqa: E402


ORIGINAL_GETADDRINFO = socket.getaddrinfo
ORIGINAL_SOCKET_CONNECT = socket.socket.connect
ORIGINAL_CREATE_CONNECTION = socket.create_connection
ORIGINAL_POPEN = subprocess.Popen
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


def _address_host(address):
    if isinstance(address, tuple) and address:
        return address[0]
    return None


def guarded_socket_connect(sock, address):
    host = _address_host(address)
    if host is not None and not local_host(host):
        raise OSError("external network disabled during backend tests")
    return ORIGINAL_SOCKET_CONNECT(sock, address)


def guarded_create_connection(address, *args, **kwargs):
    host = _address_host(address)
    if host is not None and not local_host(host):
        raise OSError("external network disabled during backend tests")
    return ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


def guarded_popen(args, *pargs, **kwargs):
    command = args if isinstance(args, (list, tuple)) else [args]
    executable = Path(str(command[0] or "")).name.casefold() if command else ""
    if executable in {"curl", "wget"}:
        raise OSError("external network subprocess disabled during backend tests")
    return ORIGINAL_POPEN(args, *pargs, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--verbosity", type=int, default=2)
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="vechasu-backend-tests-") as root:
        test_root = Path(root).resolve()
        catalog_path = test_root / "catalog.db"
        auth_path = test_root / "auth.db"
        orders_path = test_root / "orders.db"
        isolated_environment = {
            "CATALOG_DATABASE_PATH": str(catalog_path),
            "ERP_AUTH_DATABASE": str(auth_path),
            "ORDERS_DATABASE_PATH": str(orders_path),
            "ERP_SECRET_KEY": "isolated-backend-test-secret",
            "ERP_TEST_ROOT": str(test_root),
        }
        previous_environment = {
            name: os.environ.get(name) for name in isolated_environment
        }
        os.environ.update(isolated_environment)

        socket.getaddrinfo = guarded_getaddrinfo
        socket.socket.connect = guarded_socket_connect
        socket.create_connection = guarded_create_connection
        subprocess.Popen = guarded_popen
        CatalogDatabase.initialize = initialize_test_catalog
        if __name__ == "__main__":
            sys.modules["scripts.run_backend_tests"] = sys.modules[__name__]
        try:
            apply_migrations(catalog_path, app_commit="test-suite")
            apply_domain_migrations(auth_path, "auth", "test-suite")
            apply_domain_migrations(orders_path, "orders", "test-suite")
            suite = unittest.defaultTestLoader.discover(
                arguments.start_directory,
                pattern=arguments.pattern,
            )
            result = unittest.TextTestRunner(
                verbosity=arguments.verbosity,
            ).run(suite)
            return 0 if result.wasSuccessful() else 1
        finally:
            socket.getaddrinfo = ORIGINAL_GETADDRINFO
            socket.socket.connect = ORIGINAL_SOCKET_CONNECT
            socket.create_connection = ORIGINAL_CREATE_CONNECTION
            subprocess.Popen = ORIGINAL_POPEN
            CatalogDatabase.initialize = ORIGINAL_CATALOG_INITIALIZE
            for name, value in previous_environment.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
