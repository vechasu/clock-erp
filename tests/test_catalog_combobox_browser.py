import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from flask import Flask, send_file
from werkzeug.serving import make_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CatalogComboboxStructureTest(unittest.TestCase):
    def test_product_renderer_has_one_name_article_and_stock(self):
        script = (PROJECT_ROOT / "app/static/js/catalog-combobox.js").read_text(
            encoding="utf-8"
        )
        label_function = script.split(
            "function sharedCatalogProductLabel", 1
        )[1].split("function sharedCatalogStockValue", 1)[0]
        self.assertNotIn("без артикула", label_function)
        self.assertNotIn("остаток", label_function)
        self.assertIn(
            '? "Артикул: " + (item.article || "—")',
            script,
        )
        self.assertIn(
            '"Остаток: " + sharedCatalogStockValue(item)',
            script,
        )
        self.assertIn("catalog-combobox-option-details", script)
        self.assertIn("catalog-combobox-option-image", script)
        self.assertIn("positionComboboxDropdown", script)
        self.assertIn("Math.max(triggerRect.width, 520)", script)
        self.assertIn("Math.max(triggerRect.width, 320)", script)
        self.assertIn("include_order_counts", script)
        self.assertIn('Number(item?.stock) > 0', script)
        self.assertIn('"Нет товаров в наличии"', script)
        stylesheet = (
            PROJECT_ROOT / "app/static/css/catalog-combobox.css"
        ).read_text(encoding="utf-8")
        self.assertIn("max-height: 340px", stylesheet)
        self.assertIn("width: 44px", stylesheet)
        self.assertIn("word-break: normal", stylesheet)
        self.assertNotIn("overflow-wrap: anywhere", stylesheet)
        self.assertNotIn(
            ".brand-combobox-option span:last-child",
            stylesheet,
        )

    def test_all_active_sections_use_the_same_product_renderer(self):
        for template in ("warehouse.html", "sales.html", "receipts.html"):
            with self.subTest(template=template):
                source = (PROJECT_ROOT / "app/templates" / template).read_text(
                    encoding="utf-8"
                )
                self.assertIn("js/catalog-combobox.js", source)
                self.assertIn('shared_catalog_kind="product"', source)

        sales = (PROJECT_ROOT / "app/templates/sales.html").read_text(
            encoding="utf-8"
        )
        warehouse = (PROJECT_ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        receipts = (PROJECT_ROOT / "app/templates/receipts.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-catalog-in-stock="true"', sales)
        orders = (PROJECT_ROOT / "app/templates/orders.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-catalog-in-stock="false"', orders)
        self.assertNotIn('data-catalog-in-stock="true"', warehouse)
        self.assertNotIn('data-catalog-in-stock="true"', receipts)
        self.assertIn('form.dataset.submitting === "1"', warehouse)
        self.assertIn("result.doubleSubmit", warehouse)

    def test_shared_catalog_script_never_blocks_application_layout(self):
        for template in (
            "warehouse.html",
            "warehouse_inventory.html",
            "sales.html",
            "receipts.html",
        ):
            with self.subTest(template=template):
                source = (PROJECT_ROOT / "app/templates" / template).read_text(
                    encoding="utf-8"
                )
                script_tag = re.search(
                    r"<script\b[^>]*catalog-combobox\.js[^>]*>",
                    source,
                    re.DOTALL,
                )
                self.assertIsNotNone(script_tag)
                self.assertGreater(script_tag.start(), source.index("<body"))
                self.assertIn(
                    "catalog-combobox-20260824-product-hierarchy",
                    script_tag.group(0),
                )

        orders = (PROJECT_ROOT / "app/templates/orders.html").read_text(
            encoding="utf-8"
        )
        orders_script = re.search(
            r"<script\b[^>]*catalog-combobox\.js[^>]*>",
            orders,
            re.DOTALL,
        )
        self.assertIsNotNone(orders_script)
        self.assertRegex(orders_script.group(0), r"\bdefer\b")
        self.assertIn(
            "catalog-combobox-20260824-product-hierarchy",
            orders_script.group(0),
        )

    def test_external_head_scripts_cannot_block_layout_parser(self):
        for template in (
            "warehouse.html",
            "sales.html",
            "receipts.html",
            "orders.html",
        ):
            with self.subTest(template=template):
                source = (PROJECT_ROOT / "app/templates" / template).read_text(
                    encoding="utf-8"
                )
                head = source[: source.index("</head>")]
                external_scripts = re.findall(
                    r"<script\b[^>]*\bsrc=[^>]*>",
                    head,
                    re.DOTALL,
                )
                self.assertTrue(external_scripts)
                for script_tag in external_scripts:
                    self.assertRegex(script_tag, r"\b(?:async|defer)\b")

    def test_shared_shell_scripts_cannot_block_layout_parser(self):
        for template in ("_sidebar.html", "auth_base.html"):
            with self.subTest(template=template):
                source = (PROJECT_ROOT / "app/templates" / template).read_text(
                    encoding="utf-8"
                )
                external_scripts = re.findall(
                    r"<script\b[^>]*\bsrc=[^>]*>",
                    source,
                    re.DOTALL,
                )
                self.assertTrue(external_scripts)
                for script_tag in external_scripts:
                    self.assertRegex(script_tag, r"\b(?:async|defer)\b")

        sidebar = (PROJECT_ROOT / "app/templates/_sidebar.html").read_text(
            encoding="utf-8"
        )
        sidebar_scripts = re.findall(
            r"<script\b[^>]*\bsrc=[^>]*>",
            sidebar,
            re.DOTALL,
        )
        for script_tag in sidebar_scripts:
            self.assertRegex(script_tag, r"\basync\b")

        sidebar_styles = re.findall(
            r"<link\b[^>]*\brel=[\"']stylesheet[\"'][^>]*>",
            sidebar,
            re.DOTALL,
        )
        self.assertTrue(sidebar_styles)
        for style_tag in sidebar_styles:
            self.assertRegex(style_tag, r"\bmedia=[\"']print[\"']")
            self.assertRegex(style_tag, r"\bonload=")


class CatalogComboboxBrowserTest(unittest.TestCase):
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

    def test_product_options_at_desktop_and_mobile_widths(self):
        if sys.platform == "darwin":
            self.skipTest(
                "macOS Chrome does not reliably exit after --dump-dom"
            )
        chrome = self.find_chrome()
        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        app = Flask(
            __name__,
            static_folder=str(PROJECT_ROOT / "app/static"),
            static_url_path="/static",
        )

        @app.route("/")
        def fixture():
            return send_file(
                PROJECT_ROOT / "tests/fixtures/catalog_combobox_e2e.html"
            )

        @app.route("/cascade")
        def cascade_fixture():
            return send_file(
                PROJECT_ROOT
                / "tests/fixtures/catalog_cascade_uncategorized_e2e.html"
            )

        @app.route("/inventory")
        def inventory_fixture():
            return send_file(
                PROJECT_ROOT
                / "tests/fixtures/inventory_scope_combobox_e2e.html"
            )

        server = make_server("127.0.0.1", 0, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            for width, height in ((1440, 900), (390, 844), (320, 568)):
                with (
                    self.subTest(width=width, height=height),
                    tempfile.TemporaryDirectory() as profile,
                ):
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
                            f"http://127.0.0.1:{server.server_port}/",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stderr[-2000:],
                    )
                    self.assertIn(
                        'data-catalog-combobox-e2e="pass"',
                        result.stdout,
                    )

                    cascade = subprocess.run(
                        [
                            chrome,
                            "--headless=new",
                            "--no-sandbox",
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            f"--user-data-dir={profile}",
                            f"--window-size={width},{height}",
                            "--virtual-time-budget=3500",
                            "--dump-dom",
                            f"http://127.0.0.1:{server.server_port}/cascade",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(
                        cascade.returncode,
                        0,
                        cascade.stderr[-2000:],
                    )
                    self.assertIn(
                        'data-catalog-cascade-e2e="pass"',
                        cascade.stdout,
                    )

                    inventory = subprocess.run(
                        [
                            chrome,
                            "--headless=new",
                            "--no-sandbox",
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            f"--user-data-dir={profile}",
                            f"--window-size={width},{height}",
                            "--virtual-time-budget=3500",
                            "--dump-dom",
                            f"http://127.0.0.1:{server.server_port}/inventory",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(
                        inventory.returncode,
                        0,
                        inventory.stderr[-2000:],
                    )
                    self.assertIn(
                        'data-inventory-scope-combobox-e2e="pass"',
                        inventory.stdout,
                    )
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_warehouse_add_form_prevents_double_submit(self):
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
                f"http://127.0.0.1:{port}"
                "/app/products?warehouse_add_ui_e2e=1"
            )
            for _attempt in range(100):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Stage 2 preview server did not start")

            with tempfile.TemporaryDirectory() as profile:
                result = None
                for attempt in range(2):
                    try:
                        result = subprocess.run(
                            [
                                chrome,
                                "--headless=new",
                                "--no-sandbox",
                                "--disable-gpu",
                                "--disable-dev-shm-usage",
                                f"--user-data-dir={profile}/attempt-{attempt}",
                                "--window-size=1440,900",
                                "--virtual-time-budget=5000",
                                "--dump-dom",
                                url,
                            ],
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=45,
                        )
                    except subprocess.TimeoutExpired:
                        if attempt:
                            raise
                        continue
                    if 'data-warehouse-add-ui-e2e="pass"' in result.stdout:
                        break
                self.assertIsNotNone(result)
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stderr[-2000:],
                )
                self.assertIn(
                    'data-warehouse-add-ui-e2e="pass"',
                    result.stdout,
                )
                self.assertIn(
                    '&quot;doubleSubmit&quot;:true',
                    result.stdout,
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
