import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import auth, web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductBatchService


def product_result(row, name, stock, brand, category, cell):
    return {
        "excel_row": row,
        "excel_name": name,
        "excel_brand": brand,
        "excel_article": "ART-{}".format(row),
        "article_quality": "code_like",
        "category": category,
        "stock": float(stock),
        "stock_valid": True,
        "cell": cell,
        "product_id": None,
        "match_status": "not_found",
        "match_method": "test",
        "confidence": 0,
        "alternatives": [],
    }


class Stage2ProductsApiTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "catalog.db"
        self.taxonomy_path = self.root / "catalog_taxonomy.json"
        self.environment = mock.patch.dict(
            "os.environ",
            {"CATALOG_DATABASE_PATH": str(self.database_path)},
        )
        self.environment.start()
        self.taxonomy_patch = mock.patch.object(
            web,
            "CATALOG_TAXONOMY_PATH",
            self.taxonomy_path,
        )
        self.taxonomy_patch.start()
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        ExcelProductBatchService(CatalogDatabase(self.database_path)).apply(
            [
                product_result(2, "Alpha Watch", 5, "Alpha", "Часы", "A-1"),
                product_result(3, "Beta Strap", 0, "Beta", "Ремешки", ""),
                product_result(4, "Alpha Strap", 2, "Alpha", "Ремешки", "A-2"),
            ],
            "a" * 64,
            "products.xlsx",
        )
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.taxonomy_patch.stop()
        self.environment.stop()
        self.temp.cleanup()

    def test_list_search_filter_sort_and_pagination(self):
        response = self.client.get(
            "/api/products?q=strap&brand=Alpha&sort_by=stock"
            "&sort_dir=desc&page=1&page_size=1"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["data"][0]["name"], "Alpha Strap")
        self.assertEqual(
            (
                payload["meta"]["page"],
                payload["meta"]["page_size"],
                payload["meta"]["total"],
                payload["meta"]["pages"],
            ),
            (1, 1, 1, 1),
        )
        self.assertIn("facets", payload["meta"])
        self.assertTrue(payload["meta"]["csrf_token"])

    def test_create_patch_get_and_delete_product(self):
        created = self.client.post(
            "/api/v1/products",
            json={
                "name": "Gamma Case",
                "article": "G-1",
                "brand": "Gamma",
                "category": "Аксессуары",
                "cell": "C-1",
                "stock": 0,
            },
        )
        self.assertEqual(created.status_code, 201)
        product_id = created.get_json()["data"]["id"]

        updated = self.client.patch(
            "/api/products/{}".format(product_id),
            json={"name": "Gamma Case 2", "stock": 3, "stock_reason": "Инвентаризация"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["data"]["stock"], 3)

        blocked = self.client.delete("/api/products/{}".format(product_id))
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.get_json()["code"], "PRODUCT_REFERENCED")

        self.client.patch(
            "/api/products/{}".format(product_id),
            json={"stock": 0, "stock_reason": "Обнуление"},
        )
        deleted = self.client.delete("/api/products/{}".format(product_id))
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            self.client.get("/api/products/{}".format(product_id)).status_code,
            404,
        )

    def test_validation_and_taxonomy_endpoints(self):
        invalid = self.client.post(
            "/api/products",
            json={"name": "", "stock": "wrong"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(
            invalid.get_json()["code"],
            "PRODUCT_VALIDATION_FAILED",
        )

        brand = self.client.post("/api/brands", json={"name": "Casio"})
        self.assertEqual(brand.status_code, 201)
        duplicate = self.client.post("/api/brands", json={"name": "casio"})
        self.assertEqual(duplicate.status_code, 409)
        category = self.client.post(
            "/api/categories",
            json={"brand": "Casio", "name": "G-Shock"},
        )
        self.assertEqual(category.status_code, 201)
        self.assertIn(
            {"brand": "Casio", "name": "G-Shock", "count": 0},
            self.client.get("/api/categories").get_json()["data"],
        )

    def test_bulk_update_changes_selected_products_only(self):
        listing = self.client.get("/api/products?page_size=50").get_json()["data"]
        selected = [listing[0]["id"], listing[1]["id"]]
        response = self.client.patch(
            "/api/products/bulk",
            json={
                "ids": selected,
                "changes": {"brand": "Обновлённый", "cell": "Z-9"},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["updated"], 2)
        updated = self.client.get(
            "/api/products?brand=Обновлённый&page_size=50"
        ).get_json()["data"]
        self.assertEqual({item["id"] for item in updated}, set(selected))
        self.assertEqual({item["cell"] for item in updated}, {"Z-9"})

    def test_authenticated_mutation_requires_header_csrf(self):
        auth_path = self.root / "auth.db"
        web.app.config.update(
            AUTH_TESTING=True,
            AUTH_DATABASE=str(auth_path),
        )
        store = auth.AuthStore(auth_path)
        user_id = store.create_initial_admin(
            "API",
            "Admin",
            "api@example.test",
            "safe test password",
        )
        client = web.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["_csrf_token"] = "stage-2-csrf"

        rejected = client.post(
            "/api/products",
            json={"name": "Rejected"},
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.get_json()["code"], "CSRF_INVALID")

        accepted = client.post(
            "/api/products",
            json={"name": "Accepted"},
            headers={"X-CSRF-Token": "stage-2-csrf"},
        )
        self.assertEqual(accepted.status_code, 201)

        anonymous = web.app.test_client().get("/api/products")
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous.get_json()["code"], "AUTH_REQUIRED")


if __name__ == "__main__":
    unittest.main()
