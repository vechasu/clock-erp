import json
import re
import tempfile
import unittest
from contextlib import contextmanager
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
        special_at=None,
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
                if special_at is not None and offset == special_at:
                    name = "Фоссил тестовый товар"
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
                    "Наручные часы" if category_id is not None else "",
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

    def test_brand_category_filters_use_exact_canonical_category_id(self):
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
            category_id=self.duplicate_category_id,
            per_page=200,
        )

        self.assertEqual(brand_only["total"], 120)
        self.assertEqual(category_only["total"], 1)
        self.assertEqual(intersection["total"], 120)
        self.assertEqual(
            self.excel.list_products(
                brand_id=self.brand["id"],
                category_id=self.category_id,
                per_page=200,
            )["total"],
            0,
        )

    def test_stock_tabs_replace_duplicate_in_stock_toggle(self):
        all_items = self.excel.list_products(
            brand_id=self.brand["id"],
            category_id=self.duplicate_category_id,
            hide_zero=False,
            per_page=200,
        )
        in_stock = self.excel.list_products(
            brand_id=self.brand["id"],
            category_id=self.duplicate_category_id,
            hide_zero=True,
            per_page=200,
        )
        template = (ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        workspace = (ROOT / "app/templates/_products_workspace.html").read_text(
            encoding="utf-8"
        )

        self.assertEqual(all_items["total"], 120)
        self.assertEqual(in_stock["total"], 4)
        self.assertNotIn('id="warehouseInStockToggle"', template)
        self.assertIn("В наличии", workspace)
        self.assertIn("Нет в наличии", workspace)

    def test_stock_tab_counts_are_global_and_use_positive_stock_only(self):
        counts = self.excel.stock_tab_counts()

        self.assertEqual(counts["in_stock"], 4)
        self.assertEqual(counts["out_of_stock"], 118)
        self.assertEqual(counts["units_in_stock"], 4)
        self.assertEqual(counts["positions"], 122)
        self.assertEqual(counts["units_total"], 4)

    def test_brand_and_category_pages_hide_empty_entries_by_default(self):
        empty_category = self.shared.create_category(
            self.other_brand["id"], "Пустая категория"
        )

        brand_default = self.client.get("/warehouse?view=brands").get_data(
            as_text=True
        ).split('id="brandList"', 1)[1].split("</div>\n", 1)[0]
        brand_all = self.client.get(
            "/warehouse?view=brands&show_empty=1"
        ).get_data(as_text=True).split('id="brandList"', 1)[1].split(
            "</div>\n", 1
        )[0]
        category_default = self.client.get(
            "/warehouse?view=categories"
        ).get_data(as_text=True)
        category_all = self.client.get(
            "/warehouse?view=categories&show_empty=1"
        ).get_data(as_text=True)

        self.assertIn("666 Barcelona", brand_default)
        self.assertNotIn("Другой бренд", brand_default)
        self.assertIn("Другой бренд", brand_all)
        self.assertNotIn("Пустая категория", category_default)
        self.assertIn("Пустая категория", category_all)
        self.assertIn(
            "category_id={}".format(empty_category["id"]), category_all
        )

    def test_stock_filter_preserves_fractional_and_legacy_value_semantics(self):
        with self.database.transaction() as connection:
            ids = [row[0] for row in connection.execute(
                "SELECT id FROM catalog_excel_products WHERE brand_id = ? "
                "ORDER BY id LIMIT 5", (self.brand["id"],)
            ).fetchall()]
            values = (0.5, 2.5, 0, -1, "legacy-invalid")
            connection.executemany(
                "UPDATE catalog_excel_products SET stock = ? WHERE id = ?",
                list(zip(values, ids)),
            )

        result = self.excel.list_products(
            brand_id=self.brand["id"], category_id=self.duplicate_category_id,
            hide_zero=True, sort_by="stock", sort_dir="asc", per_page=200,
        )

        self.assertEqual(result["total"], 2)
        self.assertEqual([item["stock"] for item in result["items"]], [0.5, 2.5])

    def test_repair_catalog_endpoint_uses_lightweight_catalog_projection(self):
        response = self.client.get(
            "/api/v1/repairs/catalog?q=barcelona%2000001&limit=10"
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["name"], "Barcelona 00001")
        self.assertEqual(payload["data"][0]["brand"], "666 Barcelona")
        self.assertEqual(
            set(payload["data"][0]),
            {"id", "name", "brand", "model", "article"},
        )

    def test_repair_catalog_requires_two_characters_and_caps_at_twenty(self):
        empty = self.client.get("/api/v1/repairs/catalog").get_json()
        short = self.client.get("/api/v1/repairs/catalog?q=b").get_json()
        matches = self.client.get(
            "/api/v1/repairs/catalog?q=barcelona&limit=999"
        ).get_json()

        self.assertEqual(empty["data"], [])
        self.assertEqual(short["data"], [])
        self.assertEqual(len(matches["data"]), 20)
        self.assertEqual(matches["meta"]["limit"], 20)

    def test_repair_catalog_like_metacharacters_are_literal(self):
        percent = self.client.get("/api/v1/repairs/catalog?q=%25%25")
        underscore = self.client.get("/api/v1/repairs/catalog?q=__")

        self.assertEqual(percent.status_code, 200)
        self.assertEqual(percent.get_json()["data"], [])
        self.assertEqual(underscore.status_code, 200)
        self.assertEqual(underscore.get_json()["data"], [])

    def test_repair_catalog_can_resolve_exact_product_id(self):
        product = self.excel.list_products(
            brand_id=self.brand["id"], per_page=1,
        )["items"][0]
        payload = self.client.get(
            "/api/v1/repairs/catalog?product_id={}".format(product["id"])
        ).get_json()

        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["id"], str(product["id"]))

    def test_repair_catalog_searches_current_external_identifiers(self):
        product = self.excel.list_products(
            brand_id=self.brand["id"], per_page=1,
        )["items"][0]
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET moysklad_product_id = ?, "
                "bitrix_external_product_id = ?, bitrix_xml_id = ? WHERE id = ?",
                ("ms-repair-42", "bx-repair-42", "xml-repair-42", product["id"]),
            )

        for query in ("ms-repair-42", "bx-repair-42", "xml-repair-42"):
            payload = self.client.get(
                "/api/v1/repairs/catalog?q={}".format(query)
            ).get_json()
            self.assertEqual(payload["data"][0]["id"], str(product["id"]))

    def test_repair_catalog_search_is_one_read_only_sql_statement(self):
        statements = []
        original_connect = self.database.connect

        @contextmanager
        def traced_connect():
            with original_connect() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        with mock.patch.object(self.database, "connect", side_effect=traced_connect):
            items = self.excel.search_repair_catalog_items("barcelona", limit=20)

        sql = [statement.lstrip().upper() for statement in statements]
        self.assertEqual(len(items), 20)
        self.assertEqual(sum(
            "SELECT P.ID, P.EXCEL_NAME_RAW AS NAME" in statement
            for statement in sql
        ), 1)
        self.assertFalse(any(statement.startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE")
        ) for statement in sql))

    def test_repair_catalog_requires_authentication_when_auth_is_enabled(self):
        web.app.config["AUTH_TESTING"] = True
        try:
            response = self.client.get("/api/v1/repairs/catalog?q=barcelona")
        finally:
            web.app.config["AUTH_TESTING"] = False

        self.assertEqual(response.status_code, 401)

    def test_warehouse_brand_and_category_render_two_active_filter_chips(self):
        response = self.client.get(
            "/warehouse?brand_id={}&category_id={}&per_page=100".format(
                self.brand["id"],
                self.duplicate_category_id,
            )
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('class="erp-filter-chip"'), 2)
        self.assertIn("Бренд: 666 Barcelona", html)
        self.assertIn("Категория:   НАРУЧНЫЕ ЧАСЫ  ", html)
        self.assertIn("Сбросить всё", html)
        self.assertEqual(html.count('data-product-id="'), 100)

    def test_warehouse_period_chips_do_not_duplicate_stock_state(self):
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

        template = (ROOT / "app/templates/warehouse.html").read_text(encoding="utf-8")
        self.assertNotIn("Только в наличии", template)

        response = self.client.get("/warehouse?stock_state=out")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Наличие: Нет в наличии", html)
        self.assertNotIn('class="erp-filter-chip"', html)
        self.assertNotIn('class="erp-filter-count"', html)

    def test_out_of_stock_check_filter_lives_in_drawer_and_counts_once(self):
        response = self.client.get(
            "/warehouse?stock_state=out&check_state=unchecked&q=Barcelona"
        )
        html = response.get_data(as_text=True)
        toolbar = html.split(
            '<div class="search-card erp-toolbar-card">', 1
        )[1].split('<div class="warehouse-mobile-stock-controls">', 1)[0]
        drawer = html.split('<aside\n        id="filterDrawer"', 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Статус проверки площадок", toolbar)
        self.assertIn('id="warehouseCheckState"', drawer)
        self.assertIn('value="unchecked" selected', drawer)
        self.assertIn("Статус проверки: Не проверены", html)
        self.assertEqual(html.count('class="erp-filter-chip"'), 1)
        self.assertIn('class="erp-filter-count"', toolbar)
        self.assertRegex(toolbar, r'class="erp-filter-count"[^>]*>1</span>')
        self.assertIn('name="q"', toolbar)
        self.assertIn('value="Barcelona"', toolbar)
        self.assertIn('name="stock_state" value="out"', toolbar)
        self.assertIn('name="check_state" value="unchecked"', toolbar)

    def test_check_filter_is_not_applied_or_shown_outside_out_of_stock(self):
        response = self.client.get(
            "/warehouse?stock_state=in&check_state=unchecked"
        )
        html = response.get_data(as_text=True)
        toolbar = html.split(
            '<div class="search-card erp-toolbar-card">', 1
        )[1].split('<div class="warehouse-mobile-stock-controls">', 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="warehouseCheckState"', html)
        self.assertNotIn("Статус проверки: Не проверены", html)
        self.assertNotIn('name="check_state" value="unchecked"', toolbar)
        self.assertNotIn('class="erp-filter-count"', html)
        self.assertEqual(html.count('data-product-id="'), 4)

    def test_sales_uses_positive_stock_while_receipts_keep_full_catalog(self):
        query = "brand_id={}&category_id={}&limit=200".format(
            self.brand["id"],
            self.duplicate_category_id,
        )
        sales = self.client.get("/api/v1/sales/catalog?" + query).get_json()
        receipts = self.client.get(
            "/api/v1/receipts/catalog?" + query
        ).get_json()

        self.assertEqual(sales["meta"]["total"], 4)
        self.assertEqual(receipts["meta"]["total"], 120)
        self.assertEqual(len(sales["data"]), 4)
        self.assertEqual(len(receipts["data"]), 120)
        self.assertTrue(all(item["stock"] > 0 for item in sales["data"]))
        self.assertIn(0, [item["stock"] for item in receipts["data"]])

    def test_sales_add_modal_requests_only_available_cascade_options(self):
        sales_template = (ROOT / "app/templates/sales.html").read_text(
            encoding="utf-8"
        )
        options = self.client.get(
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}&available_for_sale=1".format(
                self.brand["id"], self.duplicate_category_id
            )
        ).get_json()
        brands = self.client.get(
            "/api/v1/catalog/options?type=brand&limit=200"
            "&available_for_sale=1"
        ).get_json()
        categories = self.client.get(
            "/api/v1/catalog/options?type=category&limit=200"
            "&category_scope=brand&brand_id={}"
            "&available_for_sale=1".format(self.brand["id"])
        ).get_json()

        self.assertIn('data-catalog-in-stock="true"', sales_template)
        self.assertNotIn("Добавить новый бренд", sales_template)
        self.assertNotIn("Добавить новую категорию", sales_template)
        self.assertNotIn("Добавить новый товар", sales_template)
        self.assertEqual(
            [item["id"] for item in brands["data"]],
            [self.brand["id"]],
        )
        self.assertEqual(len(categories["data"]), 1)
        self.assertEqual(categories["data"][0]["name"].strip().casefold(), "наручные часы")
        self.assertEqual(categories["data"][0]["id"], self.duplicate_category_id)
        self.assertEqual(options["meta"]["total"], 4)
        self.assertEqual(len(options["data"]), 4)
        self.assertTrue(all(item["stock"] > 0 for item in options["data"]))

    def test_available_order_mapping_options_exclude_unavailable_catalog_rows(self):
        zero_category = self.shared.create_category(
            self.brand["id"], "Очки"
        )
        archived_category = self.shared.create_category(
            self.brand["id"], "Сумка"
        )
        available_category = self.shared.create_category(
            self.brand["id"], "Украшения"
        )
        other_category = self.shared.create_category(
            self.brand["id"], "Ремешки"
        )
        zero = self.excel.create_product(
            name="FA36-012-1L", article="FA36-012-1L",
            brand_id=self.brand["id"], category_id=zero_category["id"],
            stock=0,
        )
        archived = self.excel.create_product(
            name="FA36-012-3L", article="FA36-012-3L",
            brand_id=self.brand["id"],
            category_id=archived_category["id"], stock=7,
        )
        available = self.excel.create_product(
            name="FA41-012-5S available", article="FA41-012-5S-A",
            brand_id=self.brand["id"],
            category_id=available_category["id"], stock=1,
        )
        wrong_category = self.excel.create_product(
            name="Другой раздел", article="OTHER-CATEGORY",
            brand_id=self.brand["id"], category_id=other_category["id"],
            stock=3,
        )
        wrong_brand = self.excel.create_product(
            name="Другой бренд", article="OTHER-BRAND",
            brand_id=self.other_brand["id"],
            category_id=available_category["id"], stock=4,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET active = 0 WHERE id = ?",
                (archived["id"],),
            )

        categories = self.client.get(
            "/api/v1/catalog/options?type=category&limit=200"
            "&category_scope=brand&brand_id={}"
            "&available_for_sale=1".format(self.brand["id"])
        ).get_json()["data"]
        category_names = {item["name"]: item for item in categories}

        self.assertNotIn("Очки", category_names)
        self.assertNotIn("Сумка", category_names)
        self.assertIn("Украшения", category_names)
        self.assertEqual(category_names["Украшения"]["count"], 1)

        product_url = (
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}&available_for_sale=1"
        ).format(self.brand["id"], available_category["id"])
        products = self.client.get(product_url).get_json()["data"]
        product_ids = [str(item["id"]) for item in products]
        self.assertEqual(product_ids, [str(available["id"])])
        self.assertNotIn(str(zero["id"]), product_ids)
        self.assertNotIn(str(wrong_category["id"]), product_ids)
        self.assertNotIn(str(wrong_brand["id"]), product_ids)
        self.assertTrue(all(item["stock"] > 0 for item in products))

        unavailable_search = self.client.get(
            product_url + "&q=FA36-012-1L"
        ).get_json()["data"]
        available_search = self.client.get(
            product_url + "&q=FA41-012-5S"
        ).get_json()["data"]
        self.assertEqual(unavailable_search, [])
        self.assertEqual(
            [str(item["id"]) for item in available_search],
            [str(available["id"])],
        )

    def test_sale_channels_share_catalog_excel_stock(self):
        expected_product_ids = None

        for source in ("Tictactoy", "Wildberries", "Amazon"):
            with self.subTest(source=source):
                query = (
                    "source={}&brand_id={}&category_id={}&limit=200"
                ).format(source, self.brand["id"], self.duplicate_category_id)
                products = self.client.get(
                    "/api/v1/sales/catalog?" + query
                ).get_json()
                brands = self.client.get(
                    "/api/v1/catalog/options?type=brand"
                    "&available_for_sale=1&source=" + source
                ).get_json()

                product_ids = [item["id"] for item in products["data"]]
                if expected_product_ids is None:
                    expected_product_ids = product_ids
                self.assertEqual(product_ids, expected_product_ids)
                self.assertEqual(len(product_ids), 4)
                self.assertTrue(all(
                    item["stock"] > 0 for item in products["data"]
                ))
                self.assertIn(
                    self.brand["id"],
                    {item["id"] for item in brands["data"]},
                )

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
            special_at=3499,
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
                "Ф",
            )
        ).get_json()

        self.assertEqual(initial["meta"]["total"], 4601)
        self.assertEqual(len(initial["data"]), 200)
        self.assertEqual(len(searched["data"]), 1)
        self.assertEqual(
            searched["data"][0]["name"],
            "Фоссил тестовый товар",
        )
        script = (ROOT / "app/static/js/catalog-combobox.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('limit: "200"', script)

    def test_prefix_search_matches_name_or_article_but_not_infix(self):
        products = (
            ("Фоссил часы", "NAME-1"),
            ("Механические часы", "MECH-1"),
            ("Часы Фоссил", "INSIDE-1"),
            ("Товар по артикулу", "Ф123"),
            ("Товар с внутренним артикулом", "AB-Ф123"),
        )
        for name, article in products:
            self.excel.create_product(
                name=name,
                article=article,
                brand_id=self.brand["id"],
                category_id=self.category_id,
                stock=1,
            )

        api = self.client.get(
            "/api/v1/catalog/options?type=product&limit=200&q=Ф"
        ).get_json()["data"]
        api_names = {item["name"] for item in api}
        warehouse = self.client.get("/warehouse?q=Ф&per_page=200")
        warehouse_html = warehouse.get_data(as_text=True)
        warehouse_names = set(re.findall(
            r'<tr\s+data-product-id="[^"]+"\s+'
            r'data-stock="[^"]+"\s+data-name="([^"]+)"',
            warehouse_html,
        ))

        self.assertEqual(api_names, {
            "Фоссил часы",
            "Товар по артикулу",
        })
        self.assertEqual(warehouse.status_code, 200)
        self.assertEqual(warehouse_names, {
            "фоссил часы",
            "товар по артикулу",
        })

    def test_brand_category_and_product_queries_use_normalized_prefixes(self):
        danish = self.shared.create_brand("Danish Design")
        self.shared.create_brand("The D Brand")
        category = self.shared.create_category(danish["id"], "Ёлочные часы")
        self.excel.create_product(
            name="  Ёлочные   часы  ",
            article="TREE-1",
            brand_id=danish["id"],
            category_id=category["id"],
        )

        brands = self.client.get(
            "/api/v1/catalog/options?type=brand&q=d"
        ).get_json()["data"]
        categories = self.client.get(
            "/api/v1/catalog/options?type=category&q=%20ел%20"
        ).get_json()["data"]
        products = self.client.get(
            "/api/v1/catalog/options?type=product&q=%20ЕЛ%20"
        ).get_json()["data"]

        self.assertIn("Danish Design", [item["name"] for item in brands])
        self.assertNotIn("The D Brand", [item["name"] for item in brands])
        self.assertIn("Ёлочные часы", [item["name"] for item in categories])
        self.assertIn(
            "Ёлочные   часы",
            [item["name"].strip() for item in products],
        )

    def test_repeated_catalog_request_does_not_reuse_a_stale_window(self):
        url = (
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}&q=Новая%20позиция"
        ).format(self.brand["id"], self.duplicate_category_id)
        self.assertEqual(self.client.get(url).get_json()["data"], [])
        self._insert_products(
            count=1,
            brand_id=self.brand["id"],
            category_id=self.duplicate_category_id,
            prefix="Новая позиция",
        )
        refreshed = self.client.get(url).get_json()["data"]
        self.assertEqual(len(refreshed), 1)

    def test_category_options_preserve_canonical_ids_without_name_merge(self):
        options = self.client.get(
            "/api/v1/catalog/options?type=category&category_scope=brand"
            "&brand_id={}&limit=200".format(self.brand["id"])
        ).get_json()["data"]
        matching = [
            item for item in options
            if item["name"].strip().casefold() == "наручные часы"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0]["category_ids"],
            [self.duplicate_category_id],
        )
        self.assertEqual(matching[0]["id"], self.duplicate_category_id)

        global_options = self.client.get(
            "/api/v1/catalog/options?type=category&category_scope=all&limit=200"
        ).get_json()["data"]
        matching_global = [
            item for item in global_options
            if item["name"].strip().casefold() == "наручные часы"
        ]
        self.assertEqual(
            {item["id"] for item in matching_global},
            {self.category_id, self.duplicate_category_id},
        )

    def test_category_compatibility_groups_include_inactive_sales_aliases(self):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE erp_categories SET active = 0 WHERE id = ?",
                (self.duplicate_category_id,),
            )

        group = next(
            item for item in self.shared.category_compatibility_groups()
            if item["id"] == self.category_id
        )

        self.assertEqual(group["name"], "Наручные часы")
        self.assertEqual(
            group["category_ids"],
            [self.category_id, self.duplicate_category_id],
        )

    def test_selected_product_keeps_the_catalog_id(self):
        response = self.client.get(
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id={}&q=Barcelona%2000007".format(
                self.brand["id"], self.duplicate_category_id
            )
        ).get_json()["data"]
        self.assertEqual(len(response), 1)
        selected_id = response[0]["id"]
        self.assertEqual(self.shared.get_product(selected_id)["id"], selected_id)

    def test_uncategorized_products_are_filtered_by_selected_brand(self):
        self._insert_products(
            count=2,
            brand_id=self.brand["id"],
            category_id=None,
            prefix="Barcelona без категории",
        )
        self._insert_products(
            count=1,
            brand_id=self.other_brand["id"],
            category_id=None,
            prefix="Другой без категории",
        )

        response = self.client.get(
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id=0".format(self.brand["id"])
        ).get_json()
        names = [item["name"] for item in response["data"]]

        self.assertEqual(response["meta"]["total"], 2)
        self.assertEqual(
            names,
            [
                "Barcelona без категории 00000",
                "Barcelona без категории 00001",
            ],
        )
        self.assertNotIn("Другой без категории 00000", names)
        self.assertFalse(any(name.startswith("Barcelona 0") for name in names))

    def test_uncategorized_option_is_only_returned_for_a_brand_that_uses_it(self):
        without_uncategorized = self.client.get(
            "/api/v1/catalog/options?type=category&category_scope=brand"
            "&brand_id={}&limit=200".format(self.other_brand["id"])
        ).get_json()["data"]
        self.assertNotIn(0, [item["id"] for item in without_uncategorized])

        self._insert_products(
            count=1,
            brand_id=self.brand["id"],
            category_id=None,
            prefix="Без категории",
        )
        with_uncategorized = self.client.get(
            "/api/v1/catalog/options?type=category&category_scope=brand"
            "&brand_id={}&limit=200".format(self.brand["id"])
        ).get_json()["data"]
        uncategorized = [item for item in with_uncategorized if item["id"] == 0]

        self.assertEqual(len(uncategorized), 1)
        self.assertEqual(uncategorized[0]["name"], "Без категории")

    def test_uncategorized_product_search_uses_name_and_article(self):
        self._insert_products(
            count=2,
            brand_id=self.brand["id"],
            category_id=None,
            prefix="Uncategorized search",
        )
        base = (
            "/api/v1/catalog/options?type=product&limit=200"
            "&brand_id={}&category_id=0&q=".format(self.brand["id"])
        )

        by_name = self.client.get(
            base + "Uncategorized%20search%2000001"
        ).get_json()["data"]
        by_article = self.client.get(
            base + "UNCATEGORIZED%20SEARCH-00000"
        ).get_json()["data"]

        self.assertEqual([item["name"] for item in by_name], [
            "Uncategorized search 00001",
        ])
        self.assertEqual([item["name"] for item in by_article], [
            "Uncategorized search 00000",
        ])


if __name__ == "__main__":
    unittest.main()
