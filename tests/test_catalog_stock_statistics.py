import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)
from app.services.shared_catalog import SharedCatalog


class CountingDatabase:
    def __init__(self, database):
        self.database = database
        self.execute_count = 0

    def initialize(self):
        return self.database.initialize()

    @contextmanager
    def connect(self):
        with self.database.connect() as connection:
            owner = self

            class CountingConnection:
                def execute(self, *args, **kwargs):
                    owner.execute_count += 1
                    return connection.execute(*args, **kwargs)

            yield CountingConnection()


class CatalogStockStatisticsTest(unittest.TestCase):
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
                "excel_name": "Seed",
                "excel_brand": "Seed",
                "excel_article": "SEED",
                "article_quality": "code_like",
                "category": "Seed",
                "stock": 0.0,
                "stock_valid": True,
                "cell": "",
                "product_id": None,
                "match_status": "not_found",
                "match_method": "test",
                "confidence": 0,
                "alternatives": [],
            }],
            "a" * 64,
            "stock-statistics.xlsx",
        )
        self.catalog = ExcelProductCatalog(self.database)
        self.products = {
            "x_watch": self.catalog.create_product(
                name="X Watch", brand="X", category="Часы", stock=5,
            ),
            "x_strap": self.catalog.create_product(
                name="X Strap", brand="X", category="Ремни", stock=2,
            ),
            "y_watch": self.catalog.create_product(
                name="Y Watch", brand="Y", category="Часы", stock=3,
            ),
            "numeric": self.catalog.create_product(
                name="Numeric Watch",
                brand="666 Barcelona",
                category="Часы",
                stock=12480,
            ),
            "zero": self.catalog.create_product(
                name="Zero Watch", brand="Zero", category="Часы", stock=0,
            ),
            "unassigned": self.catalog.create_product(
                name="Unassigned", brand="Temporary", category="Temporary",
                stock=1,
            ),
        }
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET stock = 12480.5 WHERE id = ?",
                (self.products["numeric"]["id"],),
            )
            connection.execute(
                "UPDATE catalog_excel_products SET brand_id = NULL, "
                "category_id = NULL, excel_brand = '', excel_category = '', "
                "stock = -1.25 WHERE id = ?",
                (self.products["unassigned"]["id"],),
            )
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.client = web.app.test_client()

    def tearDown(self):
        self.environment.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp.cleanup()

    def options(self, kind, **parameters):
        response = self.client.get(
            "/api/v1/catalog/options",
            query_string={"type": kind, "limit": 100, **parameters},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["data"]

    @staticmethod
    def by_name(options):
        return {item["name"]: item for item in options}

    def test_brand_stock_totals_preserve_product_count_contract(self):
        brands = self.by_name(self.options("brand"))
        self.assertEqual(
            (brands["X"]["stock_total"], brands["X"]["stock_display"]),
            (7, "7"),
        )
        self.assertEqual((brands["X"]["product_count"], brands["X"]["count"]), (2, 2))
        self.assertEqual(brands["Y"]["stock_display"], "3")
        self.assertEqual(brands["Zero"]["stock_display"], "0")
        self.assertEqual(brands["666 Barcelona"]["stock_display"], "12 480.5")
        self.assertEqual(brands["Без бренда"]["stock_display"], "-1.25")

    def test_category_totals_are_global_then_intersect_selected_brand(self):
        global_categories = self.by_name(self.options("category"))
        self.assertEqual(global_categories["Часы"]["stock_total"], 12488.5)
        self.assertEqual(global_categories["Ремни"]["stock_display"], "2")
        self.assertEqual(global_categories["Без категории"]["stock_display"], "-1.25")

        brands = self.by_name(self.options("brand"))
        x_categories = self.by_name(self.options(
            "category",
            brand_id=brands["X"]["id"],
            category_scope="brand",
        ))
        y_categories = self.by_name(self.options(
            "category",
            brand_id=brands["Y"]["id"],
            category_scope="brand",
        ))
        self.assertEqual(
            {name: item["stock_total"] for name, item in x_categories.items()},
            {"Ремни": 2, "Часы": 5},
        )
        self.assertEqual(
            {name: item["stock_total"] for name, item in y_categories.items()},
            {"Часы": 3},
        )

    def test_products_show_their_own_current_stock(self):
        brands = self.by_name(self.options("brand"))
        categories = self.by_name(self.options(
            "category",
            brand_id=brands["X"]["id"],
            category_scope="brand",
        ))
        products = self.by_name(self.options(
            "product",
            brand_id=brands["X"]["id"],
            category_id=categories["Часы"]["id"],
        ))
        self.assertEqual(products["X Watch"]["stock_display"], "5")
        self.assertNotEqual(products["X Watch"]["stock_display"], "1")

    def test_each_aggregate_uses_one_database_query(self):
        for method, arguments in (
            ("list_brands", {}),
            ("list_categories", {}),
            ("list_category_options", {}),
        ):
            with self.subTest(method=method):
                counting = CountingDatabase(self.database)
                getattr(SharedCatalog(counting), method)(limit=100, **arguments)
                self.assertEqual(counting.execute_count, 1)

    def test_all_three_active_sections_use_the_shared_stock_component(self):
        component = Path("app/static/js/catalog-combobox.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("sharedCatalogStockDisplay", component)
        self.assertIn(' + " ед."', component)
        self.assertNotIn(
            '(item.product_count ?? item.count ?? "")',
            component,
        )
        for template in ("warehouse.html", "sales.html", "receipts.html"):
            with self.subTest(template=template):
                source = Path("app/templates", template).read_text(
                    encoding="utf-8"
                )
                self.assertIn('shared_catalog_kind="brand"', source)
                self.assertIn('shared_catalog_kind="category"', source)


if __name__ == "__main__":
    unittest.main()
