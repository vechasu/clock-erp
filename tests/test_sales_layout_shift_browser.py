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


class SalesLayoutShiftBrowserTest(unittest.TestCase):
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

    def test_sales_table_has_no_shift_with_delayed_footer_asset(self):
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
        environment = dict(
            os.environ,
            PREVIEW_PORT=str(port),
            SALES_LAYOUT_SHIFT_DELAY="1",
        )
        server = subprocess.Popen(
            [sys.executable, "tests/stage2_preview_server.py"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            url = (
                f"http://127.0.0.1:{port}/app/sales"
                "?source=all&sales_layout_shift_e2e=1"
            )
            for _attempt in range(600):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Stage 2 preview server did not start")

            for width, height in ((1440, 900), (1024, 768), (390, 844)):
                with self.subTest(width=width, height=height):
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
                                "--virtual-time-budget=5000",
                                "--dump-dom",
                                url,
                            ],
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=40,
                        )
                    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                    if 'data-sales-layout-shift-e2e="pass"' not in result.stdout:
                        value = re.search(
                            r'data-sales-layout-shift-value="([^"]*)"',
                            result.stdout,
                        )
                        self.fail(
                            "sales CLS=" + (value.group(1) if value else "unknown")
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
