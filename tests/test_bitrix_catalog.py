import copy
import unittest

from app.clients.bitrix_catalog import (
    BitrixCatalogReadOnlyClient,
    match_product,
    normalize_category,
    normalize_product,
    normalize_property,
)
from scripts.bitrix_catalog_dry_run import build_report


class CatalogNormalizationTest(unittest.TestCase):
    def test_normalizes_product_and_category(self):
        product = normalize_product({
            "ID": 123,
            "XML_ID": "xml-123",
            "ARTICLE": "ABC-123",
            "NAME": "Watch",
            "ACTIVE": "N",
            "SECTION": {"ID": 10, "NAME": "Watches", "PATH": ["Catalog", "Watches"]},
        })
        self.assertEqual(product["external_product_id"], "123")
        self.assertEqual(product["external_xml_id"], "xml-123")
        self.assertEqual(product["external_sku"], "ABC-123")
        self.assertFalse(product["active"])
        self.assertEqual(product["category"]["path"], ["Catalog", "Watches"])

    def test_category_string_path_and_depth(self):
        category = normalize_category({"ID": "7", "NAME": "Automatic", "PATH": "Watches/Men"})
        self.assertEqual(category["path"], ["Watches", "Men", "Automatic"])
        self.assertEqual(category["depth"], 2)

    def test_properties_keep_types_and_display_values(self):
        prop = normalize_property({
            "ID": 3, "CODE": "COLOR", "NAME": "Color", "PROPERTY_TYPE": "L",
            "VALUE": "RED", "VALUE_ENUM": "Red",
        })
        self.assertEqual(prop["type"], "L")
        self.assertEqual(prop["value"], "RED")
        self.assertEqual(prop["display_value"], "Red")

        number_prop = normalize_property({"CODE": "SIZE", "PROPERTY_TYPE": "N", "VALUE": 42})
        file_prop = normalize_property({"CODE": "MANUAL", "PROPERTY_TYPE": "F", "VALUE": "/manual.pdf"})
        self.assertEqual((number_prop["type"], number_prop["value"]), ("N", 42))
        self.assertEqual((file_prop["type"], file_prop["value"]), ("F", "/manual.pdf"))

    def test_multiple_property_is_list(self):
        prop = normalize_property({"CODE": "MATERIAL", "MULTIPLE": "Y", "VALUE": "Steel"})
        self.assertTrue(prop["multiple"])
        self.assertEqual(prop["value"], ["Steel"])

    def test_missing_sku_xml_and_image_remain_empty(self):
        product = normalize_product({"ID": 1, "NAME": "No identifiers"})
        self.assertEqual(product["external_sku"], "")
        self.assertEqual(product["external_xml_id"], "")
        self.assertEqual(product["images"], [])

    def test_code_is_used_as_article_when_article_is_missing(self):
        product = normalize_product({"ID": 1, "CODE": "Z031-TITI-W15BK"})
        self.assertEqual(product["external_sku"], "Z031-TITI-W15BK")

    def test_multiple_images_are_ordered_and_deduplicated(self):
        product = normalize_product({
            "ID": 1,
            "PREVIEW_PICTURE": "/one.jpg",
            "DETAIL_PICTURE": "/one.jpg",
            "MORE_PHOTO": ["/two.jpg", {"URL": "/three.jpg", "SORT": 20}],
        }, "https://example.test/")
        self.assertEqual([image["url"] for image in product["images"]], [
            "https://example.test/one.jpg", "https://example.test/two.jpg",
            "https://example.test/three.jpg",
        ])

    def test_offer_is_normalized(self):
        product = normalize_product({
            "ID": 1, "NAME": "Parent", "BRAND": "Brand",
            "OFFERS": [{"ID": 2, "XML_ID": "offer-2", "ARTICLE": "SKU-2", "NAME": "Blue"}],
        })
        self.assertEqual(product["offers"][0]["external_product_id"], "2")
        self.assertEqual(product["offers"][0]["external_xml_id"], "offer-2")
        self.assertEqual(product["offers"][0]["brand"], "Brand")

    def test_currency_is_preserved(self):
        product = normalize_product({
            "ID": 1,
            "PRICES": [
                {"PRICE": "10.50", "CURRENCY": "USD", "ROLE": "sale"},
                {"PRICE": "950", "CURRENCY": "RUB", "ROLE": "retail"},
            ],
        })
        self.assertEqual(product["sale_price"]["value"], 10.5)
        self.assertEqual(product["sale_price"]["currency"], "USD")
        self.assertEqual([price["currency"] for price in product["prices"]], ["USD", "RUB"])

    def test_purchase_price_cannot_become_sale_price(self):
        product = normalize_product({
            "ID": 1,
            "PRICES": [{"PRICE": 500, "NAME": "Закупочная"}],
        })
        self.assertIsNone(product["sale_price"])

    def test_normalizes_real_endpoint_contract(self):
        product = normalize_product({
            "id": "199482",
            "xml_id": "xml-199482",
            "article": "",
            "source_url": "https://www.tictactoy.ru/catalog/watches/ziiiro/lunar-black/",
            "name": "LUNAR Black",
            "primary_category_id": "27",
            "categories": [{
                "id": "27", "code": "ziiiro", "name": "Ziiiro",
                "path": [{"id": "172", "name": "Наручные часы"}, {"id": "27", "name": "Ziiiro"}],
            }],
            "images": [{
                "id": "1", "type": "gallery", "url": "https://example.test/a.jpg",
                "mime_type": "image/jpeg", "width": 1200, "height": 1200,
                "file_size": 1000, "sort": 10,
            }],
            "prices": [{
                "id": "1", "type": "BASE", "name": "Розничная цена",
                "amount": "19000.00", "currency": "RUB", "is_base": True,
            }],
        })
        self.assertEqual(product["category"]["id"], "27")
        self.assertEqual(product["category"]["path"], ["Наручные часы", "Ziiiro"])
        self.assertEqual(product["sale_price"]["value"], 19000.0)
        self.assertEqual(product["sale_price"]["role"], "base")
        self.assertEqual(product["images"][0]["width"], 1200)


