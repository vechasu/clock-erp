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


class ToolbarPopoverStructureTest(unittest.TestCase):
    def source(self, relative_path):
        return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    def test_products_and_receipts_share_exclusive_popover_coordinator(self):
        coordinator = self.source(
            "app/static/js/toolbar-popover-coordinator.js"
        )
        warehouse = self.source("app/templates/warehouse.html")
        receipts = self.source("app/templates/receipts.html")

        self.assertIn("closeAll(entry, false)", coordinator)
        self.assertIn('event.key !== "Escape"', coordinator)
        self.assertIn("entry.container.contains(event.target)", coordinator)
        period_picker = self.source("app/static/js/period-picker.js")
        self.assertIn(
            'button.addEventListener("click", function(event)',
            period_picker,
        )
        self.assertIn("event.stopPropagation()", period_picker)
        for source in (warehouse, receipts):
            self.assertIn("js/toolbar-popover-coordinator.js", source)
            self.assertIn("createErpToolbarPopoverCoordinator", source)

    def test_products_compact_controls_keep_visible_drawer_contract(self):
        warehouse = self.source("app/templates/warehouse.html")
        components = self.source("app/static/css/erp-components.css")
        workspace = self.source("app/static/css/products-workspace.css")

        self.assertIn("warehouse-availability-segments", warehouse)
        self.assertIn("warehouse-availability-select", warehouse)
        self.assertIn('aria-label="Наличие товара"', warehouse)
        self.assertIn("#filterDrawer.drawer:not(.open)", components)
        self.assertIn("#filterDrawer.drawer.open", components)
        self.assertIn("min-height: 48px", workspace)

    def test_sales_reference_manager_and_modal_lock_are_unchanged(self):
        sales = self.source("app/templates/sales.html")
        modal_shell = self.source("app/static/js/erp-modal-shell.js")
        receipts = self.source("app/templates/receipts.html")

        self.assertIn("activeEntry?.api.close()", sales)
        self.assertIn("salesPopoverManager.register", sales)
        self.assertIn("data-erp-modal-lock", modal_shell)
        self.assertIn("event.stopImmediatePropagation()", modal_shell)
        self.assertNotIn(
            "receipt-report-button",
            receipts.split(
                "createErpToolbarPopoverCoordinator([", 1
            )[1].split("])", 1)[0],
        )

    def test_sales_channel_selection_has_neutral_all_state(self):
        sales = self.source("app/templates/sales.html")

        self.assertIn('id="saleFormFields"', sales)
        self.assertIn("function clearSaleFormSource()", sales)
        self.assertIn(
            '"Выберите канал, в который будет добавлена продажа."',
            sales,
        )
        self.assertIn(
            '"Добавить продажу в " + sourceLabels[sourceKey]',
            sales,
        )
        self.assertNotIn(
            'localStorage.getItem(\n                    "vechasu-sales-last-source"',
            sales,
        )

    def test_main_tables_use_one_server_pagination_component(self):
        pagination = self.source("app/templates/_pagination.html")
        e2e = self.source("app/static/js/pagination-e2e.js")
        for template in ("warehouse.html", "sales.html", "receipts.html"):
            source = self.source("app/templates/" + template)
            self.assertIn("render_erp_pagination", source)
        self.assertIn('name="per_page"', pagination)
        self.assertIn('aria-current="page"', pagination)
        self.assertIn('aria-disabled="true"', pagination)
        self.assertIn("horizontal-overflow", e2e)
        self.assertIn("mobile-target", e2e)


