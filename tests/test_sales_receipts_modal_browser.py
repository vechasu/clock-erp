import os
import shutil
import subprocess
import threading
import unittest
from unittest import mock

from werkzeug.serving import make_server

from app import web


class SalesReceiptsModalBrowserTest(unittest.TestCase):
    def find_chrome(self):
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
                if candidate and os.path.isfile(candidate)
            ),
            None,
        )

    def run_browser_check(self, chrome, width, height, url, marker):
        server = make_server("127.0.0.1", 0, web.app)
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )

        try:
            thread.start()
            full_url = f"http://127.0.0.1:{server.server_port}{url}"
            result = subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    f"--window-size={width},{height}",
                    "--virtual-time-budget=6000",
                    "--dump-dom",
                    full_url,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn(
            f'data-{marker}="pass"',
            result.stdout,
            result.stdout[-2000:],
        )

    def test_sales_and_receipt_modal_behaviors_desktop_and_mobile(
        self,
    ):
        chrome = self.find_chrome()

        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        original_testing = web.app.testing
        web.app.testing = True

        try:
            for width, height in (
                (1280, 900),
                (390, 844),
            ):
                with self.subTest(width=width, height=height):
                    with mock.patch.object(
                        web,
                        "build_sales_report_records",
                        return_value=[],
                    ), mock.patch.object(
                        web,
                        "get_warehouse_items",
                        return_value=[],
                    ), mock.patch.object(
                        web,
                        "get_excel_warehouse_items",
                        return_value=[],
                    ):
                        self.run_browser_check(
                            chrome,
                            width,
                            height,
                            "/sales?source=all&sales_modal_e2e=1",
                            "sales-modal-e2e",
                        )

                    with mock.patch.object(
                        web,
                        "load_receipts",
                        return_value=[],
                    ), mock.patch.object(
                        web,
                        "get_warehouse_items",
                        return_value=[],
                    ):
                        self.run_browser_check(
                            chrome,
                            width,
                            height,
                            "/receipts?receipts_modal_e2e=1",
                            "receipts-modal-e2e",
                        )
        finally:
            web.app.testing = original_testing


if __name__ == "__main__":
    unittest.main()
