import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app import web


class RegistrationBrowserTest(unittest.TestCase):
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

    def test_registration_has_no_horizontal_scroll_at_required_widths(self):
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
        server = make_server("127.0.0.1", 0, web.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)

        try:
            thread.start()
            for width, height in ((1440, 900), (390, 844), (320, 700)):
                with self.subTest(width=width), tempfile.TemporaryDirectory() as profile:
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
                                "/register?registration_ui_e2e=1"
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
                        'data-registration-ui-e2e="pass"',
                        result.stdout,
                    )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            web.app.testing = original_testing
            if original_auth_testing is None:
                web.app.config.pop("AUTH_TESTING", None)
            else:
                web.app.config["AUTH_TESTING"] = original_auth_testing


if __name__ == "__main__":
    unittest.main()