class ToolbarPopoverBrowserTest(unittest.TestCase):
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

    def run_chrome(
        self, chrome, url, width, height, marker,
        virtual_time_budget=3500,
    ):
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
                    f"--virtual-time-budget={virtual_time_budget}",
                    "--dump-dom",
                    url,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=35,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        if marker not in result.stdout:
            error = re.search(
                r'data-sales-filters-e2e-error="([^"]*)"',
                result.stdout,
            )
            if not error:
                error = re.search(
                    r'data-sales-channel-e2e="fail-([^"]*)"',
                    result.stdout,
                )
            if not error:
                error = re.search(
                    r'data-products-workflow-e2e-error="([^"]*)"',
                    result.stdout,
                )
            self.fail(
                error.group(1) if error else result.stderr[-2000:]
            )

    def test_toolbar_popovers_and_locked_modals(self):
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
            ready_url = (
                f"http://127.0.0.1:{port}"
                "/app/products?toolbar_popover_e2e=1"
            )
            for _attempt in range(100):
                try:
                    with urllib.request.urlopen(
                        ready_url, timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("Stage 2 preview server did not start")

            paths = (
                "/app/products?toolbar_popover_e2e=1"
                "&brand=Casio&q=Casio&sort_by=stock",
                "/app/receipts?toolbar_popover_e2e=1",
            )
            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for path in paths:
                    with self.subTest(width=width, path=path):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}{path}",
                            width,
                            height,
                            'data-toolbar-popover-e2e="pass"',
                        )

            for width, height in (
                (1440, 900),
                (1920, 1080),
                (900, 1024),
                (390, 844),
            ):
                with self.subTest(path="products-workflow", width=width):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}/app/products"
                        "?products_workflow_e2e=1",
                        width,
                        height,
                        'data-products-workflow-e2e="pass"',
                        virtual_time_budget=3000,
                    )

            modal_paths = (
                (
                    "/app/sales?sales_modal_e2e=1",
                    'data-sales-modal-e2e="pass"',
                ),
                (
                    "/app/receipts?receipts_modal_e2e=1",
                    'data-receipts-modal-e2e="pass"',
                ),
            )
            for width, height in ((1440, 900), (390, 844), (320, 568)):
                with self.subTest(path="sales-kpi", width=width):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}/app/sales"
                        "?source=all&sales_kpi_e2e=1",
                        width,
                        height,
                        'data-sales-kpi-e2e="pass"',
                    )
            for path, marker in modal_paths:
                with self.subTest(path=path, width=1440):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}{path}",
                        1440,
                        900,
                        marker,
                    )
            for width, height in ((390, 844), (320, 568)):
                with self.subTest(path="sales", width=width):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}"
                        "/app/sales?sales_modal_e2e=1",
                        width,
                        height,
                        'data-sales-modal-e2e="pass"',
                    )

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for source in ("all", "tictactoy", "wildberries", "amazon"):
                    with self.subTest(
                        path="sales-article",
                        source=source,
                        width=width,
                    ):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}/app/sales"
                            f"?source={source}&sales_article_e2e=1",
                            width,
                            height,
                            'data-sales-article-e2e="pass"',
                        )

            for width, height in (
                (1440, 900),
                (1024, 768),
                (390, 844),
                (320, 568),
            ):
                with self.subTest(path="sales-columns", width=width):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}/app/sales"
                        "?source=all&sales_columns_scroll_e2e=1",
                        width,
                        height,
                        'data-sales-columns-scroll-e2e="pass"',
                    )

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for source in ("all", "tictactoy", "wildberries", "amazon"):
                    with self.subTest(
                        path="sales-channel",
                        source=source,
                        width=width,
                    ):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}/app/sales"
                            f"?source={source}&sales_channel_e2e=1",
                            width,
                            height,
                            'data-sales-channel-e2e="pass"',
                        )

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                with self.subTest(path="sales-filters", width=width):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}/app/sales"
                        "?source=all&brand_id=snapshot%3Abrand%3Acasio"
                        "&sales_filters_e2e=1",
                        width,
                        height,
                        'data-sales-filters-e2e="pass"',
                    )

            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for source in ("all", "tictactoy", "wildberries", "amazon"):
                    with self.subTest(
                        path="sales-cancellation", source=source, width=width,
                    ):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}/app/sales"
                            f"?source={source}&sales_cancel_e2e=1",
                            width,
                            height,
                            'data-sales-cancel-e2e="pass"',
                        )

            datetime_paths = (
                "/app/products?datetime_e2e=1",
                "/app/sales?source=all&datetime_e2e=1",
                "/app/sales?source=tictactoy&datetime_e2e=1",
                "/app/sales?source=wildberries&datetime_e2e=1",
                "/app/sales?source=amazon&datetime_e2e=1",
                "/app/receipts?datetime_e2e=1",
            )
            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for path in datetime_paths:
                    with self.subTest(path=path, width=width):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}{path}",
                            width,
                            height,
                            'data-datetime-e2e="pass"',
                        )

            pagination_paths = (
                "/app/products?pagination_e2e=1&page=2&per_page=25",
                "/app/sales?pagination_e2e=1&source=all&page=2&per_page=25",
                "/app/receipts?pagination_e2e=1&page=2&per_page=25",
            )
            for width, height in ((1440, 900), (390, 844), (320, 568)):
                for path in pagination_paths:
                    with self.subTest(path=path, width=width):
                        self.run_chrome(
                            chrome,
                            f"http://127.0.0.1:{port}{path}",
                            width,
                            height,
                            'data-pagination-e2e="pass"',
                        )

            for source in ("tictactoy", "wildberries", "amazon"):
                with self.subTest(path="sales-pagination", source=source):
                    self.run_chrome(
                        chrome,
                        f"http://127.0.0.1:{port}/app/sales"
                        f"?pagination_e2e=1&source={source}&per_page=25",
                        1440,
                        900,
                        'data-pagination-e2e="pass"',
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
