import unittest
from unittest import mock
from pathlib import Path

from app import web


class WarehouseFilterBadgeTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.list_products_mock = mock.Mock(return_value={
            "items": [],
            "total": 0,
            "page": 1,
            "per_page": 50,
            "pages": 0,
            "brand_groups": [],
            "category_groups": [],
            "cell_groups": [],
            "stats": {"total_stock": 0},
        })
        self.patches = [
            mock.patch.object(
                web.ExcelProductCatalog,
                "list_products",
                self.list_products_mock,
            ),
            mock.patch.object(
                web,
                "load_catalog_taxonomy",
                return_value={"brands": [], "categories": []},
            ),
            mock.patch.object(web, "get_catalog_stock_history", return_value=[]),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def api_request(self, query=""):
        self.list_products_mock.reset_mock()
        response = self.client.get("/api/v1/products" + query)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsNone(payload["error"])
        return self.list_products_mock.call_args.kwargs

    def test_warehouse_jinja_route_applies_all_filter_state(self):
        query = (
            "?q=часы&brand_id=31&category_id=47&cell=A-01"
            "&date_from=2026-07-01&date_to=2026-07-29"
            "&sort_by=stock&sort_dir=desc&page=2&per_page=100&in_stock=1"
        )
        response = self.client.get("/warehouse" + query)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.location)
        arguments = self.list_products_mock.call_args.kwargs
        self.assertEqual(arguments["query"], "часы")
        self.assertEqual(arguments["brand_id"], "31")
        self.assertEqual(arguments["category_id"], "47")
        self.assertEqual(arguments["cell"], "A-01")
        self.assertEqual(arguments["created_from"], "2026-07-01")
        self.assertEqual(arguments["created_to"], "2026-07-29")
        self.assertEqual(arguments["sort_by"], "stock")
        self.assertEqual(arguments["sort_dir"], "desc")
        self.assertEqual(arguments["page"], 2)
        self.assertEqual(arguments["per_page"], 100)
        self.assertTrue(arguments["hide_zero"])
        markup = response.get_data(as_text=True)
        self.assertIn('<div class="app">', markup)
        self.assertNotIn("/app/products", markup)

    def test_canonical_brand_and_category_ids_are_applied_together(self):
        with mock.patch.object(
            web.SharedCatalog,
            "list_brands",
            return_value=[{
                "id": 31,
                "name": "A.B. Art",
                "active": True,
                "product_count": 71,
            }],
        ), mock.patch.object(
            web.SharedCatalog,
            "list_categories",
            return_value=[{
                "id": 31,
                "brand_id": 31,
                "name": "Наручные часы",
                "brand_name": "A.B. Art",
                "active": True,
                "product_count": 71,
            }],
        ):
            arguments = self.api_request(
                "?brand=A.B.+Art&brand_id=31"
                "&category=Наручные+часы&category_id=31&page=7"
            )

        self.assertEqual(arguments["brand_id"], "31")
        self.assertEqual(arguments["category_id"], "31")
        self.assertEqual(arguments["brand"], "A.B. Art")
        self.assertEqual(arguments["category"], "Наручные часы")
        self.assertEqual(arguments["page"], 7)
        self.assertEqual(arguments["per_page"], 50)

    def test_api_maps_search_sort_stock_date_and_pagination(self):
        arguments = self.api_request(
            "?q=часы&cell=A-01&date_from=2026-07-01&date_to=2026-07-29"
            "&sort_by=stock&sort_dir=desc&page=2&per_page=100&in_stock=1"
        )
        self.assertEqual(arguments["query"], "часы")
        self.assertEqual(arguments["cell"], "A-01")
        self.assertEqual(arguments["created_from"], "2026-07-01")
        self.assertEqual(arguments["created_to"], "2026-07-29")
        self.assertEqual(arguments["sort_by"], "stock")
        self.assertEqual(arguments["sort_dir"], "desc")
        self.assertEqual(arguments["page"], 2)
        self.assertEqual(arguments["per_page"], 100)
        self.assertTrue(arguments["hide_zero"])

    def test_react_filter_badge_counts_only_panel_filters(self):
        source = (
            Path(web.PROJECT_ROOT)
            / "frontend/src/features/products/ProductsPage.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "['brand_id', 'category_id', 'product_id', 'cell', 'date_from', 'date_to']",
            source,
        )
        self.assertNotIn(
            "['q', 'sort_by', 'sort_dir', 'in_stock']",
            source,
        )

    def test_badge_caps_values_above_nine(self):
        with web.app.app_context():
            component = web.app.jinja_env.get_template(
                "_filter_count.html"
            ).module
            markup = str(component.render_filter_count(12))

        self.assertIn(">9+<", markup)
        self.assertNotIn(">12<", markup)

    def test_russian_filter_tooltips_use_correct_forms(self):
        expected = {
            1: "Активен 1 фильтр",
            2: "Активно 2 фильтра",
            5: "Активно 5 фильтров",
            11: "Активно 11 фильтров",
            21: "Активен 21 фильтр",
        }

        for count, label in expected.items():
            with self.subTest(count=count):
                self.assertEqual(
                    web.format_active_filter_label(count),
                    label,
                )

    def test_reload_preserves_filter_query_for_jinja_route(self):
        query = (
            "?brand_id=31&category_id=47&cell=A-01"
            "&date_from=2026-07-01&date_to=2026-07-29"
            "&q=часы&sort_by=stock&sort_dir=desc"
            "&page=2&per_page=100&in_stock=1"
        )
        first = self.client.get("/warehouse" + query)
        first_arguments = dict(self.list_products_mock.call_args.kwargs)
        reloaded = self.client.get("/warehouse" + query)
        reloaded_arguments = dict(self.list_products_mock.call_args.kwargs)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(reloaded.status_code, 200)
        self.assertEqual(first_arguments, reloaded_arguments)
        for response in (first, reloaded):
            markup = response.get_data(as_text=True)
            self.assertIn('name="brand_id" value="31"', markup)
            self.assertIn('name="category_id" value="47"', markup)
            self.assertIn('name="cell" value="A-01"', markup)
            self.assertIn('name="date_from" value="2026-07-01"', markup)
            self.assertIn('name="date_to" value="2026-07-29"', markup)
            self.assertNotIn("/app/products", markup)
