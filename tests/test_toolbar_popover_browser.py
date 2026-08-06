import os
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


class ToolbarPopoverStructureTest(unittest.TestCase):
    def source(self, relative_path):
        return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    def test_products_and_receipts_share_exclusive_popover_coordinator(self):
        coordinator = self.source(
            "app/static/js/toolbar-popover-coordinator.js"
        )
        warehouse = self.source("app/templates/warehouse.html")
        receipts = self.source("app/templates/receipts.html")

        self.assertIn("closeAll(entry, false)", coordinator)
        self.assertIn('event.key !== "Escape"', coordinator)
        self.assertIn("entry.container.contains(event.target)", coordinator)
        for source in (warehouse, receipts):
            self.assertIn("js/toolbar-popover-coordinator.js", source)
            self.assertIn("createErpToolbarPopoverCoordinator", source)

    def test_sales_reference_manager_and_modal_lock_are_unchanged(self):
        sales = self.source("app/templates/sales.html")
        modal_shell = self.source("app/static/js/erp-modal-shell.js")
        receipts = self.source("app/templates/receipts.html")

        self.assertIn("activeEntry?.api.close()", sales)
        self.assertIn("salesPopoverManager.register", sales)
        self.assertIn("data-erp-modal-lock", modal_shell)
        self.assertIn("event.stopImmediatePropagation()", modal_shell)
        self.assertNotIn(
            "receipt-report-button",
            receipts.split(
                "createErpToolbarPopoverCoordinator([", 1
            )[1].split("])", 1)[0],
        )

    def test_sales_channel_selection_has_neutral_all_state(self):
        sales = self.source("app/templates/sales.html")

        self.assertIn('id="saleFormFields"', sales)
        self.assertIn("function clearSaleFormSource()", sales)
        self.assertIn(
            '"Выберите канал, в который будет добавлена продажа."',
            sales,
        )
        self.assertIn(
            '"Добавить продажу в " + sourceLabels[sourceKey]',
            sales,
        )
        self.assertNotIn(
            'localStorage.getItem(\n                    "vechasu-sales-last-source"',
            sales,
        )


class ToolbarPopoverBrowserTest(unittest.TestCase):
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

    def run_chrome(self, chrome, url, width, height, marker):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    f"--user-data-dir={Path(temp) / 'profile'}",
                    f"--window-size={width},{height}",
                    "--virtual-time-budget=3500",
                    "--dump-dom",
                    url,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=35,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn(marker, result.stdout)

    def test_toolbar_popovers_and_locked_modals(self):
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
            ready_url = (
                f"http://127.0.0.1:{port}"
                "/app/products?toolbar_popover_e2e=1"
            )
            for _attempt in range(100):
                try:
                    with urllib.request.urlopen(
                        ready_url, timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Stage 2 preview server did not start")

            paths = (
                "/app/products?toolbar_popover_e2e=1"
                "&brand=Casio&q=Casio&sort_by=stock",
                "/app/receipts?toolbar_popover_e2e=1",
            )
            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for path in paths:
                    with self.subTest(width=width, path=path):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}{path}",
                            width,
                            height,
                            'data-toolbar-popover-e2e="pass"',
                        )

            modal_paths = (
                (
                    "/app/sales?sales_modal_e2e=1",
                    'data-sales-modal-e2e="pass"',
                ),
                (
                    "/app/receipts?receipts_modal_e2e=1",
                    'data-receipts-modal-e2e="pass"',
                ),
            )
            for path, marker in modal_paths:
                with self.subTest(path=path, width=1440):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}{path}",
                        1440,
                        900,
                        marker,
                    )
            for width, height in ((390, 844), (320, 568)):
                with self.subTest(path="sales", width=width):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}"
                        "/app/sales?sales_modal_e2e=1",
                        width,
                        height,
                        'data-sales-modal-e2e="pass"',
                    )

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for source in ("all", "tictactoy", "wildberries", "amazon"):
                    with self.subTest(
                        path="sales-article",
                        source=source,
                        width=width,
                    ):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}/app/sales"
                            f"?source={source}&sales_article_e2e=1",
                            width,
                            height,
                            'data-sales-article-e2e="pass"',
                        )

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for source in ("all", "tictactoy", "wildberries", "amazon"):
                    with self.subTest(
                        path="sales-channel",
                        source=source,
                        width=width,
                    ):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}/app/sales"
                            f"?source={source}&sales_channel_e2e=1",
                            width,
                            height,
                            'data-sales-channel-e2e="pass"',
                        )

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                with self.subTest(path="sales-filters", width=width):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}/app/sales"
                        "?source=all&brand_id=snapshot%3Abrand%3Acasio"
                        "&sales_filters_e2e=1",
                        width,
                        height,
                        'data-sales-filters-e2e="pass"',
                    )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
