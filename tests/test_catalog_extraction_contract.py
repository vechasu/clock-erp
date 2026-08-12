import ast
from pathlib import Path
import unittest

from flask import url_for

from app import web


class CatalogExtractionContractTest(unittest.TestCase):
    def test_catalog_application_is_independent_from_flask_and_web(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app/catalog/application.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertFalse(
            any(
                module == "flask"
                or module.startswith("flask.")
                or module == "app.web"
                for module in imported_modules
            )
        )

    def test_management_routes_keep_paths_methods_and_endpoints(self):
        expected = {
            "/warehouse/categories": (
                "warehouse_create_global_category", "POST"
            ),
            "/warehouse/categories/7/rename": (
                "warehouse_rename_category", "POST"
            ),
            "/warehouse/categories/7/delete": (
                "warehouse_delete_global_category", "POST"
            ),
            "/api/v1/category-overviews": (
                "api_category_overviews", "GET"
            ),
            "/api/v1/category-overviews/7/delete-plan": (
                "api_category_delete_plan", "GET"
            ),
            "/warehouse/brands": ("warehouse_create_brand", "POST"),
            "/warehouse/brands/5/rename": (
                "warehouse_rename_brand", "POST"
            ),
            "/warehouse/brands/5/categories": (
                "warehouse_create_brand_category", "POST"
            ),
            "/warehouse/brands/5/categories/7/rename": (
                "warehouse_rename_global_category", "POST"
            ),
            "/warehouse/brands/5/delete": (
                "warehouse_delete_brand", "POST"
            ),
            "/warehouse/brands/5/categories/7/delete": (
                "warehouse_delete_brand_category", "POST"
            ),
        }
        adapter = web.app.url_map.bind("")
        for path, (endpoint, method) in expected.items():
            with self.subTest(path=path):
                self.assertEqual(
                    adapter.match(path, method=method)[0],
                    endpoint,
                )

    def test_catalog_api_aliases_keep_historical_dispatch(self):
        adapter = web.app.url_map.bind("")
        expected = {
            ("/api/v1/brands", "GET"): "api_brand_overviews",
            ("/api/v1/brands", "POST"): "api_brands_collection",
            ("/api/brands", "GET"): "api_brands_collection",
            ("/api/brands", "POST"): "api_brands_collection",
            ("/api/v1/categories", "GET"): "api_categories_collection",
            ("/api/v1/categories", "POST"): "api_categories_collection",
            ("/api/categories", "GET"): "api_categories_collection",
            ("/api/categories", "POST"): "api_categories_collection",
            ("/api/v1/brands/5", "PATCH"): "api_brand_resource",
            ("/api/v1/brands/5", "DELETE"): "api_brand_resource",
            ("/api/brands/5", "PATCH"): "api_brand_resource",
            ("/api/brands/5", "DELETE"): "api_brand_resource",
            ("/api/v1/categories/7", "PATCH"): "api_category_resource",
            ("/api/v1/categories/7", "DELETE"): "api_category_resource",
            ("/api/categories/7", "PATCH"): "api_category_resource",
            ("/api/categories/7", "DELETE"): "api_category_resource",
            ("/api/v1/catalog/options", "GET"): "api_catalog_options",
            ("/api/catalog/options", "GET"): "api_catalog_options",
            ("/api/v1/catalog/duplicates", "GET"): "api_catalog_duplicates",
            ("/api/catalog/duplicates", "GET"): "api_catalog_duplicates",
        }
        for (path, method), endpoint in expected.items():
            with self.subTest(path=path, method=method):
                self.assertEqual(
                    adapter.match(path, method=method)[0],
                    endpoint,
                )

    def test_url_for_identity_and_versioned_alias_preference_are_stable(self):
        with web.app.test_request_context():
            self.assertEqual(
                url_for("warehouse_create_brand"),
                "/warehouse/brands",
            )
            self.assertEqual(
                url_for("warehouse_create_global_category"),
                "/warehouse/categories",
            )
            self.assertEqual(
                url_for("api_brands_collection"),
                "/api/v1/brands",
            )
            self.assertEqual(
                url_for("api_categories_collection"),
                "/api/v1/categories",
            )
            self.assertEqual(
                url_for("api_catalog_options"),
                "/api/v1/catalog/options",
            )

if __name__ == "__main__":
    unittest.main()
