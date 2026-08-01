import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.serving import make_server

from app import web


def browser_test_item(item_id, name, article):
    return {
        "id": item_id,
        "name": name,
        "article": article,
        "code": article,
        "barcode": "460000000001",
        "moysklad_product_id": "",
        "brand": "AARK",
        "category": "Наручные часы",
        "brand_id": 1,
        "category_id": 11,
        "cell": "",
        "stock": 1,
        "stock_display": "1",
        "reserve": 0,
        "quantity": 1,
        "created_at": 1,
        "created_at_display": "01.01.2026",
        "has_images": False,
        "thumbnail_url": "",
        "gallery": [],
        "price_display": "",
        "source_url": "",
        "match_status": "not_found",
        "updated_at": "2026-01-01T00:00:00Z",
        "cell_source": "product",
        "cell_source_label": "у позиции",
        "cell_source_path": "",
        "moysklad_url": "#",
        "raw_category": "Наручные часы",
    }


class WarehouseBulkBrowserTest(unittest.TestCase):
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

    def test_user_can_open_bulk_brand_and_category_form(self):
        chrome = self.find_chrome()
        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        items = [
            browser_test_item(1001, "Часы Alpha", "A-1"),
            browser_test_item(1002, "Часы Beta", "B-1"),
        ]
        original_testing = web.app.testing
        web.app.testing = True
        server = make_server("127.0.0.1", 0, web.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)

        try:
            with mock.patch.object(
                web, "serialize_api_product", side_effect=lambda item: item
            ), mock.patch.object(
                web.ExcelProductCatalog,
                "list_products",
                return_value={
                    "items": items,
                    "total": 2,
                    "page": 1,
                    "per_page": 50,
                    "pages": 1,
                    "brand_groups": [{"name": "AARK", "count": 2}],
                    "category_groups": [
                        {"name": "Наручные часы", "count": 2},
                    ],
                    "cell_groups": [],
                    "stats": {
                        "positions": 2,
                        "total_stock": 2,
                        "positive_positions": 2,
                        "zero_positions": 0,
                    },
                },
            ), mock.patch.object(
                web.ExcelProductCatalog,
                "list_manual_stock_operations",
                return_value=[],
            ), tempfile.TemporaryDirectory() as profile:
                thread.start()
                url = (
                    f"http://127.0.0.1:{server.server_port}"
                    "/warehouse?bulk_ui_e2e=1"
                )
                process = subprocess.Popen(
                    [
                        chrome,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        f"--user-data-dir={profile}",
                        "--virtual-time-budget=3000",
                        "--dump-dom",
                        url,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    stdout, stderr = process.communicate(timeout=15)
                except subprocess.TimeoutExpired as error:
                    process.terminate()
                    trailing_stdout, trailing_stderr = process.communicate(
                        timeout=5
                    )
                    timed_out_stdout = error.stdout or ""
                    timed_out_stderr = error.stderr or ""
                    if isinstance(timed_out_stdout, bytes):
                        timed_out_stdout = timed_out_stdout.decode(
                            "utf-8", errors="replace"
                        )
                    if isinstance(timed_out_stderr, bytes):
                        timed_out_stderr = timed_out_stderr.decode(
                            "utf-8", errors="replace"
                        )
                    stdout = timed_out_stdout + trailing_stdout
                    stderr = timed_out_stderr + trailing_stderr
                returncode = process.returncode
        finally:
            server.shutdown()
            thread.join(timeout=5)
            web.app.testing = original_testing

        self.assertIn(
            returncode,
            (0, -15),
            stderr[-2000:],
        )
        self.assertIn(">Часы Alpha<", stdout)
        self.assertIn(">Часы Beta<", stdout)
        self.assertIn(">Выбрать текущую страницу<", stdout)
        self.assertIn('aria-label="Выбрать Часы Alpha"', stdout)
        self.assertNotIn("Не удалось загрузить товары", stdout)
