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


class ProductsFocusModeStructureTest(unittest.TestCase):
    def source(self, relative_path):
        return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    def test_products_focus_mode_has_complete_scoped_workspace(self):
        template = self.source("app/templates/warehouse.html")
        css = self.source("app/static/css/erp-focus-mode.css")

        self.assertIn("vechasu.erp.products.focus-mode.v1", template)
        self.assertIn('id="warehouseFocusModeToggle"', template)
        self.assertIn('aria-expanded="false"', template)
        self.assertIn("data-erp-focus-mode-toolbar", template)
        self.assertIn('data-focus-mode-subject="таблицу товаров"', template)
        for control in (
            'id="warehouseSearchInput"',
            'id="warehouseSearchClear"',
            'id="warehouseFilterTrigger"',
            'aria-label="Наличие товара"',
            'id="warehouseColumnSettingsTrigger"',
            'id="warehouseProductsTable"',
            'render_erp_pagination(pagination, "Страницы товаров")',
        ):
            self.assertIn(control, template)
        self.assertIn(".warehouse-page .app.erp-focus-mode", css)
        self.assertIn("#warehouseTableScrollBody", css)
        self.assertIn("position: sticky", css)
        self.assertNotIn("Fullscreen", template)

    def test_products_focus_mode_excludes_analytics_and_preserves_shared_state(self):
        template = self.source("app/templates/warehouse.html")
        script = self.source("app/static/js/erp-focus-mode.js")

        hide_contract = template.split('data-focus-mode-hide="', 1)[1].split('"', 1)[0]
        for selector in (
            ".products-workspace-header",
            ".products-workspace-tabs",
            ".products-workspace-metrics",
            "#appSidebar",
        ):
            self.assertIn(selector, hide_contract)
        self.assertNotIn("#warehouseResults", hide_contract)
        self.assertNotIn("#warehouseSearchForm", hide_contract)
        self.assertIn("root.classList.toggle", script)
        self.assertIn('toggle.setAttribute("aria-expanded"', script)
        self.assertIn('event.key !== "Escape"', script)
        self.assertIn('window.addEventListener("pagehide"', script)
        self.assertNotIn("window.location.reload", script)
        self.assertIn("products-focus-mode-e2e.js", template)

    def test_other_sections_do_not_receive_products_focus_mode(self):
        for relative_path in (
            "app/templates/receipts.html",
            "app/templates/repair.html",
            "app/templates/journal.html",
        ):
            self.assertNotIn(
                "vechasu.erp.products.focus-mode.v1",
                self.source(relative_path),
            )


class ProductsFocusModeBrowserTest(unittest.TestCase):
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
            stderr=subprocess.DEVNULL,
        )
        try:
            url = (
                "http://127.0.0.1:{}/warehouse"
                "?q=Casio&products_focus_mode_e2e=1"
            ).format(port)
            for _attempt in range(300):
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
                    if 'data-products-focus-mode-e2e="pass"' not in completed.stdout:
                        error = re.search(
                            r'data-products-focus-mode-e2e-error="([^"]*)"',
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
