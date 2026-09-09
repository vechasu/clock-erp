import base64
import json
import subprocess
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


class SearchBitrixClient(FakeBitrixClient):
    def __init__(self, products):
        self.products = products
        self.unavailable = False

    def search_products(self, query, limit=20):
        key = query.casefold()
        matches = [item for item in self.products if (
            key in item["name"].casefold()
            or key in item["external_sku"].casefold()
            or key == item["external_product_id"]
        )]
        return sorted(matches, key=lambda item: (
            0 if item["external_sku"].casefold() == key else
            1 if item["name"].casefold() == key else 2
        ))[:limit]


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

    def post_import(self, product, action="create", quantity=2):
        brand, category = self.taxonomy()
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(product)):
            return self.client.post(
                "/api/v1/bitrix-products/{}/import".format(product["external_product_id"]),
                json={"action": action, "quantity": quantity, "brand_id": brand, "category_id": category},
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
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.get_json()["data"]["product"]["stock"], 4)

    def test_article_match_is_reported_before_create(self):
        product = source_product("505", article="SEED-1")
        response = self.post_import(product)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["match_method"], "article")
        self.assertEqual(response.get_json()["data"]["product"]["stock"], 4)

    def test_legacy_update_adds_quantity_and_preserves_card(self):
        product = source_product("506", name="First", article="BX-506")
        created = self.post_import(product).get_json()["data"]["product"]
        changed = source_product("506", name="Updated", article="BX-506", image=False)
        response = self.post_import(changed, action="update")
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()["data"]["product"]
        self.assertEqual(saved["name"], "First")
        self.assertEqual(saved["stock"], created["stock"] + 2)

    def test_new_stock_is_user_quantity_for_every_external_stock(self):
        for index, stock in enumerate((999, 998, 997, 47, 0, None)):
            with self.subTest(stock=stock):
                source = source_product(str(800 + index), article="NEW-" + str(index))
                source["stock"] = stock
                response = self.post_import(source, quantity=2)
                self.assertEqual(response.status_code, 201)
                saved = response.get_json()["data"]
                self.assertEqual(saved["product"]["stock"], 2)
                with CatalogDatabase(self.database_path).connect() as connection:
                    movement = connection.execute(
                        "SELECT stock_before, stock_after, quantity_delta FROM catalog_stock_movements WHERE receipt_id = ?",
                        (saved["receipt_id"],),
                    ).fetchone()
                    self.assertEqual(tuple(movement), (0, 2, 2))

    def test_existing_card_unchanged_with_different_unknown_taxonomy(self):
        source = source_product("850", article="EXISTING-850")
        saved = self.post_import(source, quantity=3).get_json()["data"]["product"]
        database = CatalogDatabase(self.database_path)
        with database.connect() as connection:
            before = dict(connection.execute("SELECT * FROM catalog_excel_products WHERE id = ?", (saved["id"],)).fetchone())
        changed = source_product("850", name="Changed", article="Changed article", brand="Other", category="Other")
        changed["stock"] = 999
        fake = FakeBitrixClient(changed)
        with mock.patch.object(web, "_bitrix_single_client", return_value=fake), mock.patch.object(fake, "download_product_image", side_effect=AssertionError("existing photo must not be fetched")):
            response = self.client.post("/api/v1/bitrix-products/850/import", json={"quantity": 2, "brand_id": -1, "category_id": -1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["product"]["stock"], 5)
        with database.connect() as connection:
            after = dict(connection.execute("SELECT * FROM catalog_excel_products WHERE id = ?", (saved["id"],)).fetchone())
            self.assertEqual(connection.execute("SELECT count(*) FROM catalog_excel_products WHERE bitrix_external_product_id = '850'").fetchone()[0], 1)
        for key in before.keys() - {"stock", "stock_source", "updated_at"}:
            self.assertEqual(after[key], before[key], key)

    def test_invalid_quantity_does_not_create_or_add_stock(self):
        for quantity in (None, 0, -1, 1.5, True, "bad"):
            with self.subTest(quantity=quantity):
                response = self.post_import(source_product("860", article="BAD-860"), quantity=quantity)
                self.assertEqual(response.status_code, 422)
        with CatalogDatabase(self.database_path).connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM catalog_excel_products WHERE bitrix_external_product_id = '860'").fetchone()[0], 0)

    def test_stock_post_failure_rolls_back_new_card_and_receipt(self):
        with mock.patch("app.services.receipt_inventory.ReceiptInventory._post_draft", side_effect=ValueError("blocked")):
            response = self.post_import(source_product("870", article="FAIL-870"))
        self.assertEqual(response.status_code, 422)
        with CatalogDatabase(self.database_path).connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM catalog_excel_products WHERE bitrix_external_product_id = '870'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM erp_receipts").fetchone()[0], 0)

    def test_xml_link_reuses_card_when_article_changes(self):
        source = source_product("875", article="XML-875")
        saved = self.post_import(source).get_json()["data"]["product"]
        with CatalogDatabase(self.database_path).transaction() as connection:
            connection.execute("UPDATE catalog_excel_products SET bitrix_external_product_id = NULL WHERE id = ?", (saved["id"],))
        source["external_sku"] = "CHANGED-XML-ARTICLE"
        response = self.post_import(source)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["match_method"], "xml_id")
        self.assertEqual(response.get_json()["data"]["product"]["id"], saved["id"])
        self.assertEqual(response.get_json()["data"]["product"]["stock"], 4)

    def test_conflicting_article_does_not_match_other_bitrix_identity(self):
        first = self.post_import(source_product("880", article="SAME-880")).get_json()["data"]["product"]
        response = self.post_import(source_product("881", article="SAME-880"))
        self.assertEqual(response.status_code, 422)
        with CatalogDatabase(self.database_path).connect() as connection:
            self.assertEqual(connection.execute("SELECT stock FROM catalog_excel_products WHERE id = ?", (first["id"],)).fetchone()[0], 2)

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
                json={"action": "create", "quantity": 2, "brand_id": self.taxonomy()[0], "category_id": self.taxonomy()[1]},
            )
        after = self.client.get("/api/v1/products?page_size=200").get_json()["meta"]["total"]
        self.assertEqual(response.status_code, 500)
        self.assertEqual(before, after)
        image_root = self.root / "product_images"
        self.assertFalse(image_root.exists() and list(image_root.iterdir()))

    def test_products_and_receipts_render_without_remote_clients(self):
        with mock.patch.object(web, "MoySkladClient", side_effect=AssertionError("remote")), mock.patch.object(web, "_bitrix_single_client", side_effect=AssertionError("remote")):
            products = self.client.get("/warehouse")
            receipts = self.client.get("/app/receipts")
        self.assertEqual(products.status_code, 200)
        self.assertEqual(receipts.status_code, 200)
        self.assertIn('id="bitrixImportQuantity"', products.get_data(as_text=True))

    def test_frontend_contains_dropdown_live_search_preview_and_update(self):
        template = Path("app/templates/warehouse.html").read_text(encoding="utf-8")
        header = Path("app/templates/_products_workspace.html").read_text(encoding="utf-8")
        self.assertIn("Добавить товар ▾", header)
        self.assertIn("Добавить из Bitrix", header)
        self.assertNotIn("openManualProductCreate", header + template)
        self.assertNotIn("warehouseCreateNewProduct", template)
        self.assertNotIn('action_kind="product"', template)
        self.assertNotIn('fetch("/api/v1/products",', template)
        self.assertNotIn("Создать вручную", header)

        self.assertIn("bitrixProductSearch", template)
        self.assertIn("setTimeout(function(){searchBitrixProducts", template)
        self.assertIn("bitrixImportQuantity", template)
        self.assertIn("Добавить количество", template)

    def test_search_c_opo_retro_gold_excludes_unrelated_products(self):
        products = [
            source_product("601", "Gravity Blue", "GRAVITY-BLUE"),
            source_product("602", "C-OPO Retro Gold", "C-OPO RETRO GOLD"),
            source_product("603", "Mercury Silver", "MERCURY-SILVER"),
        ]
        with mock.patch.object(web, "_bitrix_single_client", return_value=SearchBitrixClient(products)):
            response = self.client.get("/api/v1/bitrix-products/search?q=C-OPO%20RETRO%20GOLD")
        self.assertEqual([item["bitrix_id"] for item in response.get_json()["data"]], ["602"])

    def test_search_exact_article_is_first(self):
        products = [
            source_product("611", "Accessory BX-611", "OTHER"),
            source_product("612", "Watch", "BX-611"),
        ]
        with mock.patch.object(web, "_bitrix_single_client", return_value=SearchBitrixClient(products)):
            response = self.client.get("/api/v1/bitrix-products/search?q=bx-611")
        self.assertEqual(response.get_json()["data"][0]["bitrix_id"], "612")

    def test_search_partial_name_is_case_insensitive(self):
        product = source_product("621", "C-OPO Retro Gold", "RETRO-1")
        with mock.patch.object(web, "_bitrix_single_client", return_value=SearchBitrixClient([product])):
            response = self.client.get("/api/v1/bitrix-products/search?q=retro%20gold")
        self.assertEqual(response.get_json()["data"][0]["bitrix_id"], "621")

    def test_search_exact_bitrix_id(self):
        product = source_product("631", "Retro", "RETRO-2")
        with mock.patch.object(web, "_bitrix_single_client", return_value=SearchBitrixClient([product])):
            response = self.client.get("/api/v1/bitrix-products/search?q=631")
        self.assertEqual(response.get_json()["data"][0]["bitrix_id"], "631")

    def test_search_without_matches_returns_empty_data(self):
        with mock.patch.object(web, "_bitrix_single_client", return_value=SearchBitrixClient([source_product()])):
            response = self.client.get("/api/v1/bitrix-products/search?q=missing")
        self.assertEqual(response.get_json()["data"], [])

    def test_frontend_clears_search_selection_and_ignores_stale_responses(self):
        template = Path("app/templates/warehouse.html").read_text(encoding="utf-8")
        self.assertIn("resetBitrixSelection();", template)
        self.assertIn("renderBitrixResults([]);", template)
        self.assertIn("bitrixSelectedProduct = null;", template)
        self.assertIn("bitrixSearchController?.abort();", template)
        self.assertIn("requestVersion !== bitrixRequestVersion", template)
        self.assertIn("setTimeout(function(){searchBitrixProducts(query, requestVersion);}, 300)", template)

    def test_external_stock_is_preview_only(self):
        product = source_product("641", "Stocked", "STOCK-999")
        product["stock"] = 999
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(product)):
            preview = self.client.get("/api/v1/bitrix-products/641")
        self.assertEqual(preview.get_json()["data"]["stock"], 999)
        saved = self.post_import(product).get_json()["data"]["product"]
        self.assertEqual(saved["stock"], 2)


    def test_389_53_20_existing_card_is_visible_and_linked_to_draft(self):
        from app.services.supplies import SupplyEngine
        from app.services.excel_product_catalog import ExcelProductCatalog
        product = source_product(
            identity="199954", name="Fashion 389 53 (20 мм)",
            article="fashion-389-53-20-mm", image=False,
        )
        first = self.post_import(product).get_json()["data"]["product"]
        added = self.post_import(product, action="update", quantity=1)
        self.assertEqual(added.status_code, 200)
        saved = added.get_json()["data"]["product"]
        self.assertEqual(saved["id"], first["id"])
        catalog = ExcelProductCatalog(CatalogDatabase(self.database_path))
        # The shorthand is an infix: the catalog deliberately searches prefixes.
        self.assertEqual(catalog.list_products(query="389-53-20")["items"], [])
        visible = catalog.list_products(query=saved["article"])["items"]
        self.assertEqual([p["id"] for p in visible], [saved["id"]])
        page = self.client.get("/warehouse", query_string={"q": saved["article"]})
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-product-id="{}"'.format(saved["id"]), page.get_data(as_text=True))
        engine = SupplyEngine(CatalogDatabase(self.database_path))
        supply = engine.create("389-53-20 regression")
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(product)):
            resolved = self.client.post("/api/v1/bitrix-products/199954/import", json={"supply_id": supply["id"]})
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.get_json()["data"]["erp_product_id"], saved["id"])
        path = "/api/v1/receipts/supplies/" + supply["id"]
        for unused in range(2):
            linked = self.client.post(path + "/items", json={"product_id": saved["id"], "quantity": 1}, headers={"Idempotency-Key": "389-53-20-regression"})
            self.assertEqual(linked.status_code, 200)
        reloaded = self.client.get(path).get_json()["data"]
        self.assertEqual(reloaded["status"], "draft")
        self.assertEqual([(i["product_id"], i["quantity"]) for i in reloaded["items"]], [(saved["id"], 1)])
        with CatalogDatabase(self.database_path).connect() as connection:
            rows = connection.execute("SELECT id, stock FROM catalog_excel_products WHERE bitrix_external_product_id='199954'").fetchall()
            self.assertEqual([(r["id"], r["stock"]) for r in rows], [(saved["id"], 3)])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM catalog_stock_movements WHERE receipt_id=?", (supply["id"],)).fetchone()[0], 0)

    def test_add_ui_shows_saved_389_53_20_and_rejects_unconfirmed_success(self):
        source = Path("app/templates/warehouse.html").read_text(encoding="utf-8")
        handler = source.split("    async function submitBitrixImport(action) {", 1)[1].split('\n    document.addEventListener("click"', 1)[0]
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const handler = JSON.parse(fs.readFileSync(0, 'utf8'));
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, {value: id === 'bitrixImportQuantity' ? '1' : ''});
    return elements.get(id);
  },
  querySelector() { return {value: 'csrf'}; },
};
let destination, errorMessage, responsePayload;
const window = {location: {assign(url) { destination = url; }}};
const fetch = async () => ({ok: true, json: async () => responsePayload});
let bitrixSelectedProduct = {bitrix_id: 199954}, bitrixSubmitting = false;
const bitrixImportMessage = message => { errorMessage = message; };
const submit = eval('(async function(action) {' + handler + ')');
(async () => {
  responsePayload = {ok: true, data: {erp_product_id: 8345, product: {id: 8345, article: 'fashion-389-53-20-mm'}}};
  await submit('update');
  assert.equal(new URL(destination, 'https://erp.test').searchParams.get('q'), 'fashion-389-53-20-mm');
  for (const payload of [{ok: false, message: 'DB failed'}, {ok: true, data: {}}, {ok: true, data: {erp_product_id: 8345, product: {id: 999, article: 'other'}}}]) {
    destination = null;
    responsePayload = payload;
    await submit('update');
    assert.equal(destination, null);
    assert.ok(errorMessage);
    assert.equal(bitrixSubmitting, false);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(["node", "-e", script], input=json.dumps(handler), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def supply_import(self, supply_id, product=None):
        product = product or source_product()
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(product)):
            return self.client.post("/api/v1/bitrix-products/501/import", json={"supply_id": supply_id})

    def test_supply_import_card_then_add_retry_merge_and_reload(self):
        from app.services.supplies import SupplyEngine
        engine = SupplyEngine(CatalogDatabase(self.database_path))
        supply = engine.create("Test supply")
        imported = self.supply_import(supply["id"])
        self.assertEqual(imported.status_code, 201, imported.get_json())
        product = imported.get_json()["data"]["product"]
        self.assertEqual(product["stock"], 0)
        self.assertEqual(engine.get(supply["id"])["items"], [])
        self.assertEqual(engine.movements(), [])
        with CatalogDatabase(self.database_path).connect() as c:
            card = c.execute("SELECT * FROM catalog_excel_products WHERE id=?", (product["id"],)).fetchone()
            self.assertEqual(card["bitrix_external_product_id"], "501")
            self.assertEqual(card["excel_article"], "BX-501")
            self.assertEqual(card["excel_brand"], "Known")
            self.assertEqual(card["excel_category"], "Watches")
            self.assertTrue(card["local_image_path"])
        path = "/api/v1/receipts/supplies/" + supply["id"] + "/items"
        for unused in range(2):
            response = self.client.post(path, json={"product_id": product["id"], "quantity": 3}, headers={"Idempotency-Key": "picker-test-123"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["data"]["total_quantity"], 3)
        again = self.supply_import(supply["id"])
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["data"]["erp_product_id"], product["id"])
        response = self.client.post(path, json={"product_id": product["id"], "quantity": 2}, headers={"Idempotency-Key": "picker-test-456"})
        self.assertEqual(response.get_json()["data"]["position_count"], 1)
        reloaded = self.client.get("/api/v1/receipts/supplies/" + supply["id"]).get_json()["data"]
        self.assertEqual(reloaded["items"][0]["quantity"], 5)
        self.assertEqual(reloaded["items"][0]["stock_before"], 0)
        self.assertEqual(reloaded["items"][0]["stock_after"], 5)
        self.assertEqual(engine.movements(), [])
        engine.post(supply["id"])
        self.assertEqual(self.supply_import(supply["id"]).get_json()["data"]["product"]["stock"], 5)
        self.client.post(path, json={"product_id": product["id"], "quantity": 2}, headers={"Idempotency-Key": "picker-posted-1"})
        self.assertEqual(engine.get(supply["id"])["total_quantity"], 7)
        self.assertEqual(len(engine.movements()), 2)

    def test_supply_failed_import_invalid_quantity_and_failed_add(self):
        from app.services.supplies import SupplyEngine
        engine = SupplyEngine(CatalogDatabase(self.database_path))
        supply = engine.create("Test supply")
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(unavailable=True)):
            failed = self.client.post("/api/v1/bitrix-products/501/import", json={"supply_id": supply["id"]})
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(engine.get(supply["id"])["items"], [])
        product = self.supply_import(supply["id"]).get_json()["data"]["product"]
        path = "/api/v1/receipts/supplies/" + supply["id"] + "/items"
        for quantity in (0, -1, "abc", 1.5, True):
            response = self.client.post(path, json={"product_id": product["id"], "quantity": quantity}, headers={"Idempotency-Key": "invalid-quantity"})
            self.assertEqual(response.status_code, 422)
        engine.delete(supply["id"])
        response = self.client.post(path, json={"product_id": product["id"], "quantity": 3}, headers={"Idempotency-Key": "cancelled-supply"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(engine.get(supply["id"])["items"], [])
        with CatalogDatabase(self.database_path).connect() as c:
            self.assertEqual(c.execute("SELECT stock FROM catalog_excel_products WHERE id=?", (product["id"],)).fetchone()[0], 0)
        with mock.patch.object(web, "_bitrix_single_client") as remote:
            self.assertEqual(self.supply_import(supply["id"]).status_code, 422)
            remote.assert_not_called()

    def test_supply_rejects_conflicting_identity_without_changing_card(self):
        from app.services.supplies import SupplyEngine
        supply = SupplyEngine(CatalogDatabase(self.database_path)).create("Test")
        original = self.post_import(source_product()).get_json()["data"]["product"]
        other = source_product(identity="502", name="Different", article="BX-501")
        with mock.patch.object(web, "_bitrix_single_client", return_value=FakeBitrixClient(other)):
            result = self.client.post("/api/v1/bitrix-products/502/import", json={"supply_id": supply["id"]})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(self.supply_import(supply["id"]).get_json()["data"]["erp_product_id"], original["id"])


if __name__ == "__main__":
    unittest.main()
