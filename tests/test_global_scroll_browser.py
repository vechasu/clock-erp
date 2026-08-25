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


class GlobalScrollBrowserTest(unittest.TestCase):
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

    def test_bottom_action_is_reachable_with_one_document_scroller(self):
        if sys.platform == "darwin":
            self.skipTest("macOS Chrome does not reliably exit after --dump-dom")
        chrome = self.find_chrome()
        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(port),
                "--bind",
                "127.0.0.1",
                "--directory",
                str(PROJECT_ROOT),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = (
            "http://127.0.0.1:{}/tests/fixtures/global_scroll_e2e.html"
        ).format(port)
        try:
            for _attempt in range(100):
                try:
                    with urllib.request.urlopen(base_url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Scroll fixture server did not start")

            for scenario in ("customers", "order"):
                for width, height in (
                    (1920, 1080),
                    (1536, 864),
                    (1440, 900),
                    (1366, 768),
                    (1024, 600),
                    (768, 520),
                    (390, 520),
                ):
                    with self.subTest(
                        scenario=scenario, width=width, height=height
                    ), tempfile.TemporaryDirectory() as profile:
                        url = base_url + "?target=" + scenario
                        for _browser_attempt in range(3):
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
                                    "--virtual-time-budget=1000",
                                    "--dump-dom",
                                    url,
                                ],
                                check=False,
                                capture_output=True,
                                text=True,
                                timeout=35,
                            )
                            if (
                                completed.returncode == 0
                                and 'data-global-scroll-e2e="pass"'
                                in completed.stdout
                            ):
                                break
                        self.assertEqual(
                            completed.returncode,
                            0,
                            completed.stderr[-2000:],
                        )
                        if (
                            'data-global-scroll-e2e="pass"'
                            not in completed.stdout
                        ):
                            details = re.search(
                                r'data-details="([^"]*)"', completed.stdout
                            )
                            self.fail(
                                details.group(1)
                                if details
                                else completed.stderr[-2000:]
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
