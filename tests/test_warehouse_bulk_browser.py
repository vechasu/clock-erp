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
        "brand": "AARK",
        "category": "Наручные часы",
        "cell": "",
        "stock": 1,
        "stock_display": "1",
        "reserve": 0,
        "quantity": 1,
        "created_at": 1,
        "created_at_display": "01.01.2026",
        "has_images": False,
        "thumbnail_url": "",
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
            browser_test_item("1001", "Часы Alpha", "A-1"),
            browser_test_item("1002", "Часы Beta", "B-1"),
        ]
        original_testing = web.app.testing
        web.app.testing = True
        server = make_server("127.0.0.1", 0, web.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)

        try:
            with mock.patch.object(
                web, "get_excel_warehouse_items", return_value=items
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
                result = subprocess.run(
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
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            web.app.testing = original_testing

        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn('data-bulk-ui-e2e="pass"', result.stdout)
        self.assertIn("BULK_EDIT_UI_BUILD_PENDING", result.stdout)
        self.assertIn("BULK_FIELDS_RENDERED", result.stdout)
        self.assertIn(">Бренд<", result.stdout)
        self.assertIn(">Категория<", result.stdout)
        self.assertIn(">Применить<", result.stdout)
