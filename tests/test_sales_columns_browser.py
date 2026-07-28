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


def browser_sale(sale_id, source, order_number, price):
    return {
        "id": sale_id,
        "sale_type": "manual",
        "sale_type_label": "Ручная",
        "is_manual": True,
        "created_at": "2026-07-22",
        "source": source,
        "source_key": web.normalize_sales_source_key(source),
        "barcode": f"BARCODE-{sale_id}",
        "brand": "Brand",
        "category": "Коллекция",
        "product_id": sale_id,
        "product_name": f"Часы {sale_id}",
        "quantity_value": 1,
        "quantity_display": "1",
        "unit_price": price,
        "unit_price_display": f"{price} ₽",
        "total_amount": price,
        "total_amount_display": f"{price} ₽",
        "order_number": order_number,
        "track_number": f"TRACK-{sale_id}",
        "delivery_method": "",
        "delivery_cost": 0,
        "delivery_cost_display": "",
        "region": "",
        "city": "",
        "payment_method": "",
        "recipient_name": "",
        "platform": "",
        "country": "",
        "delivery_address": "",
        "invoice_number": "",
        "note": "",
        "order_status": "completed",
        "order_status_label": "Завершён",
        "is_cancelled": False,
        "cancelled_at": "",
        "sticker_number": "",
        "commission_amount": 0,
        "commission_display": "0 ₽",
        "search_text": "",
    }


class SalesColumnsBrowserTest(unittest.TestCase):
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
        records = [
            browser_sale("one", "Tictactoy", "ORDER-2", 200),
            browser_sale("two", "Wildberries", "ORDER-1", 100),
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
                return_value=records,
            ), mock.patch.object(
                web,
                "get_warehouse_items",
                return_value=[],
            ), tempfile.TemporaryDirectory() as profile:
                thread.start()
                url = (
                    f"http://127.0.0.1:{server.server_port}"
                    "/sales?source=all&sales_columns_e2e=1"
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
                        "--virtual-time-budget=6000",
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
            'data-sales-columns-e2e="pass"',
            result.stdout,
            result.stdout[-2000:],
        )

    def test_column_preferences_restore_on_desktop_and_mobile(self):
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
                (1440, 900),
                (1024, 900),
                (768, 900),
                (430, 932),
                (390, 844),
                (375, 812),
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
