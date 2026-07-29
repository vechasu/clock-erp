"""Browser regression checks for the warehouse stock switch."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest import mock

from werkzeug.serving import make_server

from app import web


def warehouse_browser_item(item_id, name, stock):
    return {
        "id": item_id,
        "name": name,
        "article": "SKU-{}".format(item_id),
        "code": "SKU-{}".format(item_id),
        "barcode": "",
        "brand": "1",
        "category": "Наручные часы",
        "cell": "A-01",
        "stock": stock,
        "stock_display": str(stock),
        "reserve": 0,
        "quantity": stock,
        "created_at": 1,
        "created_at_display": "29.07.2026",
        "has_images": False,
        "thumbnail_url": "",
        "gallery": [],
        "price_display": "1 000 ₽",
        "cell_source": "product",
        "cell_source_label": "у позиции",
        "cell_source_path": "",
        "moysklad_url": "#",
        "raw_category": "Наручные часы",
    }


class InjectWarehouseStockToggleCheck:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        response = {}

        def capture(status, headers, exc_info=None):
            response["status"] = status
            response["headers"] = headers
            response["exc_info"] = exc_info

        iterable = self.application(environ, capture)
        try:
            body = b"".join(iterable)
        finally:
            close = getattr(iterable, "close", None)
            if close:
                close()

        if environ.get("PATH_INFO") == "/warehouse" and b"</body>" in body:
            body = body.replace(
                b"</body>",
                self.browser_script() + b"</body>",
                1,
            )
            response["headers"] = [
                (name, value)
                for name, value in response["headers"]
                if name.lower() != "content-length"
            ]
            response["headers"].append(
                ("Content-Length", str(len(body)))
            )

        start_response(
            response["status"],
            response["headers"],
            response["exc_info"],
        )
        return [body]

    @staticmethod
    def browser_script():
        return r"""
