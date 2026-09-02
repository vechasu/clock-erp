import unittest
from unittest import mock

from app import web


def sale(
    sale_id,
    *,
    brand_id="b1",
    brand="Luch",
    category_id="c1",
    category="Наручные часы",
    product_id="p1",
    product="Луч Классика",
    article="FD41",
    source="tictactoy",
    status="completed",
    created_at="2026-08-05T12:00:00",
    quantity=1,
):
    return {
        "id": sale_id,
        "brand_id": brand_id,
        "brand": brand,
        "category_id": category_id,
        "category": category,
        "product_id": product_id,
        "product_name": product,
        "article": article,
        "source_key": source,
        "source": web.SALES_SOURCE_LABELS[source],
        "order_status": status,
        "created_at": created_at,
        "quantity_value": quantity,
        "net_quantity_value": quantity,
        "total_amount": quantity * 100,
        "order_number": f"order-{sale_id}",
        "is_cancelled": status == "cancelled",
        "sale_type": "manual",
    }


class SalesServerFiltersTest(unittest.TestCase):
    def setUp(self):
        self.sales = [
            sale("1"),
            sale(
                "2",
                category_id=0,
                category="",
                product_id="p2",
                product="Луч без категории",
                source="wildberries",
                status="cancelled",
                quantity=3,
            ),
            sale(
                "3",
                brand_id="b2",
                brand="Исторический бренд",
                category_id="old-category",
                category="Удалённая категория",
                product_id="deleted-product",
                product="Старое название товара",
                source="amazon",
                created_at="2025-01-02T12:00:00",
            ),
        ]

    def test_individual_and_combined_filters_use_sale_snapshots(self):
        base = {"source": "all"}
        cases = {
            "brand_id": ("b1", {"1", "2"}),
            "category_id": ("0", {"2"}),
            "product_id": ("deleted-product", {"3"}),
            "status": ("cancelled", {"2"}),
            "source": ("wildberries", {"2"}),
        }
        for field, (value, expected) in cases.items():
            with self.subTest(field=field):
                filters = {**base, field: value}
                result = web.filter_sales_report_records(
                    self.sales, filters
                )
                self.assertEqual({item["id"] for item in result}, expected)

        result = web.filter_sales_report_records(
            self.sales,
            {
                "source": "wildberries",
                "brand_id": "b1",
                "category_id": "0",
                "product_id": "p2",
                "status": "cancelled",
                "q": "без категории",
                "date_from": "2026-08-01",
                "date_to": "2026-08-06",
            },
        )
        self.assertEqual([item["id"] for item in result], ["2"])

    def test_zero_category_and_historical_snapshot_options_are_stable(self):
        catalog = web.build_sales_filter_catalog(self.sales)
        uncategorized = next(
            item for item in catalog if item["product_id"] == "p2"
        )
        self.assertEqual(uncategorized["category_id"], "0")
        self.assertEqual(uncategorized["category"], "Без категории")
        historical = next(
            item
            for item in catalog
            if item["product_id"] == "deleted-product"
        )
        self.assertEqual(historical["brand"], "Исторический бренд")
        self.assertEqual(historical["category"], "Удалённая категория")

    def test_filter_catalog_keeps_article_and_duplicate_names_by_id(self):
        duplicate_names = [
            sale(
                "sku-one",
                product_id="product-one",
                product="Example Watch",
                article="SKU123",
            ),
            sale(
                "sku-two",
                product_id="product-two",
                product="Example Watch",
                article="SKU999",
            ),
        ]

        catalog = web.build_sales_filter_catalog(duplicate_names)

        self.assertEqual(
            {(item["product_id"], item["article"]) for item in catalog},
            {("product-one", "SKU123"), ("product-two", "SKU999")},
        )

    def test_snapshot_identifier_is_compatible_with_production_python(self):
        self.assertEqual(
            web.get_sale_filter_identifier(
                {"brand": "Luch"}, "brand_id", "brand"
            ),
            "snapshot:brand:luch",
        )
        source = (web.PROJECT_ROOT / "app" / "web.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".removesuffix(", source)

    def test_dependent_options_do_not_mix_brands_or_categories(self):
        options = web.build_sales_filter_options(
            self.sales,
            {"brand_id": "b1", "category_id": "0"},
        )
        self.assertEqual(
            {item["value"] for item in options["categories"]},
            {"c1", "0"},
        )
        self.assertEqual(
            [item["value"] for item in options["products"]],
            ["p2"],
        )

    def test_exact_brand_duplicate_uses_one_id_backed_option(self):
        duplicate_sales = [
            sale("id-backed", brand_id=9, brand="Aark"),
            sale(
                "snapshot",
                brand_id=None,
                brand="AARK",
                product_id="legacy-product",
            ),
        ]

        options = web.build_sales_filter_options(
            duplicate_sales, {"brand_id": "", "category_id": ""}
        )

        self.assertEqual(options["brands"], [{
            "value": "9",
            "label": "Aark",
        }])
        filtered = web.filter_sales_report_records(
            duplicate_sales, {"source": "all", "brand_id": "9"}
        )
        self.assertEqual({item["id"] for item in filtered}, {
            "id-backed", "snapshot",
        })

    def test_explicit_brand_alias_combines_results_and_sources(self):
        alias_sales = [
            sale(
                "legacy-{}".format(index),
                brand_id=None,
                brand="A B ART",
                source="tictactoy",
            )
            for index in range(3)
        ] + [
            sale(
                "canonical-{}".format(index),
                brand_id=31,
                brand="A.B. Art",
                source=("amazon" if index < 4 else "wildberries"),
            )
            for index in range(7)
        ]
        logical_value = "logical:brand:a-b-art"

        options = web.build_sales_filter_options(
            alias_sales, {"brand_id": "", "category_id": ""}
        )
        self.assertEqual(options["brands"], [{
            "value": logical_value,
            "label": "A.B. Art",
        }])
        for source, expected_count in (
            ("all", 10),
            ("tictactoy", 3),
            ("wildberries", 3),
            ("amazon", 4),
        ):
            filtered = web.filter_sales_report_records(alias_sales, {
                "source": source,
                "brand_id": logical_value,
            })
            self.assertEqual(len(filtered), expected_count)

        legacy_options = web.build_sales_filter_options(
            alias_sales,
            {"brand_id": "31", "category_id": ""},
        )
        self.assertTrue(legacy_options["categories"])
        legacy_filtered = web.filter_sales_report_records(alias_sales, {
            "source": "all",
            "brand_id": "snapshot:brand:a b art",
        })
        self.assertEqual(len(legacy_filtered), 10)

    def test_brand_filter_does_not_rewrite_historical_snapshots(self):
        alias_sales = [
            sale("legacy", brand_id=None, brand="A B ART"),
            sale("canonical", brand_id=31, brand="A.B. Art"),
        ]

        web.filter_sales_report_records(alias_sales, {
            "source": "all",
            "brand_id": "logical:brand:a-b-art",
        })

        self.assertEqual(
            [item["brand"] for item in alias_sales],
            ["A B ART", "A.B. Art"],
        )

    def test_punctuation_collision_is_not_merged_without_explicit_alias(self):
        distinct_sales = [
            sale("first", brand_id=41, brand="AC-ME"),
            sale("second", brand_id=42, brand="AC.ME"),
        ]

        options = web.build_sales_filter_options(
            distinct_sales, {"brand_id": "", "category_id": ""}
        )

        self.assertEqual(
            {item["value"] for item in options["brands"]},
            {"41", "42"},
        )

    def test_duplicate_category_ids_are_one_logical_server_filter(self):
        duplicate_sales = [
            sale("10", category_id=10, product_id="p10", product="Модель 10"),
            sale("25", category_id=25, product_id="p25", product="Модель 25"),
            sale(
                "25-b",
                brand_id="b2",
                brand="Second",
                category_id=25,
                product_id="p25-b",
                product="Модель 25 B",
            ),
        ]
        category_groups = [{
            "id": 10,
            "name": "Наручные часы",
            "normalized_name": "наручные часы",
            "category_ids": [10, 25],
        }]

        options = web.build_sales_filter_options(
            duplicate_sales,
            {"brand_id": "", "category_id": "10"},
            category_groups=category_groups,
        )
        self.assertEqual(options["categories"], [{
            "value": "10",
            "label": "Наручные часы",
        }])
        self.assertEqual(
            {item["value"] for item in options["products"]},
            {"p10", "p25", "p25-b"},
        )
        filtered = web.filter_sales_report_records(
            duplicate_sales,
            {"source": "all", "category_id": "10"},
            category_groups=category_groups,
        )
        self.assertEqual({item["id"] for item in filtered}, {
            "10", "25", "25-b",
        })
        legacy_url = web.filter_sales_report_records(
            duplicate_sales,
            {"source": "all", "category_id": "25"},
            category_groups=category_groups,
        )
        self.assertEqual({item["id"] for item in legacy_url}, {
            "10", "25", "25-b",
        })

    def test_duplicate_category_group_respects_brand_cascade(self):
        duplicate_sales = [
            sale("a", category_id=10, product_id="pa", product="A"),
            sale(
                "b",
                brand_id="b2",
                brand="Second",
                category_id=25,
                product_id="pb",
                product="B",
            ),
        ]
        groups = [{
            "id": 10,
            "name": "Наручные часы",
            "category_ids": [10, 25],
        }]
        options = web.build_sales_filter_options(
            duplicate_sales,
            {"brand_id": "b2", "category_id": "10"},
            category_groups=groups,
        )

        self.assertEqual(
            [item["value"] for item in options["categories"]],
            ["10"],
        )
        self.assertEqual(
            [item["value"] for item in options["products"]],
            ["pb"],
        )

    def test_uncategorized_is_not_the_all_categories_state(self):
        options = web.build_sales_filter_options(
            self.sales,
            {"brand_id": "b1", "category_id": "0"},
        )
        self.assertEqual(
            [item for item in options["categories"] if item["value"] == "0"],
            [{"value": "0", "label": "Без категории"}],
        )
        self.assertNotIn("", {
            item["value"] for item in options["categories"]
        })

    def test_unknown_value_returns_empty_without_error(self):
        result = web.filter_sales_report_records(
            self.sales,
            {"source": "all", "brand_id": "missing"},
        )
        self.assertEqual(result, [])

    def test_sales_page_filters_once_updates_kpi_and_preserves_urls(self):
        with web.app.test_request_context(
            "/app/sales?tab=all&source=wildberries&brand_id=b1&category_id=0&product_id=p2&status=cancelled&q=без&date_from=2026-08-01&date_to=2026-08-06&sort=product_name&sort_dir=asc"
        ), mock.patch.object(
            web, "get_warehouse_items", return_value=[]
        ), mock.patch.object(
            web,
            "build_sales_report_records",
            return_value=self.sales,
        ) as builder, mock.patch.object(
            web, "render_template", side_effect=lambda name, **ctx: ctx
        ):
            context = web.sales_page()

        builder.assert_called_once_with(warehouse_items=[])
        self.assertEqual([item["id"] for item in context["sales"]], ["2"])
        self.assertEqual(context["total_sales"], 1)
        self.assertEqual(context["total_cancelled"], 1)
        self.assertIn("brand_id=b1", context["report_url"])
        self.assertIn("category_id=0", context["report_url"])
        self.assertIn("status=cancelled", context["report_url"])
        self.assertNotIn("sort=", context["report_url"])
        for tab in context["source_tabs"]:
            self.assertIn("brand_id=b1", tab["url"])
            self.assertIn("sort=product_name", tab["url"])

    def test_report_and_exports_share_server_filters(self):
        with web.app.test_request_context(
            "/sales/report?source=wildberries&brand_id=b1&category_id=0&product_id=p2&status=cancelled"
        ), mock.patch.object(
            web, "build_sales_report_records", return_value=self.sales
        ) as builder:
            context = web.build_sales_report_context()

        builder.assert_called_once_with()
        self.assertEqual([item["id"] for item in context["sales"]], ["2"])
        self.assertEqual(context["total_records"], 1)
        self.assertEqual(context["total_cancelled"], 1)

        with web.app.test_request_context(
            "/sales/report?tab=all&source=wildberries"
            "&brand_id=b1&category_id=0&product_id=p2"
            "&status=cancelled"
        ), mock.patch.object(
            web, "build_sales_report_records", return_value=self.sales
        ):
            body = web.sales_report_page()
        self.assertIn("brand_id=b1", body)
        self.assertIn("category_id=0", body)
        self.assertIn("product_id=p2", body)
        self.assertIn("status=cancelled", body)

    def test_builder_prefers_saved_snapshot_and_preserves_zero_category(self):
        stored = {
            "id": "historical",
            "product_id": "p1",
            "product_name": "Старое название",
            "brand_id": "old-brand",
            "brand": "Старый бренд",
            "category_id": 0,
            "category": "",
            "source": "Tictactoy",
            "quantity": 1,
            "created_at": "2026-08-01",
        }
        current = {
            "id": "p1",
            "name": "Новое название",
            "brand_id": "new-brand",
            "brand": "Новый бренд",
            "category_id": "new-category",
            "category": "Новая категория",
        }
        result = web.build_sales_report_records(
            warehouse_items=[current],
            operations=[],
            stored_manual_sales=[stored],
            automatic_overrides={},
        )[0]
        self.assertEqual(result["product_name"], "Старое название")
        self.assertEqual(result["brand_id"], "old-brand")
        self.assertEqual(result["brand"], "Старый бренд")
        self.assertEqual(str(result["category_id"]), "0")


if __name__ == "__main__":
    unittest.main()
