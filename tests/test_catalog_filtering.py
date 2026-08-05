import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)
from app.services.shared_catalog import SharedCatalog


ROOT = Path(__file__).resolve().parents[1]


class CatalogFilteringTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(self.database_path)},
        )
        self.environment.start()
        self.database = CatalogDatabase(self.database_path)
        ExcelProductBatchService(self.database).apply(
            [{
                "excel_row": 2,
                "excel_name": "Служебная карточка",
                "excel_brand": "Служебный бренд",
                "excel_article": "SEED",
                "article_quality": "code_like",
                "category": "Служебная категория",
                "stock": 0,
                "stock_valid": True,
                "cell": "A-1",
                "product_id": None,
                "match_status": "not_found",
                "match_method": "test",
                "confidence": 0,
                "alternatives": [],
            }],
            "f" * 64,
            "filtering.xlsx",
        )
        self.shared = SharedCatalog(self.database)
        self.excel = ExcelProductCatalog(self.database)
        self.brand = self.shared.create_brand("666 Barcelona")
        self.other_brand = self.shared.create_brand("Другой бренд")
        self.category_id, self.duplicate_category_id = self._categories()
        self._insert_products(
            count=120,
            brand_id=self.brand["id"],
            category_id=self.duplicate_category_id,
            prefix="Barcelona",
            positive_count=4,
        )
        self._insert_products(
            count=1,
            brand_id=self.other_brand["id"],
            category_id=self.category_id,
            prefix="Other",
            positive_count=0,
        )
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def _categories(self):
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_categories "
                "(brand_id, name, normalized_name, active, created_at, updated_at) "
                "VALUES (?, 'Наручные часы', 'наручные часы', 1, ?, ?)",
                (self.other_brand["id"], now, now),
            )
            canonical_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO erp_categories "
                "(brand_id, name, normalized_name, active, created_at, updated_at) "
                "VALUES (?, '  НАРУЧНЫЕ ЧАСЫ  ', 'наручные часы', 1, ?, ?)",
                (self.brand["id"], now, now),
            )
            duplicate_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
        return canonical_id, duplicate_id

    def _insert_products(
        self,
        count,
        brand_id,
        category_id,
        prefix,
        positive_count=0,
        special_last=False,
    ):
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self.database.transaction() as connection:
            batch = connection.execute(
                "SELECT id, file_sha256 FROM catalog_excel_batches "
                "WHERE status = 'active'"
            ).fetchone()
            start_row = connection.execute(
                "SELECT COALESCE(MAX(excel_row), 1) + 1 "
                "FROM catalog_excel_products"
            ).fetchone()[0]
            rows = []
            for offset in range(count):
                name = "{} {:05d}".format(prefix, offset)
                if special_last and offset == count - 1:
                    name = "{} Последний товар".format(prefix)
                article = "{}-{:05d}".format(prefix.upper(), offset)
                rows.append((
                    "test:{}:{}".format(prefix, offset),
                    batch["id"],
                    batch["id"],
                    json.dumps({"name": name}, ensure_ascii=False),
                    start_row + offset,
                    name,
                    name.casefold(),
                    article,
                    "code_like",
                    "666 Barcelona" if brand_id == self.brand["id"] else prefix,
                    "Наручные часы",
                    brand_id,
                    category_id,
                    1 if offset < positive_count else 0,
                    batch["file_sha256"],
                    now,
                    now,
                ))
            connection.executemany(
                "INSERT INTO catalog_excel_products ("
                "source_key, created_batch_id, current_batch_id, active, "
                "raw_excel_json, excel_row, excel_name_raw, normalized_name, "
                "excel_article, article_quality, excel_brand, excel_category, "
                "brand_id, category_id, stock, stock_source, file_sha256, "
                "match_status, match_method, match_confidence, match_decision, "
                "candidates_json, bitrix_link_cardinality, "
                "shared_bitrix_row_count, created_at, updated_at"
                ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'test', ?, 'not_found', 'test', 0, 'unmatched', '[]', "
                "'unlinked', 0, ?, ?)",
                rows,
            )

    def test_brand_category_and_intersection_filters_are_complete(self):
        brand_only = self.excel.list_products(
            brand_id=self.brand["id"],
            per_page=200,
        )
        category_only = self.excel.list_products(
            category_id=self.category_id,
            per_page=200,
        )
        intersection = self.excel.list_products(
            brand_id=self.brand["id"],
            category_id=self.category_id,
            per_page=200,
        )

        self.assertEqual(brand_only["total"], 120)
        self.assertEqual(category_only["total"], 121)
        self.assertEqual(intersection["total"], 120)

    def test_stock_filter_is_explicit_and_reset_removes_it(self):
        all_items = self.excel.list_products(
            brand_id=self.brand["id"],
            category_id=self.category_id,
            hide_zero=False,
            per_page=200,
        )
        in_stock = self.excel.list_products(
            brand_id=self.brand["id"],
            category_id=self.category_id,
            hide_zero=True,
            per_page=200,
        )
        template = (ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )

        self.assertEqual(all_items["total"], 120)
        self.assertEqual(in_stock["total"], 4)
        self.assertIn('id="warehouseInStockToggle"', template)
        self.assertIn('"in_stock",\n                "page"', template)
        self.assertIn('in_stock: ["in_stock"]', template)

    def test_warehouse_brand_and_category_render_two_active_filter_chips(self):
        response = self.client.get(
            "/warehouse?brand_id={}&category_id={}&per_page=200".format(
                self.brand["id"],
                self.category_id,
            )
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('class="erp-filter-chip"'), 2)
        self.assertIn("Бренд: 666 Barcelona", html)
        self.assertIn("Категория: Наручные часы", html)
        self.assertIn("Сбросить всё", html)
        self.assertEqual(html.count('data-product-id="'), 120)

    def test_warehouse_period_chip_variants_and_stock_chip(self):
        variants = (
            (
                "date_from=2026-08-01&date_to=2026-08-05",
                "Период: 01.08.2026–05.08.2026",
            ),
            ("date_from=2026-08-01", "Период: с 01.08.2026"),
            ("date_to=2026-08-05", "Период: до 05.08.2026"),
        )

        for query, label in variants:
            with self.subTest(label=label):
                response = self.client.get("/warehouse?" + query)
                html = " ".join(response.get_data(as_text=True).split())

                self.assertEqual(response.status_code, 200)
                self.assertEqual(html.count('class="erp-filter-chip"'), 1)
                self.assertIn(label, html)

        response = self.client.get(
            "/warehouse?brand_id={}&category_id={}&in_stock=1&per_page=200".format(
                self.brand["id"],
                self.category_id,
            )
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('class="erp-filter-chip"'), 3)
        self.assertIn("Только в наличии", html)
        self.assertIn('id="warehouseInStockToggle"', html)
        self.assertIn("checked", html)
        self.assertEqual(html.count('data-product-id="'), 4)

    def test_sales_and_receipts_return_complete_zero_stock_catalog(self):
        query = "brand_id={}&category_id={}&limit=200".format(
            self.brand["id"],
            self.category_id,
        )
        sales = self.client.get("/api/v1/sales/catalog?" + query).get_json()
        receipts = self.client.get(
            "/api/v1/receipts/catalog?" + query
        ).get_json()

        self.assertEqual(sales["meta"]["total"], 120)
        self.assertEqual(receipts["meta"]["total"], 120)
        self.assertEqual(len(sales["data"]), 120)
        self.assertEqual(len(receipts["data"]), 120)
        self.assertIn(0, [item["stock"] for item in sales["data"]])
        self.assertIn(0, [item["stock"] for item in receipts["data"]])

    def test_shared_options_do_not_apply_a_hidden_sales_stock_filter(self):
        sales_template = (ROOT / "app/templates/sales.html").read_text(
            encoding="utf-8"
        )
        options = self.client.get(
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}".format(
                self.brand["id"], self.category_id
            )
        ).get_json()

        self.assertNotIn('data-catalog-in-stock="true"', sales_template)
        self.assertEqual(options["meta"]["total"], 120)
        self.assertEqual(len(options["data"]), 120)

    def test_large_catalog_is_bounded_but_searches_beyond_first_window(self):
        large_brand = self.shared.create_brand("Большой бренд")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_categories "
                "(brand_id, name, normalized_name, active, created_at, updated_at) "
                "VALUES (?, 'Большая категория', 'большая категория', 1, ?, ?)",
                (large_brand["id"], now, now),
            )
            large_category_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
        self._insert_products(
            count=4601,
            brand_id=large_brand["id"],
            category_id=large_category_id,
            prefix="Large",
            special_last=True,
        )

        initial = self.client.get(
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}".format(
                large_brand["id"], large_category_id
            )
        ).get_json()
        searched = self.client.get(
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}&q={}".format(
                large_brand["id"],
                large_category_id,
                "Последний%20товар",
            )
        ).get_json()

        self.assertEqual(initial["meta"]["total"], 4601)
        self.assertEqual(len(initial["data"]), 200)
        self.assertEqual(len(searched["data"]), 1)
        self.assertEqual(searched["data"][0]["name"], "Large Последний товар")
        script = (ROOT / "app/static/js/catalog-combobox.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('limit: "200"', script)

    def test_repeated_catalog_request_does_not_reuse_a_stale_window(self):
        url = (
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}&q=Новая%20позиция"
        ).format(self.brand["id"], self.category_id)
        self.assertEqual(self.client.get(url).get_json()["data"], [])
        self._insert_products(
            count=1,
            brand_id=self.brand["id"],
            category_id=self.duplicate_category_id,
            prefix="Новая позиция",
        )
        refreshed = self.client.get(url).get_json()["data"]
        self.assertEqual(len(refreshed), 1)

    def test_normalized_duplicate_categories_are_one_option(self):
        options = self.client.get(
            "/api/v1/catalog/options?type=category&category_scope=brand"
            "&brand_id={}&limit=200".format(self.brand["id"])
        ).get_json()["data"]
        matching = [
            item for item in options
            if item["name"].strip().casefold() == "наручные часы"
        ]
        self.assertEqual(len(matching), 1)

    def test_selected_product_keeps_the_catalog_id(self):
        response = self.client.get(
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}&q=Barcelona%2000007".format(
                self.brand["id"], self.category_id
            )
        ).get_json()["data"]
        self.assertEqual(len(response), 1)
        selected_id = response[0]["id"]
        self.assertEqual(self.shared.get_product(selected_id)["id"], selected_id)


if __name__ == "__main__":
    unittest.main()
