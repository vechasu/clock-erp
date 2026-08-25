import os
import socket
import subprocess
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import requests

from scripts import run_backend_tests


class NoEgressGuardTests(unittest.TestCase):
    def test_direct_external_dns_socket_and_connection_are_blocked(self):
        with self.assertRaisesRegex(OSError, "external network disabled"):
            run_backend_tests.guarded_getaddrinfo("example.com", 443)
        with mock.patch.object(
            run_backend_tests, "ORIGINAL_SOCKET_CONNECT"
        ) as real_connect:
            with self.assertRaisesRegex(OSError, "external network disabled"):
                run_backend_tests.guarded_socket_connect(
                    mock.Mock(), ("93.184.216.34", 443)
                )
            real_connect.assert_not_called()
        with mock.patch.object(
            run_backend_tests, "ORIGINAL_CREATE_CONNECTION"
        ) as real_create:
            with self.assertRaisesRegex(OSError, "external network disabled"):
                run_backend_tests.guarded_create_connection(
                    ("93.184.216.34", 443)
                )
            real_create.assert_not_called()

    def test_loopback_and_unix_socket_paths_are_allowed(self):
        with mock.patch.object(
            run_backend_tests, "ORIGINAL_SOCKET_CONNECT", return_value="ok"
        ) as real_connect:
            self.assertEqual(
                run_backend_tests.guarded_socket_connect(
                    mock.Mock(), ("127.0.0.1", 5000)
                ),
                "ok",
            )
            self.assertEqual(
                run_backend_tests.guarded_socket_connect(
                    mock.Mock(), "/tmp/test.sock"
                ),
                "ok",
            )
            self.assertEqual(real_connect.call_count, 2)

    def test_curl_and_wget_subprocesses_are_blocked(self):
        for command in (["curl", "https://example.com"], ["/usr/bin/wget", "x"]):
            with self.assertRaisesRegex(OSError, "subprocess disabled"):
                run_backend_tests.guarded_popen(command)

    def test_requests_and_urllib_are_blocked_at_socket_boundary(self):
        with self.assertRaises(requests.RequestException):
            requests.get("http://93.184.216.34/", timeout=0.1)
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen("http://93.184.216.34/", timeout=0.1)

    def test_production_credentials_are_cleared_by_runner(self):
        for name in (
            "MOYSKLAD_TOKEN",
            "BITRIX_CATALOG_TOKEN",
            "WB_API_TOKEN",
            "SMTP_PASSWORD",
        ):
            self.assertEqual(os.environ.get(name), "")
        self.assertEqual(os.environ.get("ERP_TEST_MODE"), "1")

    def test_default_databases_are_isolated_under_temporary_root(self):
        test_root = Path(os.environ["ERP_TEST_ROOT"]).resolve()
        project_instance = (
            Path(run_backend_tests.PROJECT_ROOT) / "instance"
        ).resolve()
        for name in (
            "CATALOG_DATABASE_PATH",
            "ERP_AUTH_DATABASE",
            "ORDERS_DATABASE_PATH",
        ):
            database_path = Path(os.environ[name]).resolve()
            self.assertEqual(database_path.parent, test_root)
            self.assertNotEqual(database_path.parent, project_instance)
            self.assertTrue(database_path.is_file())


if __name__ == "__main__":
    unittest.main()
