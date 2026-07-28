import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.serving import make_server

from app import web


def catalog_item(
    item_id,
    name,
    article,
    barcode,
    brand,
    category,
):
    return {
        "id": item_id,
        "name": name,
        "article": article,
        "barcode": barcode,
        "brand": brand,
        "category": category,
        "stock": 3,
        "stock_display": "3",
    }


class SalesCatalogPickerBrowserTest(unittest.TestCase):
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
                if candidate and Path(candidate).is_file()
            ),
            None,
        )

    def run_browser_check(self, chrome, width, height):
        items = [
            catalog_item(
                "catalog-one",
                "Alpha One",
                "ARTICLE-ONE",
                "BAR-ONE",
                "Alpha",
                "Часы/Мужские",
            ),
            catalog_item(
                "catalog-two",
                "Alpha Strap",
                "ARTICLE-STRAP",
                "BAR-STRAP",
                "Alpha",
                "Ремни",
            ),
            catalog_item(
                "catalog-three",
                "Beta One",
                "ARTICLE-BETA",
                "BAR-BETA",
                "Beta",
                "Часы/Мужские",
            ),
        ]
        server = make_server("127.0.0.1", 0, web.app)
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )

        try:
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
                return_value=items,
            ), tempfile.TemporaryDirectory() as profile:
                thread.start()
                url = (
                    f"http://127.0.0.1:{server.server_port}"
                    "/sales?source=all"
                    "&sales_catalog_picker_e2e=1"
                )
                result = subprocess.run(
                    [
                        chrome,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        f"--user-data-dir={profile}",
                        f"--window-size={width},{height}",
                        "--virtual-time-budget=5000",
                        "--dump-dom",
                        url,
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
            'data-sales-catalog-picker-e2e="pass"',
            result.stdout,
            result.stdout[-2000:],
        )

    def test_catalog_picker_on_desktop_and_mobile(self):
        if sys.platform == "darwin":
            self.skipTest(
                "macOS Chrome does not reliably exit after --dump-dom"
            )

        chrome = self.find_chrome()

        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        original_testing = web.app.testing
        web.app.testing = True

        try:
            for width, height in (
                (1280, 900),
                (390, 844),
                (320, 700),
            ):
                with self.subTest(width=width):
                    self.run_browser_check(
                        chrome,
                        width,
                        height,
                    )
        finally:
            web.app.testing = original_testing


if __name__ == "__main__":
    unittest.main()
