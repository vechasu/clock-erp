import io
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
        self.assertIn("Найдено 5 000 товаров", html)
        self.assertIn("Показаны 1–50", html)
        self.assertIn("Страница 1 из 100", html)
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

    def test_pagination_state_is_kept_in_urls(self):
        html = self.client.get(
            "/warehouse?q=Product&brand=Omega&sort_by=article"
            "&sort_dir=desc&page=2&per_page=100"
        ).get_data(as_text=True)
        self.assertIn("Показаны 101–200", html)
        self.assertIn("Страница 2 из 25", html)
        self.assertIn("q=Product", html)
        self.assertIn("brand=Omega", html)
        self.assertIn("per_page=100", html)

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
