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


class ReceiptCardBrowserTest(unittest.TestCase):
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

    @staticmethod
    def long_receipt():
        return {
            "id": "receipt-layout-e2e",
            "number": "ДОКУМЕНТ-" + "1234567890" * 10,
            "receipt_date": "2026-08-04",
            "created_at": "2026-08-04 14:37",
            "note": "Первая строка длинного комментария\n"
            + "Вторая строка с подробным описанием поставки " * 4,
            "brand": "Очень длинное название бренда " * 4,
            "category": "Очень длинное название категории " * 4,
            "product_id": "9133",
            "product_name": "Очень длинное полное название товара " * 8,
            "product_image_url": (
                "data:image/gif;base64,"
                "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
            ),
            "status": "posted",
            "status_label": "Проведён",
            "total_quantity": 15,
            "positions": [{
                "product_id": "9133",
                "product_name": "Очень длинное полное название товара " * 8,
                "quantity": 15,
                "purchase_price": 1,
            }],
        }
    def test_long_receipt_layout_at_required_viewports(self):
        if sys.platform == "darwin":
            self.skipTest(
                "macOS Chrome does not reliably exit after --dump-dom"
            )
        chrome = self.find_chrome()
        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        original_testing = web.app.testing
        original_auth_testing = web.app.config.get("AUTH_TESTING")
        web.app.testing = True
        web.app.config["AUTH_TESTING"] = False
        patchers = [
            mock.patch.object(
                web,
                "api_receipt_records",
                return_value=(self.long_receipt(),),
            ),
        ]
        for patcher in patchers:
            patcher.start()
        server = make_server("127.0.0.1", 0, web.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            for width, height in (
                (1440, 900),
                (1024, 768),
                (768, 1024),
                (390, 844),
                (320, 568),
            ):
                with (
                    self.subTest(width=width, height=height),
                    tempfile.TemporaryDirectory() as temp,
                ):
                    profile = Path(temp) / "profile"
                    result = subprocess.run(
                        [
                            chrome,
                            "--headless=new",
                            "--no-sandbox",
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            f"--user-data-dir={profile}",
                            f"--window-size={width},{height}",
                            "--virtual-time-budget=2500",
                            "--dump-dom",
                            (
                                f"http://127.0.0.1:{server.server_port}"
                                "/app/receipts?receipt_card_layout_e2e=1"
                            ),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stderr[-2000:],
                    )
                    self.assertIn(
                        'data-receipt-card-layout-e2e="pass"',
                        result.stdout,
                    )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            for patcher in reversed(patchers):
                patcher.stop()
            web.app.testing = original_testing
            if original_auth_testing is None:
                web.app.config.pop("AUTH_TESTING", None)
            else:
                web.app.config["AUTH_TESTING"] = original_auth_testing


if __name__ == "__main__":
    unittest.main()
