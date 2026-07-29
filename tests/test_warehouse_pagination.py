import io
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog


class WarehousePaginationTest(unittest.TestCase):
    PRODUCT_COUNT = 5000

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "catalog.db"
        cls.database = CatalogDatabase(cls.path)
        cls.database.initialize()
        now = "2026-07-29T10:00:00+00:00"
        with cls.database.connect() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, sheet_name, row_count, "
                "total_stock, positive_rows, zero_rows, status, created_at, "
                "applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "active-batch", "a" * 64, "bulk.xlsx", "Импорт",
                    cls.PRODUCT_COUNT, cls.PRODUCT_COUNT // 2,
                    cls.PRODUCT_COUNT // 2, cls.PRODUCT_COUNT // 2,
                    "active", now, now,
                ),
            )
            connection.execute(
                "INSERT INTO catalog_products ("
                "name, article, barcode, brand, active, external_source, "
                "external_product_id, external_xml_id, payload_hash, "
                "normalized_payload_json, created_at, updated_at, "
                "first_synced_at, last_synced_at"
                ") VALUES (?, ?, ?, ?, 1, 'bitrix', ?, ?, ?, '{}', ?, ?, ?, ?)",
                (
                    "Special Barcode Product", "SPECIAL-42", "BARCODE-42",
                    "Omega", "BITRIX-42", "XML-42", "b" * 64,
                    now, now, now, now,
                ),
            )
            catalog_product_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            rows = []
            for index in range(cls.PRODUCT_COUNT):
                special = index == 42
                name = "Product {:04d}".format(index)
                article = "SKU-{:04d}".format(index)
                brand = "Omega" if index % 2 == 0 else "Casio"
                category = (
                    "Наручные часы" if index % 3 else "Будильники"
                )
                rows.append((
                    "row-{}".format(index),
                    "active-batch",
                    "active-batch",
                    "{}",
                    index + 2,
                    name,
                    name.casefold(),
                    article,
                    "code_like",
                    brand,
                    category,
                    float(index % 2),
                    "A-{:02d}".format(index % 20),
                    "a" * 64,
                    "exact",
                    "test",
                    1.0,
                    "automatic",
                    catalog_product_id if special else None,
                    "BITRIX-42" if special else "BITRIX-{}".format(index),
                    "XML-42" if special else "XML-{}".format(index),
                    "https://example.test/{}.jpg".format(index),
                    "https://example.test/thumb-{}.jpg".format(index),
                    "1000",
                    "RUB",
                    now,
                    now,
                ))
            connection.executemany(
                "INSERT INTO catalog_excel_products ("
                "source_key, created_batch_id, current_batch_id, raw_excel_json, "
                "excel_row, excel_name_raw, normalized_name, excel_article, "
                "article_quality, excel_brand, excel_category, stock, cell, "
                "file_sha256, match_status, match_method, match_confidence, "
                "match_decision, bitrix_catalog_product_id, "
                "bitrix_external_product_id, bitrix_xml_id, "
                "bitrix_primary_image_url, bitrix_thumbnail_url, "
                "bitrix_price_amount, bitrix_price_currency, created_at, updated_at"
                ") VALUES ({})".format(", ".join("?" for _ in range(27))),
                rows,
            )
        cls.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(cls.path)},
        )
        cls.environment.start()
        web.app.config.update(TESTING=True)
        cls.client = web.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.temp.cleanup()

    def test_default_and_selectable_page_sizes(self):
        response = self.client.get("/warehouse")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('data-product-id="'), 50)
        self.assertIn(
            'id="warehouseResultStart">1</span>–<span '
            'id="warehouseResultEnd">50</span>',
            html,
        )
        self.assertIn('id="warehouseResultTotal">5 000</span>', html)
        self.assertIn('class="active" aria-current="page">1</span>', html)
        self.assertIn('aria-label="Страница 2"', html)
        self.assertIn('aria-label="Страница 100"', html)
        self.assertNotIn('aria-label="Первая страница"', html)
        self.assertNotIn('aria-label="Последняя страница"', html)
        self.assertEqual(html.count('id="warehousePageSize"'), 1)
        self.assertIn('loading="lazy"', html)
        self.assertIn('decoding="async"', html)
        self.assertNotIn('data-gallery="', html)

        for per_page in (100, 200):
            with self.subTest(per_page=per_page):
                html = self.client.get(
                    "/warehouse?per_page={}".format(per_page)
                ).get_data(as_text=True)
                self.assertEqual(
                    html.count('data-product-id="'),
                    per_page,
                )

    def test_server_search_filters_sort_and_combination(self):
        catalog = ExcelProductCatalog(self.database)
        checks = (
            ("Product 0042", "Product 0042"),
            ("Omega", "Omega"),
            ("Будильники", "Будильники"),
            ("будильники", "Будильники"),
            ("SKU-0042", "SKU-0042"),
            ("BARCODE-42", "Product 0042"),
            ("BITRIX-42", "Product 0042"),
            ("XML-42", "Product 0042"),
            ("A-02", "A-02"),
        )
        for query, expected in checks:
            with self.subTest(query=query):
                result = catalog.list_products(query=query)
                self.assertGreater(result["total"], 0)
                rendered = str(result["items"][0])
                self.assertIn(expected, rendered)

        combined = catalog.list_products(
            brand="Casio",
            category="Будильники",
            hide_zero=True,
            sort_by="article",
            sort_dir="desc",
            per_page=200,
        )
        self.assertTrue(combined["items"])
        self.assertTrue(all(
            row["excel_brand"] == "Casio"
            and row["excel_category"] == "Будильники"
            and row["stock"] > 0
            for row in combined["items"]
        ))
        articles = [row["excel_article"] for row in combined["items"]]
        self.assertEqual(articles, sorted(articles, reverse=True))

    def test_brand_filter_counts_positions_and_respects_other_filters(self):
        catalog = ExcelProductCatalog(self.database)
        listing = catalog.list_products(per_page=50)
        self.assertEqual(listing["brand_all_count"], self.PRODUCT_COUNT)
        self.assertEqual(listing["brand_groups"], [
            {"name": "Casio", "count": 2500},
            {"name": "Omega", "count": 2500},
        ])

        searched = catalog.list_products(
            query="Product 0042",
            per_page=50,
        )
        self.assertEqual(searched["brand_all_count"], 1)
        self.assertEqual(
            searched["brand_groups"],
            [{"name": "Omega", "count": 1}],
        )

        selected = catalog.list_products(
            brand="Omega",
            per_page=50,
        )
        self.assertEqual(selected["total"], 2500)
        self.assertEqual(selected["brand_all_count"], self.PRODUCT_COUNT)
        self.assertEqual(selected["brand_groups"], listing["brand_groups"])

        html = self.client.get("/warehouse").get_data(as_text=True)
        brand_filter = html.split(
            'id="filterBrandCombobox"',
            1,
        )[1].split(
            '<div class="category-cell-form-title"',
            1,
        )[0]
        self.assertIn("Все бренды", brand_filter)
        self.assertIn("<span>5000</span>", brand_filter)
        self.assertEqual(brand_filter.count("<span>2500</span>"), 2)

    def test_pagination_state_is_kept_in_urls(self):
        html = self.client.get(
            "/warehouse?q=Product&brand=Omega&sort_by=article"
            "&sort_dir=desc&page=2&per_page=100"
        ).get_data(as_text=True)
        self.assertIn(
            'id="warehouseResultStart">101</span>–<span '
            'id="warehouseResultEnd">200</span>',
            html,
        )
        self.assertIn('class="active" aria-current="page">2</span>', html)
        self.assertIn('aria-label="Страница 25"', html)
        self.assertIn("q=Product", html)
        self.assertIn("brand=Omega", html)
        self.assertIn("per_page=100", html)

    def test_first_and_last_pages_use_compact_numbered_pagination(self):
        first_html = self.client.get("/warehouse").get_data(as_text=True)
        self.assertNotIn(
            'href="#" aria-label="Предыдущая страница"',
            first_html,
        )
        self.assertIn('aria-label="Следующая страница"', first_html)

        last_html = self.client.get(
            "/warehouse?page=100"
        ).get_data(as_text=True)
        self.assertIn(
            'id="warehouseResultStart">4951</span>–<span '
            'id="warehouseResultEnd">5000</span>',
            last_html,
        )
        self.assertIn(
            'class="active" aria-current="page">100</span>',
            last_html,
        )
        self.assertIn('aria-label="Предыдущая страница"', last_html)
        self.assertNotIn(
            'href="#" aria-label="Следующая страница"',
            last_html,
        )

    def test_zero_stock_filter_and_icon_actions_use_shared_components(self):
        html = self.client.get(
            "/warehouse?in_stock=1"
        ).get_data(as_text=True)
        self.assertRegex(
            html,
            r'id="warehouseInStockToggle"[^>]*\schecked',
        )

        stock_header = re.search(
            r'<th data-column-key="stock">(.*?)</th>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(stock_header)
        stock_header_markup = stock_header.group(1)
        self.assertIn("Остаток", stock_header_markup)
        self.assertIn('role="switch"', stock_header_markup)
        self.assertIn('aria-checked="true"', stock_header_markup)
        self.assertIn(
            'aria-label="Показать товары с нулевым остатком"',
            stock_header_markup,
        )
        self.assertIn(
            'title="Показать товары с нулевым остатком"',
            stock_header_markup,
        )
        self.assertNotIn("Скрыть нулевые остатки", html)

        actions_cell = re.search(
            r'<td data-column-key="actions">(.*?)</td>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(actions_cell)
        action_markup = actions_cell.group(1)
        self.assertIn("erp-table-action-view", action_markup)
        self.assertIn("erp-table-action-delete", action_markup)
        self.assertIn('title="Открыть карточку"', action_markup)
        self.assertIn('aria-label="Удалить товар"', action_markup)
        self.assertNotIn(">Карточка<", action_markup)
        self.assertNotIn(">Удалить<", action_markup)

    def test_in_stock_filter_hides_and_restores_zero_stock(self):
        all_html = self.client.get(
            "/warehouse?per_page=100"
        ).get_data(as_text=True)
        positive_html = self.client.get(
            "/warehouse?in_stock=1&per_page=100"
        ).get_data(as_text=True)
        restored_html = self.client.get(
            "/warehouse?per_page=100"
        ).get_data(as_text=True)

        stock_pattern = (
            r'<tr\s+data-product-id="[^"]+"\s+'
            r'data-stock="([^"]+)"'
        )
        all_stocks = [
            float(value)
            for value in re.findall(stock_pattern, all_html)
        ]
        positive_stocks = [
            float(value)
            for value in re.findall(stock_pattern, positive_html)
        ]
        restored_stocks = [
            float(value)
            for value in re.findall(stock_pattern, restored_html)
        ]

        self.assertIn(0, all_stocks)
        self.assertTrue(positive_stocks)
        self.assertTrue(all(stock > 0 for stock in positive_stocks))
        self.assertIn(0, restored_stocks)
        self.assertIn(
            'id="visiblePositionsCount" class="stat-value erp-stat-value">'
            "2500</div>",
            positive_html,
        )
        self.assertIn(
            'id="totalStockCount" class="stat-value erp-stat-value">'
            "2500</div>",
            positive_html,
        )
        self.assertIn(
            'id="visiblePositionsCount" class="stat-value erp-stat-value">'
            "5000</div>",
            restored_html,
        )

    def test_in_stock_combines_with_search_brand_category_and_date(self):
        cases = (
            "/warehouse?q=Product%20000&in_stock=1&per_page=100",
            "/warehouse?brand=Casio&in_stock=1&per_page=100",
            "/warehouse?category=Будильники&in_stock=1&per_page=100",
            (
                "/warehouse?brand=Casio&category=Будильники"
                "&in_stock=1&per_page=100"
            ),
            (
                "/warehouse?date_from=2026-07-29&date_to=2026-07-29"
                "&in_stock=1&per_page=100"
            ),
        )
        stock_pattern = (
            r'<tr\s+data-product-id="[^"]+"\s+'
            r'data-stock="([^"]+)"'
        )
        for path in cases:
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                stocks = [
                    float(value)
                    for value in re.findall(stock_pattern, html)
                ]
                self.assertTrue(stocks)
                self.assertTrue(all(stock > 0 for stock in stocks))
                self.assertRegex(
                    html,
                    r'id="warehouseInStockToggle"[^>]*\schecked',
                )

    def test_in_stock_keeps_sort_pagination_and_page_size_state(self):
        html = self.client.get(
            "/warehouse?in_stock=1&sort_by=stock&sort_dir=desc"
            "&page=2&per_page=100"
        ).get_data(as_text=True)
        stocks = [
            float(value)
            for value in re.findall(
                r'<tr\s+data-product-id="[^"]+"\s+'
                r'data-stock="([^"]+)"',
                html,
            )
        ]

        self.assertEqual(len(stocks), 100)
        self.assertTrue(all(stock > 0 for stock in stocks))
        self.assertIn(
            'id="warehouseResultStart">101</span>–<span '
            'id="warehouseResultEnd">200</span>',
            html,
        )
        self.assertIn("in_stock=1", html)
        self.assertIn("sort_by=stock", html)
        self.assertIn("sort_dir=desc", html)
        self.assertIn("per_page=100", html)
        self.assertIn(
            '<option value="100" selected>100</option>',
            html,
        )
        self.assertIn('aria-checked="true"', html)
        self.assertIn("↓", html)

    def test_in_stock_markup_keeps_toggle_and_sort_handlers_separate(self):
        html = self.client.get(
            "/warehouse?brand=1&category=Будильники&q=Product"
            "&date_from=2026-07-29&date_to=2026-07-29"
            "&sort_by=stock&sort_dir=desc&page=2&per_page=100"
        ).get_data(as_text=True)
        template = (
            Path(web.app.root_path)
            / web.app.template_folder
            / "warehouse.html"
        ).read_text(encoding="utf-8")

        self.assertIn('name="brand" value="1"', html)
        self.assertIn('id="warehouseInStockToggle"', html)
        self.assertIn('aria-checked="false"', html)
        self.assertIn('data-sort-field="stock"', template)
        self.assertIn(
            'onclick="sortWarehouseTable(this.dataset.sortField)"',
            template,
        )
        self.assertIn(
            'onchange="toggleWarehouseInStock(event, this)"',
            template,
        )
        self.assertIn('onclick="event.stopPropagation()"', template)
        self.assertIn(
            'url.searchParams.set("in_stock", "1");',
            template,
        )
        self.assertIn(
            'url.searchParams.delete("in_stock");',
            template,
        )
        self.assertIn(
            'url.searchParams.delete("page");',
            template,
        )
        toggle_handler = template.split(
            "function toggleWarehouseInStock", 1
        )[1].split("document.addEventListener", 1)[0]
        self.assertNotIn('searchParams.set("brand"', toggle_handler)
        self.assertNotIn('searchParams.delete("brand"', toggle_handler)
        sort_handler = template.split(
            "function sortWarehouseTable", 1
        )[1].split("initializeWarehouseTableView", 1)[0]
        self.assertNotIn('searchParams.set("in_stock"', sort_handler)
        self.assertNotIn('searchParams.delete("in_stock"', sort_handler)

    def test_query_count_is_constant_and_response_is_bounded(self):
        catalog = ExcelProductCatalog(self.database)

        def select_count(per_page):
            statements = []
            original_connect = self.database.connect

            def traced_connect():
                connection = original_connect()
                connection.set_trace_callback(statements.append)
                return connection

            with mock.patch.object(self.database, "connect", traced_connect):
                catalog.list_products(per_page=per_page)
            return len([
                sql for sql in statements
                if sql.lstrip().upper().startswith(("SELECT", "WITH"))
            ])

        self.assertEqual(select_count(50), select_count(200))
        started = time.perf_counter()
        response = self.client.get("/warehouse")
        elapsed = time.perf_counter() - started
        self.assertLess(len(response.data), 2_000_000)
        self.assertLess(elapsed, 1.5)

    def test_export_uses_all_filtered_rows_not_current_page(self):
        response = self.client.get(
            "/warehouse/export.xlsx?brand=Omega&sort_by=article"
        )
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(io.BytesIO(response.data), read_only=True)
        sheet = workbook.active
        self.assertEqual(sum(1 for _row in sheet.iter_rows()), 2501)


if __name__ == "__main__":
    unittest.main()
