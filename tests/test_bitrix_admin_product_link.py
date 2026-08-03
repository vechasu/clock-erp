import unittest

from app import web


PUBLIC_URL = "https://www.tictactoy.ru/catalog/watches/luch/011211757/"
ADMIN_URL = (
    "https://www.tictactoy.ru/bitrix/admin/iblock_element_edit.php"
    "?IBLOCK_ID=5&type=TicTacToy&ID=204699&lang=ru"
)


def catalog_template_product(element_id="204699"):
    product = {
        "id": 3677,
        "name": "011211757 Lady",
        "brand": "Luch",
        "categories": [],
        "active": True,
        "properties": [],
        "images": [],
        "prices": [],
        "offers": [],
        "mapping": None,
        "sync_history": [],
        "source_url": PUBLIC_URL,
        "external_product_id": element_id,
        "external_xml_id": "204699",
        "iblock_id": "5",
    }
    product.update(web.build_bitrix_product_links(element_id, PUBLIC_URL))
    return product


class BitrixAdminProductLinkTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True, AUTH_TESTING=False)

    def test_lady_uses_confirmed_bitrix_admin_contract(self):
        links = web.build_bitrix_product_links("204699", PUBLIC_URL)

        self.assertEqual(links["bitrix_element_id"], "204699")
        self.assertEqual(links["bitrix_iblock_id"], 5)
        self.assertEqual(links["bitrix_admin_url"], ADMIN_URL)
        self.assertEqual(links["public_product_url"], PUBLIC_URL)
        self.assertNotEqual(links["bitrix_admin_url"], PUBLIC_URL)

    def test_warehouse_projection_separates_admin_and_public_urls(self):
        item = web.build_excel_warehouse_items([{
            "id": 9037,
            "excel_name_raw": "011211757 Lady",
            "bitrix_external_product_id": "204699",
            "bitrix_source_url": PUBLIC_URL,
        }])[0]

        self.assertEqual(item["bitrix_element_id"], "204699")
        self.assertEqual(item["bitrix_admin_url"], ADMIN_URL)
        self.assertEqual(item["public_product_url"], PUBLIC_URL)

    def test_missing_or_invalid_bitrix_id_has_no_false_admin_fallback(self):
        for element_id in (None, "", "not-an-id", "0", "-1"):
            with self.subTest(element_id=element_id):
                links = web.build_bitrix_product_links(element_id, PUBLIC_URL)
                self.assertEqual(links["bitrix_element_id"], "")
                self.assertEqual(links["bitrix_admin_url"], "")
                self.assertEqual(links["public_product_url"], PUBLIC_URL)

    def test_catalog_card_admin_link_has_safe_new_tab_attributes(self):
        with web.app.test_request_context("/catalog/3677"):
            html = web.render_template(
                "catalog_detail.html",
                product=catalog_template_product(),
            )

        admin_anchor = html.split(">Открыть в Bitrix</a>", 1)[0].rsplit(
            "<a", 1
        )[1]
        self.assertIn(
            'href="{}"'.format(ADMIN_URL.replace("&", "&amp;")),
            admin_anchor,
        )
        self.assertIn('target="_blank"', admin_anchor)
        self.assertIn('rel="noopener noreferrer"', admin_anchor)
        self.assertNotIn(PUBLIC_URL, admin_anchor)
        self.assertIn(
            'href="{}"'.format(PUBLIC_URL),
            html.split(">Открыть на сайте</a>", 1)[0],
        )

    def test_catalog_card_without_id_is_disabled_but_keeps_public_link(self):
        with web.app.test_request_context("/catalog/3677"):
            html = web.render_template(
                "catalog_detail.html",
                product=catalog_template_product(element_id=""),
            )

        disabled = html.split(">Открыть в Bitrix</span>", 1)[0].rsplit(
            "<span", 1
        )[1]
        self.assertIn('aria-disabled="true"', disabled)
        self.assertIn('title="Карточка Bitrix не найдена"', disabled)
        self.assertNotIn("href=", disabled)
        self.assertIn("Открыть на сайте", html)


if __name__ == "__main__":
    unittest.main()
