import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyError
from app.services.excel_product_catalog import ExcelProductBatchService


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def source_product(identity="501", name="Bitrix Watch", article="BX-501",
                   brand="Known", category="Watches", image=True):
    images = [] if not image else [{
        "id": "image-" + identity, "kind": "gallery",
        "original_url": "https://www.tictactoy.ru/upload/{}.png".format(identity),
        "filename": identity + ".png", "mime_type": "image/png",
        "width": 1, "height": 1, "file_size": len(PNG),
        "order": 1, "is_primary": True,
    }]
    return {
        "external_source": "bitrix", "external_product_id": identity,
        "external_xml_id": "xml-" + identity, "external_sku": article,
        "name": name, "brand": brand,
        "category": {"name": category}, "categories": [{"name": category}],
        "url": "https://www.tictactoy.ru/catalog/" + identity,
        "preview_text": "", "detail_text": "", "properties": [],
        "images": images, "prices": [], "offers": [],
        "sale_price": {"value": 12500, "value_text": "12500", "currency": "RUB"},
        "stock": 4, "active": True,
    }


def excel_row(row=2, article="SEED-1"):
    return {
        "excel_row": row, "excel_name": "Seed", "excel_name_raw": "Seed",
        "excel_article": article, "excel_brand": "Known",
        "category": "Watches", "stock": 2, "stock_valid": True,
        "cell": "A-1", "match_status": "not_found", "match_method": "test",
        "confidence": 0, "alternatives": [],
    }


class FakeBitrixClient:
    def __init__(self, product=None, unavailable=False):
        self.product = product or source_product()
        self.unavailable = unavailable

    def search_products(self, query, limit=20):
        if self.unavailable:
            raise BitrixCatalogReadOnlyError("offline")
        return [self.product]

    def get_product(self, product_id):
        if self.unavailable:
            raise BitrixCatalogReadOnlyError("offline")
        return self.product if str(product_id) == self.product["external_product_id"] else None

    def download_product_image(self, image):
        if self.unavailable:
            raise BitrixCatalogReadOnlyError("offline")
        return PNG, "image/png", image["filename"]


class SingleBitrixProductImportTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "catalog.db"
        self.environment = mock.patch.dict(
            "os.environ", {"CATALOG_DATABASE_PATH": str(self.database_path)}
        )
        self.environment.start()
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        ExcelProductBatchService(CatalogDatabase(self.database_path)).apply(
            [excel_row()], "a" * 64, "seed.xlsx"
        )
        self.client = web.app.test_client()

    def tearDown(self):
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.environment.stop()
        self.temp.cleanup()

    def taxonomy(self):
        with CatalogDatabase(self.database_path).connect() as connection:
            brand = connection.execute("SELECT id FROM erp_brands WHERE name='Known'").fetchone()[0]
            category = connection.execute("SELECT id FROM erp_categories WHERE name='Watches'").fetchone()[0]
        return brand, category

    def post_import(self, product, action="create"):
        brand, category = self.taxonomy()
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(product)):
            return self.client.post(
                "/api/v1/bitrix-products/{}/import".format(product["external_product_id"]),
                json={"action": action, "brand_id": brand, "category_id": category},
            )

    def test_search_and_import_one_product_with_local_photo(self):
        product = source_product()
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(product)):
            search = self.client.get("/api/v1/bitrix-products/search?q=BX-501")
        self.assertEqual(search.status_code, 200)
        response = self.post_import(product)
        self.assertEqual(response.status_code, 201)
        saved = response.get_json()["data"]["product"]
        with CatalogDatabase(self.database_path).connect() as connection:
            row = connection.execute(
                "SELECT bitrix_external_product_id, local_image_path FROM catalog_excel_products WHERE id = ?",
                (saved["id"],),
            ).fetchone()
        self.assertEqual(row["bitrix_external_product_id"], "501")
        self.assertTrue((self.root / "product_images" / row["local_image_path"]).is_file())

    def test_product_without_photo_is_created(self):
        response = self.post_import(source_product("502", article="BX-502", image=False))
        self.assertEqual(response.status_code, 201)
        product_id = response.get_json()["data"]["product"]["id"]
        with CatalogDatabase(self.database_path).connect() as connection:
            path = connection.execute(
                "SELECT local_image_path FROM catalog_excel_products WHERE id = ?",
                (product_id,),
            ).fetchone()[0]
        self.assertFalse(path)

    def test_unknown_taxonomy_requires_selection_and_accepts_existing_choice(self):
        product = source_product("503", article="BX-503", brand="Unknown", category="Other")
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(product)):
            blocked = self.client.post(
                "/api/v1/bitrix-products/503/import", json={"action": "create"}
            )
        self.assertEqual(blocked.status_code, 422)
        self.assertEqual(self.post_import(product).status_code, 201)

    def test_repeat_import_returns_existing_without_overwrite(self):
        product = source_product("504", article="BX-504")
        self.assertEqual(self.post_import(product).status_code, 201)
        repeated = self.post_import(product)
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.get_json()["code"], "PRODUCT_ALREADY_EXISTS")

    def test_article_match_is_reported_before_create(self):
        product = source_product("505", article="SEED-1")
        response = self.post_import(product)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["fields"]["preview"]["match_method"], "article")

    def test_explicit_update_changes_card_but_preserves_stock(self):
        product = source_product("506", name="First", article="BX-506")
        created = self.post_import(product).get_json()["data"]["product"]
        changed = source_product("506", name="Updated", article="BX-506", image=False)
        response = self.post_import(changed, action="update")
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()["data"]["product"]
        self.assertEqual(saved["name"], "Updated")
        self.assertEqual(saved["stock"], created["stock"])

    def test_bitrix_unavailable_does_not_create_card(self):
        before = self.client.get("/api/v1/products?page_size=200").get_json()["meta"]["total"]
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(unavailable=True)):
            response = self.client.get("/api/v1/bitrix-products/search?q=test")
        after = self.client.get("/api/v1/products?page_size=200").get_json()["meta"]["total"]
        self.assertEqual(response.status_code, 503)
        self.assertEqual(before, after)

    def test_save_error_rolls_back_card_and_prepared_photo(self):
        product = source_product("507", article="BX-507")
        before = self.client.get("/api/v1/products?page_size=200").get_json()["meta"]["total"]
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(product)), \
                mock.patch("app.services.bitrix_erp_product_sync.AuditJournal.record", side_effect=RuntimeError("save failed")):
            response = self.client.post(
                "/api/v1/bitrix-products/507/import",
                json={"action": "create", "brand_id": self.taxonomy()[0], "category_id": self.taxonomy()[1]},
            )
        after = self.client.get("/api/v1/products?page_size=200").get_json()["meta"]["total"]
        self.assertEqual(response.status_code, 500)
        self.assertEqual(before, after)
        image_root = self.root / "product_images"
        self.assertFalse(image_root.exists() and list(image_root.iterdir()))

    def test_frontend_contains_dropdown_live_search_preview_and_update(self):
        template = Path("app/templates/warehouse.html").read_text(encoding="utf-8")
        header = Path("app/templates/_products_workspace.html").read_text(encoding="utf-8")
        self.assertIn("Добавить товар ▾", header)
        self.assertIn("Добавить из Bitrix", header)
        self.assertIn("bitrixProductSearch", template)
        self.assertIn("setTimeout(function(){searchBitrixProducts", template)
        self.assertIn("Изменятся поля:", template)
        self.assertIn("Обновить из Bitrix", template)


if __name__ == "__main__":
    unittest.main()
