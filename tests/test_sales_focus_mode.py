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


class SalesFocusModeStructureTest(unittest.TestCase):
    def source(self, relative_path):
        return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    def test_sales_focus_mode_has_scoped_accessible_contract(self):
        template = self.source("app/templates/sales.html")
        css = self.source("app/static/css/erp-focus-mode.css")
        script = self.source("app/static/js/erp-focus-mode.js")

        self.assertIn("vechasu.erp.sales.focus-mode.v1", template)
        self.assertIn('id="salesFocusModeToggle"', template)
        self.assertIn('aria-pressed="false"', template)
        self.assertIn("data-erp-focus-mode-toolbar", template)
        self.assertLess(
            template.index('id="salesColumnSettingsTrigger"'),
            template.index('id="salesFocusModeToggle"'),
        )
        self.assertIn(".sales-page .app.erp-focus-mode", css)
        self.assertNotIn("Fullscreen", template + script)
        self.assertIn('document.addEventListener("keydown"', script)
        self.assertIn("hasOpenOverlay()", script)
        self.assertIn("ResizeObserver", script)
        self.assertIn('window.addEventListener("pagehide"', script)
        self.assertIn("localStorage.setItem", script)
        self.assertIn("focusableState", script)

    def test_focus_mode_preserves_one_table_scroll_owner(self):
        css = self.source("app/static/css/erp-focus-mode.css")
        script = self.source("app/static/js/erp-focus-mode.js")

        self.assertIn("overflow: hidden", css)
        self.assertIn("overflow: auto", css)
        self.assertIn("position: sticky", css)
        self.assertIn("--erp-focus-toolbar-height", script)
        self.assertIn("hiddenElements.forEach", script)
        self.assertNotIn("document.body.classList.add", script)


class SalesFocusModeBrowserTest(unittest.TestCase):
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

    def test_focus_mode_at_required_viewports(self):
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
        environment = dict(os.environ, PREVIEW_PORT=str(port))
        server = subprocess.Popen(
            [sys.executable, "tests/stage2_preview_server.py"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            url = (
                "http://127.0.0.1:{}/app/sales"
                "?source=wildberries&status=completed"
                "&sales_focus_mode_e2e=1"
            ).format(port)
            for _attempt in range(100):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Preview server did not start")

            for width, height in (
                (1440, 900),
                (1024, 768),
                (390, 844),
                (320, 568),
            ):
                with (
                    self.subTest(width=width, height=height),
                    tempfile.TemporaryDirectory() as profile,
                ):
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
                            "--virtual-time-budget=6500",
                            "--dump-dom",
                            url,
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=40,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr[-2000:],
                    )
                    if 'data-sales-focus-mode-e2e="pass"' not in completed.stdout:
                        error = re.search(
                            r'data-sales-focus-mode-e2e-error="([^"]*)"',
                            completed.stdout,
                        )
                        self.fail(
                            error.group(1)
                            if error
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