class CatalogMatchingTest(unittest.TestCase):
    def test_matches_by_xml_id_before_sku(self):
        match = match_product(
            {"external_product_id": "1", "external_xml_id": "xml-1", "external_sku": "same", "name": "Watch"},
            [{"id": "a", "externalCode": "xml-1", "article": "other", "name": "Other"},
             {"id": "b", "externalCode": "xml-2", "article": "same", "name": "Watch"}],
        )
        self.assertEqual(match["product"]["id"], "a")
        self.assertEqual(match["method"], "xml_id")

    def test_matches_by_unique_sku(self):
        match = match_product(
            {"external_product_id": "1", "external_xml_id": "", "external_sku": "sku-1", "name": "Watch"},
            [{"id": "a", "article": "SKU-1", "name": "Other"}],
        )
        self.assertEqual(match["status"], "matched")
        self.assertEqual(match["method"], "sku")

    def test_ambiguous_exact_name_is_not_matched(self):
        match = match_product(
            {"external_product_id": "1", "external_xml_id": "", "external_sku": "", "name": " Watch  One "},
            [{"id": "a", "name": "watch one"}, {"id": "b", "name": "WATCH ONE"}],
        )
        self.assertEqual(match["status"], "ambiguous")
        self.assertIsNone(match["product"])

    def test_confirmed_mapping_has_highest_priority(self):
        match = match_product(
            {"external_product_id": "12", "external_xml_id": "xml", "external_sku": "", "name": "Watch"},
            [{"id": "confirmed", "externalCode": "other"}, {"id": "xml", "externalCode": "xml"}],
            {"bitrix:12": {"moysklad_product_id": "confirmed"}},
        )
        self.assertEqual(match["product"]["id"], "confirmed")
        self.assertEqual(match["method"], "confirmed_mapping")


class DryRunTest(unittest.TestCase):
    def test_repeated_dry_run_does_not_mutate_input(self):
        products = [normalize_product({"ID": 1, "NAME": "Watch", "XML_ID": "xml-1"})]
        vechasu = [{"id": "v1", "name": "Watch"}]
        original_products = copy.deepcopy(products)
        original_vechasu = copy.deepcopy(vechasu)
        first = build_report(products, vechasu, total=1)
        second = build_report(products, vechasu, total=1)
        self.assertEqual(first, second)
        self.assertEqual(products, original_products)
        self.assertEqual(vechasu, original_vechasu)
        self.assertEqual(first["writes_performed"], 0)

    def test_client_accessors_return_normalized_parts(self):
        product = normalize_product({
            "ID": 1, "PROPERTIES": [{"CODE": "COLOR", "VALUE": "Black"}],
            "PRICES": [{"PRICE": 100, "CURRENCY": "RUB"}], "IMAGES": ["/a.jpg"],
            "OFFERS": [{"ID": 2}],
        })
        self.assertEqual(len(BitrixCatalogReadOnlyClient.get_properties(product)), 1)
        self.assertEqual(len(BitrixCatalogReadOnlyClient.get_prices(product)), 1)
        self.assertEqual(len(BitrixCatalogReadOnlyClient.get_offers(product)), 1)
        self.assertEqual(BitrixCatalogReadOnlyClient.get_image_links(product), ["/a.jpg"])


if __name__ == "__main__":
    unittest.main()
