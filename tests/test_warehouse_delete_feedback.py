import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock

from app import web


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WarehouseDeleteFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)

    @mock.patch.object(web, "ExcelProductCatalog")
    def test_delete_uses_danger_feedback_without_changing_success_flows(
        self, catalog_class
    ):
        ajax_response = self.client.post(
            "/warehouse/archive",
            data={"product_id": "42"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        redirect_response = self.client.post(
            "/warehouse/archive",
            data={"product_id": "43"},
        )

        self.assertEqual(
            ajax_response.get_json(),
            {"ok": True, "message": "Товар удалён"},
        )
        self.assertIn("notice=danger", redirect_response.location)
        redirect_query = parse_qs(urlparse(redirect_response.location).query)
        self.assertEqual(redirect_query["message"], ["Товар удалён"])
        self.assertEqual(catalog_class.return_value.delete_product.call_count, 2)

        source = (PROJECT_ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('.notice-error,\n        .notice-danger {', source)
        self.assertIn('notice.className = "notice notice-" + variant', source)
        self.assertIn('data.message || "Товар удалён"', source)
        self.assertIn('"danger"', source)
        self.assertIn('notice.querySelector(".notice-icon")', source)
        self.assertIn("row.remove();", source)
        self.assertIn('target.searchParams.set("notice", "success")', source)


class WarehouseDeleteFeedbackBrowserTest(unittest.TestCase):
    @staticmethod
    def find_chrome():
        candidates = (
            os.environ.get("CHROME_BIN"),
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        return next(
            (
                candidate
                for candidate in candidates
                if candidate and Path(candidate).is_file()
            ),
            None,
        )

    def test_feedback_and_row_removal_at_required_viewports(self):
        if sys.platform == "darwin":
            self.skipTest("macOS Chrome does not reliably exit after --dump-dom")
        chrome = self.find_chrome()
        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        environment = dict(os.environ, PREVIEW_PORT=str(port))
        server = subprocess.Popen(
            [sys.executable, "tests/stage2_preview_server.py"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = (
            "http://127.0.0.1:{}/app/products"
            "?delete_feedback_e2e=1"
        ).format(port)
        try:
            for _attempt in range(100):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Stage 2 preview server did not start")

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                with self.subTest(width=width), \
                        tempfile.TemporaryDirectory() as profile:
                    result = subprocess.run(
                        [
                            chrome,
                            "--headless=new",
                            "--no-sandbox",
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            "--user-data-dir={}".format(profile),
                            "--window-size={},{}".format(width, height),
                            "--virtual-time-budget=4000",
                            "--dump-dom",
                            url,
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=35,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                    if (
                        'data-warehouse-delete-feedback-e2e="pass"'
                        not in result.stdout
                    ):
                        error = re.search(
                            r'data-warehouse-delete-feedback-e2e-error="([^"]*)"',
                            result.stdout,
                        )
                        self.fail(error.group(1) if error else result.stderr[-2000:])
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    unittest.main()
