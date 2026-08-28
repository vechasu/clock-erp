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
VIEWPORTS = (
    (1998, 1305),
    (1440, 900),
    (1280, 800),
    (1024, 768),
    (768, 1024),
    (390, 844),
)


class MailUiBrowserTest(unittest.TestCase):
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
        return next((item for item in candidates if item and Path(item).is_file()), None)

    def setUp(self):
        if sys.platform == "darwin":
            self.skipTest("macOS Chrome does not reliably exit after --dump-dom")
        self.chrome = self.find_chrome()
        if not self.chrome:
            self.skipTest("Chrome/Chromium is unavailable")

    def server(self, role="admin", connected=False):
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        environment = dict(
            os.environ,
            PREVIEW_PORT=str(port),
            MAIL_PREVIEW_ROLE=role,
            MAIL_PREVIEW_CONNECTED="1" if connected else "0",
        )
        process = subprocess.Popen(
            [sys.executable, "tests/mail_ui_preview_server.py"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = "http://127.0.0.1:{}/app/mail".format(port)
        for unused in range(600):
            del unused
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        return process, url
            except OSError:
                time.sleep(0.05)
        process.terminate()
        process.wait(timeout=5)
        self.fail("Mail preview server did not start")

    def dump(self, url, width, height):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [
                    self.chrome,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--hide-scrollbars",
                    "--user-data-dir={}".format(Path(temporary) / "profile"),
                    "--window-size={},{}".format(width, height),
                    "--virtual-time-budget=3500",
                    "--dump-dom",
                    url,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return result.stdout

    @staticmethod
    def stop(process):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def test_owner_empty_state_wizard_and_connected_workspace_are_responsive(self):
        empty_process, empty_url = self.server()
        try:
            for width, height in VIEWPORTS:
                with self.subTest(state="empty", width=width, height=height):
                    dom = self.dump(empty_url + "?mail_ui_e2e=empty", width, height)
                    for check in ("overflow", "empty", "connect-button", "toast", "a11y"):
                        self.assertIn('data-mail-{}="pass"'.format(check), dom)
                with self.subTest(state="wizard", width=width, height=height):
                    dom = self.dump(empty_url + "?mail_ui_e2e=wizard", width, height)
                    for check in ("overflow", "wizard", "focus", "a11y"):
                        self.assertIn('data-mail-{}="pass"'.format(check), dom)
        finally:
            self.stop(empty_process)

        connected_process, connected_url = self.server(connected=True)
        try:
            for width, height in VIEWPORTS:
                with self.subTest(state="connected", width=width, height=height):
                    dom = self.dump(
                        connected_url + "?mail_ui_e2e=connected", width, height
                    )
                    for check in ("overflow", "connected", "a11y"):
                        self.assertIn('data-mail-{}="pass"'.format(check), dom)
        finally:
            self.stop(connected_process)

    def test_employee_sees_instruction_without_connection_controls(self):
        process, url = self.server(role="employee")
        try:
            dom = self.dump(url + "?mail_ui_e2e=employee", 390, 844)
            self.assertIn('data-mail-overflow="pass"', dom)
            self.assertIn('data-mail-employee="pass"', dom)
            self.assertIn('data-mail-a11y="pass"', dom)
        finally:
            self.stop(process)


if __name__ == "__main__":
    unittest.main()
