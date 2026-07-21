import ast
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from app.catalog_db import CatalogDatabase
from app.clients.moysklad import MoySkladClient
from app.services.receipt_import import (
    ReceiptCatalogRepository,
    ReceiptImportError,
    ReceiptImportExecutor,
    ReceiptImportJournal,
    ReceiptImportPreview,
    build_execution_plan,
    read_receipt_workbook,
)


HEADERS = ["Название", "Артикул", "Бренд", "Категория", "Остаток", "Ячейка"]


def workbook_bytes(rows, sheet="Импорт"):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(HEADERS)
    for row in rows:
        worksheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def insert_catalog_product(database, external_id, name, article, brand,
                           category="Категория", price=1000, xml_id=None,
                           moysklad_id=None):
    now = "2026-07-21T00:00:00+00:00"
    with database.transaction() as connection:
        category_id = connection.execute(
            "INSERT OR IGNORE INTO catalog_categories "
            "(external_category_id,name,path_json,created_at,updated_at) VALUES (?,?,?,?,?)",
            (
                "category-{}".format(category), category,
                json.dumps([{"id": "root", "name": "Часы"}, {"id": category, "name": category}], ensure_ascii=False),
                now, now,
            ),
        ).lastrowid
        if not category_id:
            category_id = connection.execute(
                "SELECT id FROM catalog_categories WHERE external_category_id=?",
                ("category-{}".format(category),),
            ).fetchone()[0]
        product_id = connection.execute(
            "INSERT INTO catalog_products "
            "(name,article,brand,active,primary_category_id,external_source,external_product_id,"
            "external_xml_id,payload_hash,normalized_payload_json,created_at,updated_at,"
            "first_synced_at,last_synced_at,last_sync_mode) VALUES (?,?,?,?,?,'bitrix',?,?,?,?,?,?,?,?,?)",
            (
                name, article, brand, 1, category_id, str(external_id),
                xml_id or "xml-{}".format(external_id), "hash", "{}", now, now, now, now, "full_sync",
            ),
        ).lastrowid
        if price is not None:
            connection.execute(
                "INSERT INTO catalog_prices "
                "(product_id,external_price_id,price_type,price_name,amount,currency,is_base,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'RUB',1,?,?)",
                (product_id, "BASE", "BASE", "Цена продажи", str(price), now, now),
            )
        if moysklad_id:
            connection.execute(
                "INSERT INTO catalog_moysklad_mappings "
                "(product_id,moysklad_product_id,match_status,match_method,candidate_count,confirmed,"
                "confirmed_at,created_at,updated_at) VALUES (?,?,'confirmed','manual',1,1,?,?,?)",
                (product_id, moysklad_id, now, now, now),
            )
    return product_id


class FakeMoySkladClient:
    def __init__(self, fail_on_product_call=None):
        self.fail_on_product_call = fail_on_product_call
        self.product_calls = 0
        self.created = []
        self.updated = []
        self.enters = []

    def get_or_create_product_folder(self, path):
        return {"id": "folder", "meta": {"href": "folder"}, "path": path}

    def _maybe_fail(self):
        self.product_calls += 1
        if self.product_calls == self.fail_on_product_call:
            raise RuntimeError("simulated safe failure")

    def create_product_for_bitrix_import(self, product, code, product_folder=None):
        self._maybe_fail()
        result = {"id": "created-{}".format(product["catalog_product_id"])}
        self.created.append({"product": product, "code": code, "folder": product_folder, "result": result})
        return result

    def update_product_for_bitrix_import(self, product_id, product, product_folder=None):
        self._maybe_fail()
        result = {"id": product_id}
        self.updated.append({"id": product_id, "product": product, "folder": product_folder})
        return result

    def create_stock_enter_without_purchase_prices(self, positions, reason=None, moment=None):
        self.enters.append({"positions": positions, "reason": reason})
        return {"id": "enter-1", "name": "ENTER-1"}


class SalePriceClient(MoySkladClient):
    def __init__(self):
        self.posts = []
        self.puts = []

    def get_sale_price_type(self):
        return {"meta": {"href": "price-type"}}

    def get_rub_currency(self):
        return {"meta": {"href": "currency-rub"}}

    def post(self, endpoint, payload):
        self.posts.append((endpoint, payload))
        return {"id": "new-product"}

    def put(self, endpoint, payload):
        self.puts.append((endpoint, payload))
        return {"id": endpoint.rsplit("/", 1)[-1]}

    def get_default_organization(self):
        return {"meta": {"href": "organization"}}

    def get_default_store(self):
        return {"meta": {"href": "store"}}


class ReceiptImportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = CatalogDatabase(self.root / "catalog.db")
        self.database.initialize()
        self.first_id = insert_catalog_product(
            self.database, "101", "Часы Alpha", "A-1", "Brand A", price=15990,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def preview(self, rows, moysklad_products=None, manual_matches=None):
        return ReceiptImportPreview(
            ReceiptCatalogRepository(self.database),
            moysklad_products=moysklad_products or [],
        ).build(
            workbook_bytes(rows), "fixture.xlsx", "Импорт", manual_matches=manual_matches,
        )

    def test_reads_import_sheet(self):
        result = read_receipt_workbook(
            workbook_bytes([["Часы Alpha", "A-1", "Brand A", "Часы", 2, "A-01"]]),
            "fixture.xlsx",
        )
        self.assertEqual(result["sheet"], "Импорт")
        self.assertEqual(result["rows"][0]["quantity"], 2)

    def test_mass_import_contains_no_purchase_price(self):
        preview = self.preview([["Часы Alpha", "A-1", "Brand A", "Часы", 2, "A-01"]])
        plan = build_execution_plan(preview)
        serialized = json.dumps({"preview": preview, "plan": plan}, ensure_ascii=False).casefold()
        self.assertNotIn("purchase_price", serialized)
        self.assertNotIn("закупоч", serialized)

    def test_exact_article_match(self):
        row = self.preview([["Другое имя", "A-1", "Other", "Other", 1, ""]])["rows"][0]
        self.assertEqual(row["match_method"], "article")
        self.assertEqual(row["bitrix_product"]["catalog_product_id"], self.first_id)

    def test_name_and_brand_match(self):
        row = self.preview([[" часы—alpha ", "", "brand a", "Other", 1, ""]])["rows"][0]
        self.assertEqual(row["match_method"], "name_brand")

    def test_fuzzy_match_is_suggestion_only(self):
        row = self.preview([["Часы Alph", "", "Brand A", "Часы", 1, ""]])["rows"][0]
        self.assertEqual(row["status"], "requires_mapping")
        self.assertEqual(row["match_method"], "none")
        self.assertTrue(row["suggestions"])

    def test_multiple_exact_matches_require_mapping(self):
        insert_catalog_product(self.database, "102", "Часы Beta", "A-1", "Brand B")
        row = self.preview([["Что угодно", "A-1", "", "", 1, ""]])["rows"][0]
        self.assertEqual(row["status"], "requires_mapping")
        self.assertEqual(len(row["suggestions"]), 2)

    def test_not_found_in_bitrix(self):
        row = self.preview([["Совсем другой товар", "NONE", "Other", "Other", 1, ""]])["rows"][0]
        self.assertEqual(row["status"], "not_found")

    def test_manual_selection_resolves_ambiguity(self):
        second_id = insert_catalog_product(self.database, "102", "Часы Beta", "A-1", "Brand B")
        preview = self.preview(
            [["Что угодно", "A-1", "", "", 1, ""]],
            manual_matches={2: second_id},
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["rows"][0]["match_method"], "manual")

    def test_zero_stock_creates_card_without_enter(self):
        preview = self.preview([["Часы Alpha", "A-1", "Brand A", "Часы", 0, "A-01"]])
        client = FakeMoySkladClient()
        result = ReceiptImportExecutor(
            client, ReceiptCatalogRepository(self.database),
            ReceiptImportJournal(self.root / "journal.json"),
        ).apply(preview)
        self.assertEqual(result["created_products"], 1)
        self.assertEqual(client.enters, [])

    def test_positive_stock_is_added_to_enter(self):
        preview = self.preview([["Часы Alpha", "A-1", "Brand A", "Часы", 3, ""]])
        client = FakeMoySkladClient()
        ReceiptImportExecutor(
            client, ReceiptCatalogRepository(self.database),
            ReceiptImportJournal(self.root / "journal.json"),
        ).apply(preview)
        self.assertEqual(client.enters[0]["positions"][0]["quantity"], 3)

    def test_zero_stock_is_excluded_from_enter(self):
        insert_catalog_product(self.database, "102", "Часы Beta", "B-1", "Brand B")
        preview = self.preview([
            ["Часы Alpha", "A-1", "Brand A", "Часы", 0, ""],
            ["Часы Beta", "B-1", "Brand B", "Часы", 2, ""],
        ])
        client = FakeMoySkladClient()
        ReceiptImportExecutor(
            client, ReceiptCatalogRepository(self.database),
            ReceiptImportJournal(self.root / "journal.json"),
        ).apply(preview)
        self.assertEqual(len(client.created), 2)
        self.assertEqual(len(client.enters[0]["positions"]), 1)

    def test_sale_price_is_written_as_sale_price(self):
        client = SalePriceClient()
        product = ReceiptCatalogRepository(self.database).list_products()[0]
        client.create_product_for_bitrix_import(product, "BITRIX-101", {"meta": {"href": "folder"}})
        payload = client.posts[0][1]
        self.assertEqual(payload["salePrices"][0]["value"], 1599000)
        self.assertEqual(payload["salePrices"][0]["priceType"]["meta"]["href"], "price-type")

    def test_sale_price_type_selects_explicit_retail_price(self):
        class PriceTypeLookupClient(MoySkladClient):
            def __init__(self):
                self.requested_endpoints = []

            def get(self, endpoint, params=None):
                self.requested_endpoints.append(endpoint)
                return {
                    "rows": [
                        {
                            "name": "Закупочная цена",
                            "meta": {"href": "purchase-price-type"},
                        },
                        {
                            "name": "Розничная цена",
                            "meta": {"href": "retail-price-type"},
                        },
                    ]
                }

        client = PriceTypeLookupClient()
        result = client.get_sale_price_type()

        self.assertEqual(
            result["meta"]["href"],
            "retail-price-type",
        )
        self.assertEqual(
            client.requested_endpoints,
            ["/context/companysettings/pricetype"],
        )

    def test_sale_price_type_never_falls_back_to_purchase_price(self):
        class PriceTypeLookupClient(MoySkladClient):
            def __init__(self):
                pass

            def get(self, endpoint, params=None):
                return {
                    "rows": [
                        {
                            "name": "Закупочная цена",
                            "meta": {"href": "purchase-price-type"},
                        },
                        {
                            "name": "Себестоимость",
                            "meta": {"href": "cost-price-type"},
                        },
                    ]
                }

        client = PriceTypeLookupClient()

        with self.assertRaisesRegex(
            ValueError,
            "не найден явный тип цены продажи",
        ):
            client.get_sale_price_type()

    def test_no_purchase_price_is_written(self):
        client = SalePriceClient()
        product = ReceiptCatalogRepository(self.database).list_products()[0]
        client.create_product_for_bitrix_import(product, "BITRIX-101")
        client.create_stock_enter_without_purchase_prices([{"product_id": "p1", "quantity": 2}])
        serialized = json.dumps(client.posts, ensure_ascii=False).casefold()
        for forbidden in ("buyprice", "purchase", "minprice", "закупоч"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("price", client.posts[-1][1]["positions"][0])

    def test_repeated_completed_import_is_blocked(self):
        preview = self.preview([["Часы Alpha", "A-1", "Brand A", "Часы", 1, ""]])
        journal = ReceiptImportJournal(self.root / "journal.json")
        ReceiptImportExecutor(
            FakeMoySkladClient(), ReceiptCatalogRepository(self.database), journal,
        ).apply(preview)
        with self.assertRaisesRegex(ReceiptImportError, "уже применён"):
            ReceiptImportExecutor(
                FakeMoySkladClient(), ReceiptCatalogRepository(self.database), journal,
            ).apply(preview)

    def test_file_hash_and_batch_id_are_stable(self):
        content = workbook_bytes([["Часы Alpha", "A-1", "Brand A", "Часы", 1, ""]])
        first = ReceiptImportPreview(ReceiptCatalogRepository(self.database)).build(content, "a.xlsx")
        second = ReceiptImportPreview(ReceiptCatalogRepository(self.database)).build(content, "b.xlsx")
        self.assertEqual(first["file_hash"], hashlib.sha256(content).hexdigest())
        self.assertEqual(first["import_batch_id"], second["import_batch_id"])

    def test_preview_performs_zero_writes(self):
        content = workbook_bytes([["Часы Alpha", "A-1", "Brand A", "Часы", 1, ""]])
        before = hashlib.sha256(self.database.path.read_bytes()).hexdigest()
        preview = ReceiptImportPreview(ReceiptCatalogRepository(self.database)).build(content, "a.xlsx")
        after = hashlib.sha256(self.database.path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(preview["writes"], {"bitrix": 0, "moysklad": 0, "local": 0})

    def test_partial_failure_resumes_without_duplicate(self):
        insert_catalog_product(self.database, "102", "Часы Beta", "B-1", "Brand B")
        preview = self.preview([
            ["Часы Alpha", "A-1", "Brand A", "Часы", 1, ""],
            ["Часы Beta", "B-1", "Brand B", "Часы", 1, ""],
        ])
        journal = ReceiptImportJournal(self.root / "journal.json")
        first_client = FakeMoySkladClient(fail_on_product_call=2)
        with self.assertRaises(RuntimeError):
            ReceiptImportExecutor(
                first_client, ReceiptCatalogRepository(self.database), journal,
            ).apply(preview)
        self.assertEqual(len(first_client.created), 1)
        retry_client = FakeMoySkladClient()
        result = ReceiptImportExecutor(
            retry_client, ReceiptCatalogRepository(self.database), journal,
        ).apply(preview)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(retry_client.created), 1)

    def test_source_is_python_36_compatible(self):
        source = (Path(__file__).parents[1] / "app" / "services" / "receipt_import.py").read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 6))

    def test_routes_return_json_errors_and_render_receipts(self):
        from app import web
        client = web.app.test_client()
        response = client.post("/receipts/import/preview", data={})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.is_json)
        self.assertEqual(response.get_json()["code"], "missing_file")
        with mock.patch.object(web, "load_receipts", return_value=[]), mock.patch.object(
            web, "get_warehouse_items", return_value=[]
        ):
            page = client.get("/receipts")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Создать товары и провести приход", html)
        self.assertNotIn("Цена закупки", html)


if __name__ == "__main__":
    unittest.main()
