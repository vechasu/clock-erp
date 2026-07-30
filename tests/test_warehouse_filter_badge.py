import html
import re
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest import mock

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

    def render(self, query=""):
        response = self.client.get("/warehouse" + query)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def filter_button(self, markup):
        match = re.search(
            r'<button\s+id="warehouseFilterTrigger"(?P<attrs>.*?)>'
            r'(?P<body>.*?)</button>',
            markup,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        return match.group("attrs"), match.group("body")

    def badge_text(self, markup):
        _, button_body = self.filter_button(markup)
        match = re.search(
            r'class="erp-filter-count"[^>]*>\s*([^<]+?)\s*</span>',
            button_body,
            re.DOTALL,
        )
        return match.group(1).strip() if match else None

    def reset_button_attributes(self, markup):
        match = re.search(
            r'<button\s+id="warehouseFilterReset"(?P<attrs>.*?)>',
            markup,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        return match.group("attrs")

    def test_badge_is_hidden_without_panel_filters(self):
        markup = self.render()
        attributes, button_body = self.filter_button(markup)

        self.assertIsNone(self.badge_text(markup))
        self.assertNotIn(" is-active", attributes)
        self.assertIn("Открыть фильтры товаров", attributes)
        self.assertIn(" hidden", self.reset_button_attributes(markup))
        self.assertIn("erp-filter-icon-slot", button_body)
        self.assertIn("erp-filter-label", button_body)

    def test_brand_category_and_combination_counts(self):
        cases = (
            ("?brand=Casio", "1"),
            ("?category=Будильники", "1"),
            ("?brand=Casio&category=Будильники", "2"),
        )

        for query, expected in cases:
            with self.subTest(query=query):
                markup = self.render(query)
                attributes, button_body = self.filter_button(markup)
                plain_text = " ".join(
                    re.sub(r"<[^>]+>", " ", button_body).split()
                )

                self.assertEqual(self.badge_text(markup), expected)
                self.assertIn(" is-active", attributes)
                self.assertNotIn("Фильтры1", plain_text)
                self.assertNotIn(" hidden", self.reset_button_attributes(markup))

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
            markup = self.render(
                "?brand=A.B.+Art&brand_id=31"
                "&category=Наручные+часы&category_id=31&page=7"
            )

        arguments = self.list_products_mock.call_args.kwargs
        self.assertEqual(arguments["brand_id"], "31")
        self.assertEqual(arguments["category_id"], "31")
        self.assertEqual(arguments["brand"], "")
        self.assertEqual(arguments["category"], "")
        self.assertEqual(arguments["page"], 7)
        self.assertEqual(self.badge_text(markup), "2")

    def test_date_range_counts_as_one_filter(self):
        markup = self.render(
            "?brand=Casio&category=Будильники"
            "&date_from=2026-07-01&date_to=2026-07-29"
        )

        self.assertEqual(self.badge_text(markup), "3")
        attributes, _ = self.filter_button(markup)
        self.assertIn('title="Активно 3 фильтра"', attributes)
        self.assertIn(
            'aria-label="Фильтры. Активно 3 фильтра"',
            attributes,
        )

    def test_search_sort_and_stock_toggle_do_not_increase_count(self):
        excluded_only = (
            "?q=часы",
            "?sort_by=stock&sort_dir=desc",
            "?in_stock=1",
            "?q=часы&sort_by=stock&sort_dir=desc&in_stock=1",
        )

        for query in excluded_only:
            with self.subTest(query=query):
                markup = self.render(query)
                self.assertIsNone(self.badge_text(markup))
                self.assertIn(
                    " hidden",
                    self.reset_button_attributes(markup),
                )

        markup = self.render(
            "?brand=Casio&q=часы&sort_by=stock"
            "&sort_dir=desc&in_stock=1"
        )
        self.assertEqual(self.badge_text(markup), "1")

    def test_reset_clears_panel_filters_and_preserves_other_controls(self):
        markup = self.render(
            "?q=часы&brand=Casio&category=Будильники&cell=A-01"
            "&date_from=2026-07-01&date_to=2026-07-29"
            "&sort_by=stock&sort_dir=desc&per_page=100&in_stock=1"
        )
        link_match = re.search(
            r'class="category-link"\s+href="([^"]+)"',
            markup,
            re.DOTALL,
        )
        self.assertIsNotNone(link_match)
        reset_url = html.unescape(link_match.group(1))
        reset_params = parse_qs(urlsplit(reset_url).query)

        self.assertEqual(reset_params["q"], ["часы"])
        self.assertEqual(reset_params["sort_by"], ["stock"])
        self.assertEqual(reset_params["sort_dir"], ["desc"])
        self.assertEqual(reset_params["per_page"], ["100"])
        self.assertEqual(reset_params["in_stock"], ["1"])
        for name in ("brand", "category", "cell", "date_from", "date_to"):
            self.assertNotIn(name, reset_params)

        reset_markup = self.client.get(reset_url).get_data(as_text=True)
        self.assertIsNone(self.badge_text(reset_markup))
        self.assertIn(" hidden", self.reset_button_attributes(reset_markup))

        reset_function = re.search(
            r"function resetWarehouseTableFilters\(\) \{(.*?)\n        \}",
            markup,
            re.DOTALL,
        )
        self.assertIsNotNone(reset_function)
        reset_script = reset_function.group(1)
        for name in (
            "brand",
            "brand_id",
            "category",
            "category_id",
            "cell",
            "date_from",
            "date_to",
        ):
            self.assertIn(f'"{name}"', reset_script)
        for name in ("q", "sort_by", "sort_dir", "per_page", "in_stock"):
            self.assertNotIn(f'"{name}"', reset_script)

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

    def test_reload_restores_count_from_url(self):
        query = (
            "?brand=Casio&category=Будильники&cell=A-01"
            "&date_from=2026-07-01&date_to=2026-07-29"
            "&q=часы&sort_by=stock&sort_dir=desc"
            "&page=2&per_page=100&in_stock=1"
        )

        first_markup = self.render(query)
        reloaded_markup = self.render(query)

        self.assertEqual(self.badge_text(first_markup), "4")
        self.assertEqual(self.badge_text(reloaded_markup), "4")
        attributes, _ = self.filter_button(reloaded_markup)
        self.assertIn("Активно 4 фильтра", attributes)
