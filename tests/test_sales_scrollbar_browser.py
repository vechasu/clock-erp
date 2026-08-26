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


class SalesScrollbarStructureTest(unittest.TestCase):
    def test_sales_table_owns_a_mouse_draggable_synchronized_scrollbar(self):
        template = (
            PROJECT_ROOT / "app/templates/sales.html"
        ).read_text(encoding="utf-8")

        for contract in (
            "sales-table-wrap",
            'id="salesHorizontalScrollbar"',
            'id="salesHorizontalScrollControl"',
            'type="range"',
            "initializeSalesHorizontalScrollbar",
            'control.addEventListener("input"',
            'control.addEventListener("pointerdown"',
            'control.addEventListener("pointermove"',
            'tableWrap.addEventListener("scroll"',
            "new ResizeObserver(scheduleUpdate)",
            "new MutationObserver(scheduleUpdate)",
            'dock.classList.toggle("is-floating"',
            'root.scrollWidth > root.clientWidth',
            'document.body.scrollWidth > document.body.clientWidth',
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, template)

        self.assertNotIn("html.horizontal-scroll", template)
        self.assertNotIn("body.horizontal-scroll", template)


class SalesScrollbarBrowserTest(unittest.TestCase):
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

    def run_chrome(self, chrome, url, width, height):
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
                    "--virtual-time-budget=3000",
                    "--dump-dom",
                    url,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=35,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        if 'data-sales-scrollbar-e2e="pass"' not in result.stdout:
            error = re.search(
                r'data-sales-scrollbar-e2e-error="([^"]*)"',
                result.stdout,
            )
            self.fail(error.group(1) if error else result.stderr[-2000:])

    def test_scrollbar_survives_sources_filters_and_viewports(self):
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
            base_url = f"http://127.0.0.1:{port}/app/sales"
            # The shared preview fixture applies the full local schema before
            # listening. Give heavily loaded CI runners enough time to finish
            # that deterministic setup instead of treating startup latency as
            # a scrollbar regression.
            for _attempt in range(600):
                try:
                    with urllib.request.urlopen(
                        base_url + "?source=all", timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Stage 2 preview server did not start")

            cases = [
                ("all", "", 1920, 1080),
                ("all", "", 1440, 900),
                ("tictactoy", "", 1440, 900),
                ("wildberries", "", 1440, 900),
                ("amazon", "", 1440, 900),
            ]
            cases.extend([
                ("all", "&q=Casio", 1100, 760),
                ("all", "&status=completed", 1280, 800),
            ])

            for source, query, width, height in cases:
                with self.subTest(
                    source=source,
                    query=query,
                    width=width,
                ):
                    self.run_chrome(
                        chrome,
                        base_url
                        + f"?source={source}{query}&sales_scrollbar_e2e=1",
                        width,
                        height,
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
