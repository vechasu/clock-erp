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


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LivePrefixSearchStructureTest(unittest.TestCase):
    def test_products_sales_and_receipts_enable_shared_prefix_search(self):
        for template_name, ids in (
            ("warehouse.html", ("addBrandCombobox", "addCategoryCombobox", "addProductCombobox")),
            ("sales.html", ("saleBrand", "saleCategory", "saleProduct")),
            ("receipts.html", ("receiptBrand", "receiptCategory", "receiptProduct")),
        ):
            source = (PROJECT_ROOT / "app/templates" / template_name).read_text(
                encoding="utf-8"
            )
            for component_id in ids:
                block = source.split('"{}"'.format(component_id), 1)[1].split(
                    ") }}", 1
                )[0]
                self.assertIn("prefix_search=true", block)

    def test_warehouse_has_immediate_prefix_filter_and_stale_protection(self):
        source = (PROJECT_ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("function normalizeWarehouseSearchValue", source)
        self.assertIn(".startsWith(query)", source)
        self.assertIn("restoreWarehouseSearchSnapshot(immediateQuery)", source)
        self.assertIn("requestId !== warehouseSearchRequestId", source)
        self.assertIn('data-barcode="{{ (item.barcode or \'\')|lower|e }}"', source)


class LivePrefixSearchBrowserTest(unittest.TestCase):
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
            (candidate for candidate in candidates if candidate and Path(candidate).is_file()),
            None,
        )

    def test_live_prefix_search_at_desktop_and_mobile_widths(self):
        if sys.platform == "darwin":
            self.skipTest(
                "macOS Chrome does not reliably exit after --dump-dom"
            )
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
        try:
            url = (
                "http://127.0.0.1:{}/app/products"
                "?live_search_e2e=1&per_page=200"
            ).format(port)
            for _attempt in range(300):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Preview server did not start")

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                with self.subTest(width=width), tempfile.TemporaryDirectory() as profile:
                    completed = subprocess.run(
                        [
                            chrome,
                            "--headless=new",
                            "--no-sandbox",
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            "--disable-background-networking",
                            "--no-first-run",
                            "--user-data-dir={}".format(profile),
                            "--window-size={},{}".format(width, height),
                            "--virtual-time-budget=1800",
                            "--dump-dom",
                            url,
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=35,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
                    if 'data-live-search-e2e="pass"' not in completed.stdout:
                        error = re.search(
                            r'data-live-search-e2e-(?:error|result)="([^"]*)"',
                            completed.stdout,
                        )
                        self.fail(error.group(1) if error else completed.stderr[-2000:])
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