<script>
(() => {
    let running = false;
    const run = () => {
        if (running) return;
        running = true;
        window.setTimeout(() => {
            const params = new URL(window.location.href).searchParams;
            const mobile = window.innerWidth <= 767;
            const toggle = document.getElementById(
                mobile
                    ? "warehouseMobileInStockToggle"
                    : "warehouseInStockToggle"
            );
            const failures = JSON.parse(
                sessionStorage.getItem("warehouseStockFailures") || "[]"
            );
            const check = (condition, label) => {
                if (!condition) failures.push(label);
            };
            const keepFilters = () => {
                check(params.get("q") === "Product", "search");
                check(params.get("brand") === "1", "brand");
                check(
                    params.get("category") === "Наручные часы",
                    "category"
                );
                check(
                    params.get("date_from") === "2026-07-29",
                    "date-from"
                );
                check(
                    params.get("date_to") === "2026-07-29",
                    "date-to"
                );
                check(params.get("sort_by") === "stock", "sort-field");
                check(params.get("per_page") === "100", "page-size");
            };
            const step = Number(
                sessionStorage.getItem("warehouseStockStep") || "0"
            );

            check(Boolean(toggle), "toggle-present-" + step);
            if (!toggle) {
                document.documentElement.dataset.warehouseStockToggleE2e =
                    failures.join(",") || "toggle-missing";
                return;
            }

            const rect = toggle.closest("label").getBoundingClientRect();
            check(
                rect.width > 0
                    && rect.height > 0
                    && rect.left >= -1
                    && rect.right <= window.innerWidth + 1,
                "toggle-visible-" + step
            );
            keepFilters();

            if (step === 0) {
                check(params.get("page") === "2", "initial-page");
                check(!params.has("in_stock"), "initial-off-url");
                check(!toggle.checked, "initial-off-control");
                check(
                    toggle.getAttribute("aria-checked") === "false",
                    "initial-off-aria"
                );
                check(
                    params.get("sort_dir") === "desc",
                    "initial-sort"
                );
                sessionStorage.setItem("warehouseStockStep", "1");
                sessionStorage.setItem(
                    "warehouseStockFailures",
                    JSON.stringify(failures)
                );
                toggle.click();
                return;
            }

            if (step === 1) {
                check(params.get("in_stock") === "1", "enabled-url");
                check(!params.has("page"), "enabled-page-reset");
                check(toggle.checked, "enabled-control");
                check(
                    toggle.getAttribute("aria-checked") === "true",
                    "enabled-aria"
                );
                check(
                    toggle.getAttribute("aria-label")
                        === "Показать товары с нулевым остатком",
                    "enabled-label"
                );
                check(
                    params.get("sort_dir") === "desc",
                    "toggle-kept-sort"
                );
                sessionStorage.setItem("warehouseStockStep", "2");
                sessionStorage.setItem(
                    "warehouseStockFailures",
                    JSON.stringify(failures)
                );
                if (mobile) {
                    const sort = document.getElementById(
                        "warehouseMobileSort"
                    );
                    sort.value = "stock";
                    sort.dispatchEvent(
                        new Event("change", {bubbles: true})
                    );
                } else {
                    document.querySelector(
                        '[data-sort-field="stock"]'
                    ).click();
                }
                return;
            }

            if (step === 2) {
                check(params.get("in_stock") === "1", "sort-kept-toggle");
                check(toggle.checked, "sort-kept-control");
                check(params.get("sort_dir") === "asc", "sort-changed");
                sessionStorage.setItem("warehouseStockStep", "3");
                sessionStorage.setItem(
                    "warehouseStockFailures",
                    JSON.stringify(failures)
                );
                toggle.click();
                return;
            }

            if (step === 3) {
                check(!params.has("in_stock"), "disabled-url");
                check(!params.has("page"), "disabled-page-reset");
                check(!toggle.checked, "disabled-control");
                check(
                    toggle.getAttribute("aria-checked") === "false",
                    "disabled-aria"
                );
                check(
                    params.get("sort_dir") === "asc",
                    "disabled-kept-sort"
                );
                sessionStorage.setItem("warehouseStockStep", "4");
                sessionStorage.setItem(
                    "warehouseStockFailures",
                    JSON.stringify(failures)
                );
                window.history.back();
                return;
            }

            if (step === 4) {
                check(params.get("in_stock") === "1", "back-url");
                check(toggle.checked, "back-control");
                sessionStorage.setItem("warehouseStockStep", "5");
                sessionStorage.setItem(
                    "warehouseStockFailures",
                    JSON.stringify(failures)
                );
                window.history.forward();
                return;
            }

            check(!params.has("in_stock"), "forward-url");
            check(!toggle.checked, "forward-control");
            sessionStorage.setItem(
                "warehouseStockFailures",
                JSON.stringify(failures)
            );
            document.documentElement.dataset.warehouseStockToggleE2e =
                failures.length ? failures.join(",") : "pass";
        }, 120);
    };

    window.addEventListener("pageshow", () => {
        running = false;
        run();
    });
})();
</script>
""".encode("utf-8")


class WarehouseStockToggleBrowserTest(unittest.TestCase):
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

    def run_browser_check(self, chrome, width, height):
        items = [
            warehouse_browser_item("1", "Product zero", 0),
            warehouse_browser_item("2", "Product positive", 2),
        ]

        def list_products(**filters):
            visible_items = (
                [items[1]] if filters.get("hide_zero") else items
            )
            return {
                "items": visible_items,
                "total": len(visible_items),
                "page": 1,
                "per_page": 100,
                "pages": 1,
                "brand_groups": [{"name": "1", "count": 2}],
                "category_groups": [{
                    "name": "Наручные часы",
                    "count": 2,
                }],
                "cell_groups": [],
                "stats": {
                    "positions": len(visible_items),
                    "total_stock": 2,
                },
            }

        original_testing = web.app.testing
        web.app.testing = True
        server = make_server(
            "127.0.0.1",
            0,
            InjectWarehouseStockToggleCheck(web.app),
        )
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )
        query = urlencode({
            "q": "Product",
            "brand": "1",
            "category": "Наручные часы",
            "date_from": "2026-07-29",
            "date_to": "2026-07-29",
            "sort_by": "stock",
            "sort_dir": "desc",
            "page": "2",
            "per_page": "100",
        })

        try:
            with mock.patch.object(
                web.ExcelProductCatalog,
                "list_products",
                side_effect=list_products,
            ), mock.patch.object(
                web,
                "build_excel_warehouse_items",
                side_effect=lambda rows: rows,
            ), mock.patch.object(
                web,
                "load_catalog_taxonomy",
                return_value={"brands": [], "categories": []},
            ), mock.patch.object(
                web.ExcelProductCatalog,
                "list_manual_stock_operations",
                return_value=[],
            ), tempfile.TemporaryDirectory() as profile:
                thread.start()
                result = subprocess.run(
                    [
                        chrome,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        f"--user-data-dir={profile}",
                        f"--window-size={width},{height}",
                        "--virtual-time-budget=7000",
                        "--dump-dom",
                        (
                            "http://127.0.0.1:{}"
                            "/warehouse?{}"
                        ).format(server.server_port, query),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            web.app.testing = original_testing

        self.assertEqual(
            result.returncode,
            0,
            result.stderr[-2000:],
        )
        self.assertIn(
            'data-warehouse-stock-toggle-e2e="pass"',
            result.stdout,
            result.stdout[-4000:],
        )

    def test_toggle_sort_history_and_layout_on_required_viewports(self):
        if sys.platform == "darwin":
            self.skipTest(
                "macOS Chrome does not reliably exit after --dump-dom"
            )

        chrome = self.find_chrome()
        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        for width, height in (
            (1920, 1080),
            (1440, 1000),
            (1366, 900),
            (430, 932),
            (390, 844),
            (375, 812),
            (320, 700),
        ):
            with self.subTest(width=width):
                self.run_browser_check(chrome, width, height)


if __name__ == "__main__":
    unittest.main()
