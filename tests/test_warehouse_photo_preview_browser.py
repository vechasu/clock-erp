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


class WarehousePhotoPreviewBrowserTest(unittest.TestCase):
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
        return next((
            candidate for candidate in candidates
            if candidate and Path(candidate).is_file()
        ), None)

    def test_preview_interactions_at_required_viewports(self):
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
            stderr=None,
        )
        base_url = "http://127.0.0.1:{}/app/products".format(port)
        url = base_url + "?warehouse_photo_preview_e2e=1"
        try:
            for _attempt in range(300):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Stage 2 preview server did not start")

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                scenarios = (
                    ("viewer", url, 'data-warehouse-photo-preview-e2e="pass"'),
                    (
                        "card-editor",
                        base_url + "?warehouse_bitrix_photo_e2e=1",
                        'data-warehouse-bitrix-photo-e2e="pass"',
                    ),
                    (
                        "photo-mutation",
                        base_url + "?warehouse_photo_mutation_e2e=1",
                        'data-warehouse-photo-mutation-e2e="pass"',
                    ),
                )
                for scenario, scenario_url, expected in scenarios:
                    with self.subTest(width=width, scenario=scenario), \
                            tempfile.TemporaryDirectory() as profile:
                        command = [
                            chrome,
                            "--headless=new",
                            "--no-sandbox",
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            "--user-data-dir={}".format(profile),
                            "--window-size={},{}".format(width, height),
                            "--virtual-time-budget=3000",
                            "--dump-dom",
                            scenario_url,
                        ]
                        for attempt in range(2):
                            command[5] = "--user-data-dir={}/attempt-{}".format(
                                profile, attempt
                            )
                            try:
                                result = subprocess.run(
                                    command,
                                    check=False,
                                    capture_output=True,
                                    text=True,
                                    timeout=30,
                                )
                                break
                            except subprocess.TimeoutExpired:
                                if attempt:
                                    raise
                        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                        self.assertIn(expected, result.stdout)
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    unittest.main()
