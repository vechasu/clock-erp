import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.serving import make_server

from app import web


def product(item_id, name, article, with_photo=False):
    return {
        "id": item_id,
        "name": name,
        "article": article,
        "code": article,
        "barcode": "460000000001",
        "moysklad_product_id": "",
        "brand": "AARK",
        "category": "Наручные часы",
        "brand_id": 1,
        "category_id": 11,
        "cell": "A-01",
        "stock": 1,
        "stock_display": "1",
        "reserve": 0,
        "quantity": 1,
        "created_at": 1,
        "created_at_display": "01.01.2026",
        "has_images": with_photo,
        "thumbnail_url": (
            "https://example.test/thumb.jpg" if with_photo else ""
        ),
        "gallery": (
            [{"thumbnail_url": "https://example.test/thumb.jpg",
              "download_url": "https://example.test/photo.jpg"}]
            if with_photo else []
        ),
        "price_display": "",
        "source_url": "",
        "match_status": "not_found",
        "updated_at": "2026-01-01T00:00:00Z",
        "cell_source": "product",
        "cell_source_label": "у позиции",
        "cell_source_path": "",
        "moysklad_url": "#",
        "raw_category": "Наручные часы",
    }


class WarehouseTableBrowserTest(unittest.TestCase):
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
            (value for value in candidates if value and Path(value).is_file()),
            None,
        )

    def run_browser(self, width):
        chrome = self.find_chrome()
        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        items = [
            product(1001, "Часы Alpha", "A-1", with_photo=True),
            product(1002, "Часы Beta", "B-1"),
        ]
        original_testing = web.app.testing
        original_auth_testing = web.app.config.get("AUTH_TESTING")
        web.app.testing = True
        web.app.config["AUTH_TESTING"] = False
        server = make_server("127.0.0.1", 0, web.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        listing = {
            "items": [{}, {}],
            "total": 2,
            "page": 1,
            "per_page": 50,
            "pages": 1,
            "brand_groups": [{"name": "AARK", "count": 2}],
            "brand_all_count": 2,
            "category_groups": [{"name": "Наручные часы", "count": 2}],
            "cell_groups": [],
            "stats": {
                "positions": 2,
                "total_stock": 2,
                "positive_positions": 2,
                "zero_positions": 0,
            },
        }

        try:
            with mock.patch.object(
                web, "build_excel_warehouse_items", return_value=items
            ), mock.patch.object(
                web.ExcelProductCatalog, "list_products", return_value=listing
            ), mock.patch.object(
                web.ExcelProductCatalog,
                "list_manual_stock_operations",
                return_value=[],
            ), tempfile.TemporaryDirectory() as profile:
                thread.start()
                url = (
                    "http://127.0.0.1:{}"
                    "/warehouse?table_ui_e2e=1&brand=AARK&page=2"
                ).format(server.server_port)
                process = subprocess.Popen(
                    [
                        chrome,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--window-size={},900".format(width),
                        "--virtual-time-budget=4000",
                        "--user-data-dir={}".format(profile),
                        "--dump-dom",
                        url,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    stdout, stderr = process.communicate(timeout=8)
                except subprocess.TimeoutExpired as error:
                    process.terminate()
                    trailing_stdout, trailing_stderr = process.communicate(
                        timeout=5
                    )
                    timed_out_stdout = error.stdout or ""
                    timed_out_stderr = error.stderr or ""
                    if isinstance(timed_out_stdout, bytes):
                        timed_out_stdout = timed_out_stdout.decode(
                            "utf-8", errors="replace"
                        )
                    if isinstance(timed_out_stderr, bytes):
                        timed_out_stderr = timed_out_stderr.decode(
                            "utf-8", errors="replace"
                        )
                    stdout = timed_out_stdout + trailing_stdout
                    stderr = timed_out_stderr + trailing_stderr
        finally:
            server.shutdown()
            thread.join(timeout=5)
            web.app.testing = original_testing
            if original_auth_testing is None:
                web.app.config.pop("AUTH_TESTING", None)
            else:
                web.app.config["AUTH_TESTING"] = original_auth_testing

        self.assertIn(process.returncode, (0, -15), stderr[-2000:])
        self.assertIn('data-table-ui-e2e="pass"', stdout)
        return stdout

    def test_table_behaviour_at_mobile_and_desktop_viewports(self):
        for width in (390, 1024, 1440):
            with self.subTest(width=width):
                html = self.run_browser(width)
                marker = html.split('data-table-ui-e2e-result="', 1)[1].split(
                    '"', 1
                )[0]
                self.assertIn("migration", marker)
                self.assertIn("staleProtection", marker)


class WarehouseTableContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (
            Path(web.app.root_path) / web.app.template_folder / "warehouse.html"
        ).read_text(encoding="utf-8")

    def test_live_search_and_url_contracts_are_locked(self):
        self.assertIn("}, 275);", self.template)
        self.assertIn("new AbortController()", self.template)
        self.assertIn("requestId !== warehouseSearchRequestId", self.template)
        self.assertIn('url.searchParams.delete("page")', self.template)
        self.assertIn('url.searchParams.set("q", query)', self.template)
        self.assertNotIn("warehouseSearchKeepFocus", self.template)
        self.assertIn('warehouseSearchForm.addEventListener("submit"', self.template)

    def test_table_state_migration_reset_and_width_bounds_are_locked(self):
        self.assertIn("vechasu.warehouse.table-view.v1", self.template)
        self.assertIn("vechasu.warehouse.table-view.v2", self.template)
        self.assertIn("warehouseTableMaximumWidths", self.template)
        self.assertIn('id="warehouseTableReset"', self.template)
        self.assertIn("view.rowHeights = {};", self.template)
        self.assertIn("applyWarehouseColumnOrder(table, view.order)", self.template)

    def test_column_visibility_uses_v2_state_and_stable_keys(self):
        self.assertIn('id="warehouseColumnSettingsTrigger"', self.template)
        self.assertIn("warehouseTableRequiredColumns", self.template)
        self.assertIn("applyWarehouseColumnVisibility", self.template)
        self.assertIn("saved.hidden", self.template)
        self.assertNotIn("warehouse.column-visibility", self.template)
        visibility_block = self.template.split(
            "function applyWarehouseColumnVisibility", 1
        )[1].split("function syncWarehouseColumnSettings", 1)[0]
        self.assertIn("element.dataset.columnKey", visibility_block)
        self.assertIn('matchMedia("(min-width: 768px)")', visibility_block)

    def test_photo_form_uses_the_existing_safe_products_api(self):
        form = self.template.split('id="warehouseAddForm"', 1)[1].split(
            "</form>", 1
        )[0]
        self.assertIn('enctype="multipart/form-data"', form)
        self.assertIn('name="product_image"', form)
        self.assertIn("image/jpeg,image/png,image/webp", form)
        self.assertIn('fetch("/api/v1/products"', self.template)
        self.assertIn("new FormData(form)", self.template)
        self.assertIn("warehouseAddError", self.template)

    def test_bulk_limit_and_edit_preserve_existing_photo_contract(self):
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        client = web.app.test_client()
        response = client.post(
            "/warehouse/bulk-edit",
            data={"product_ids": [str(index) for index in range(101)]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "%D0%BD%D0%B5+%D0%B1%D0%BE%D0%BB%D1%8C%D1%88%D0%B5+100",
            response.location,
        )

        with mock.patch.object(
            web.ExcelProductCatalog, "update_product", return_value={}
        ) as update:
            edited = client.post(
                "/warehouse/edit",
                data={
                    "product_id": "1001",
                    "name": "Часы Alpha 2",
                    "article": "A-1",
                    "brand": "AARK",
                    "category": "Наручные часы",
                    "stock": "1",
                    "return_query": "q=Alpha&page=2&per_page=50",
                },
            )
        self.assertEqual(edited.status_code, 302)
        self.assertIn("q=Alpha", edited.location)
        self.assertIn("page=2", edited.location)
        self.assertNotIn("product_image", update.call_args.kwargs)

    def test_backend_photo_validator_remains_the_single_source_of_truth(self):
        self.assertIn(
            "read_product_image_upload(\n            request.files.get(\"product_image\"),\n            allow_webp=True",
            Path(web.__file__).read_text(encoding="utf-8"),
        )
        self.assertEqual(web.PRODUCT_IMAGE_MAX_BYTES, 3 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
