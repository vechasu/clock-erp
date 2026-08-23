import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.shared_catalog import SharedCatalog


ROOT = Path(__file__).resolve().parents[1]


class ProductFilterHierarchyTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.database_path)}
        )
        self.environment.start()
        self.database = CatalogDatabase(self.database_path)
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,"
                "total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES ('batch','sha','filters.xlsx',0,0,0,0,'active',?,?)",
                ("2026-08-23T09:00:00+00:00", "2026-08-23T09:00:00+00:00"),
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.shared = SharedCatalog(self.database)
        self.x_classic = self.catalog.create_product(
            name="Classic Model X Steel", article="X-1", brand="Ziro",
            category="Наручные часы", model="Model X", stock=3,
        )
        self.x_sport = self.catalog.create_product(
            name="Sport Model X", article="X-2", brand="Ziro",
            category="Наручные часы", model="Model X", stock=0,
        )
        self.y_classic = self.catalog.create_product(
            name="Classic Model Y", article="Y-1", brand="Ziro",
            category="Наручные часы", model="Model Y", stock=2,
        )
        self.strap = self.catalog.create_product(
            name="Classic Strap", article="S-1", brand="Ziro",
            category="Ремешки", model="Strap One", stock=1,
        )
        self.other_watch = self.catalog.create_product(
            name="Classic Other", article="O-1", brand="Other",
            category="Наручные часы", model="Other Model", stock=4,
        )
        self.empty_category = self.shared.create_category(
            self.x_classic["brand_id"], "Пустая категория"
        )
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def test_brand_returns_only_categories_backed_by_real_products(self):
        options = self.shared.list_category_options(
            brand_id=self.x_classic["brand_id"], only_used_by_brand=True,
            limit=200,
        )
        names = {item["name"].strip().casefold() for item in options}
        self.assertEqual(names, {"наручные часы", "ремешки"})
        self.assertNotIn(self.empty_category["name"].casefold(), names)

    def test_shared_canonical_category_id_combines_with_exact_brand(self):
        self.assertEqual(
            self.x_classic["category_id"], self.other_watch["category_id"]
        )
        ziro = self.catalog.list_products(
            brand_id=self.x_classic["brand_id"],
            category_id=self.other_watch["category_id"], per_page=50,
        )
        other = self.catalog.list_products(
            brand_id=self.other_watch["brand_id"],
            category_id=self.x_classic["category_id"], per_page=50,
        )
        self.assertEqual(ziro["total"], 3)
        self.assertEqual(other["total"], 1)

    def test_brand_category_returns_only_its_structured_models(self):
        options = self.shared.list_model_options(
            self.x_classic["brand_id"], self.x_classic["category_id"], limit=50
        )
        self.assertEqual(
            {item["name"]: item["product_count"] for item in options},
            {"Model X": 2, "Model Y": 1},
        )

    def test_impossible_brand_category_and_model_are_rejected_by_backend(self):
        category_response = self.client.get(
            "/api/v1/catalog/options?type=model&brand_id={}&category_id={}".format(
                self.x_classic["brand_id"], self.empty_category["id"]
            )
        )
        self.assertEqual(category_response.status_code, 422)
        model_response = self.client.post(
            "/api/v1/inventories",
            json={
                "brand_id": self.x_classic["brand_id"],
                "category_id": self.x_classic["category_id"],
                "model_id": self.other_watch["model_id"],
            },
        )
        self.assertEqual(model_response.status_code, 400)

    def test_brand_category_model_and_search_are_combined_with_and_semantics(self):
        model_x = self.catalog.list_products(
            brand_id=self.x_classic["brand_id"],
            category_id=self.x_classic["category_id"],
            model_id=self.x_classic["model_id"], per_page=50,
        )
        classic_x = self.catalog.list_products(
            query="Classic", brand_id=self.x_classic["brand_id"],
            category_id=self.x_classic["category_id"],
            model_id=self.x_classic["model_id"], per_page=50,
        )
        brand_only = self.catalog.list_products(
            brand_id=self.x_classic["brand_id"], per_page=50
        )
        category_only = self.catalog.list_products(
            brand_id=self.x_classic["brand_id"],
            category_id=self.x_classic["category_id"], per_page=50,
        )
        self.assertEqual(model_x["total"], 2)
        self.assertEqual(classic_x["total"], 1)
        self.assertEqual(brand_only["total"], 4)
        self.assertEqual(category_only["total"], 3)

    def test_stale_url_descendants_are_cleared_server_side(self):
        page = self.client.get(
            "/warehouse?brand_id={}&category_id={}&model_id={}".format(
                self.x_classic["brand_id"], self.empty_category["id"],
                self.other_watch["model_id"],
            )
        )
        markup = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        filter_markup = markup.split('id="filterBrandCombobox"', 1)[1].split(
            'class="drawer-actions"', 1
        )[0]
        self.assertNotIn(
            'data-shared-catalog-selected-id="{}"'.format(
                self.empty_category["id"]
            ), filter_markup,
        )
        self.assertNotIn(
            'data-shared-catalog-selected-id="{}"'.format(
                self.other_watch["model_id"]
            ), filter_markup,
        )

    def test_frontend_order_resets_and_stale_request_guards_are_present(self):
        template = (ROOT / "app/templates/warehouse.html").read_text()
        script = (ROOT / "app/static/js/catalog-combobox.js").read_text()
        brand = template.index('"filterBrandCombobox"')
        category = template.index('"filterCategoryCombobox"')
        model = template.index('"filterModelCombobox"')
        self.assertLess(brand, category)
        self.assertLess(category, model)
        self.assertIn("clearSharedCatalogCombobox(category)", script)
        self.assertIn("clearSharedCatalogCombobox(model)", script)
        self.assertIn("requestControllers.get(combobox)?.abort()", script)
        self.assertIn("requestControllers.delete(combobox)", script)
        self.assertIn("requestControllers.get(combobox) !== controller", script)
        self.assertIn('kind === "model"', script)

    def test_stock_search_empty_and_query_state_preserve_hierarchy(self):
        in_stock = self.catalog.list_products(
            query="Classic", brand_id=self.x_classic["brand_id"],
            category_id=self.x_classic["category_id"],
            model_id=self.x_classic["model_id"], stock_state="in",
            per_page=50,
        )
        out_of_stock = self.catalog.list_products(
            brand_id=self.x_classic["brand_id"],
            category_id=self.x_classic["category_id"],
            model_id=self.x_classic["model_id"], stock_state="out",
            per_page=50,
        )
        empty = self.client.get(
            "/warehouse?brand_id={}&category_id={}&model_id={}"
            "&stock_state=in&q=missing-value".format(
                self.x_classic["brand_id"],
                self.x_classic["category_id"],
                self.x_classic["model_id"],
            )
        )
        markup = empty.get_data(as_text=True)
        self.assertEqual(in_stock["total"], 1)
        self.assertEqual(out_of_stock["total"], 1)
        self.assertEqual(empty.status_code, 200)
        self.assertIn("товары не найдены", markup)
        self.assertIn('value="missing-value"', markup)
        self.assertIn(
            'data-shared-catalog-selected-id="{}"'.format(
                self.x_classic["model_id"]
            ),
            markup,
        )

    def test_model_options_have_normal_empty_state(self):
        no_model = self.catalog.create_product(
            name="Model-free accessory", article="NO-MODEL", brand="Ziro",
            category="Без модели", model="", stock=1,
        )
        response = self.client.get(
            "/api/v1/catalog/options?type=model&brand_id={}"
            "&category_id={}".format(
                no_model["brand_id"], no_model["category_id"]
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"], [])


if __name__ == "__main__":
    unittest.main()
