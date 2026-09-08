"""Existing imported names are not a new collision on an unrelated update."""
import test_stage2_products_api as api_tests
from app.services.excel_product_catalog import ExcelProductCatalog
from app.catalog_db import CatalogDatabase
from app.services.shared_catalog import DuplicateCatalogValueError
from app.services.product_bundles import ProductBundles
from app.services.bitrix_erp_product_sync import BitrixERPProductSync
from test_bitrix_erp_product_sync import product as bitrix_product
import unittest


class ProductDuplicateUpdateTest(unittest.TestCase):
    setUp = api_tests.Stage2ProductsApiTest.setUp
    tearDown = api_tests.Stage2ProductsApiTest.tearDown

    def catalog(self):
        return ExcelProductCatalog(CatalogDatabase(self.database_path))

    def legacy_pair(self):
        catalog = self.catalog()
        first = catalog.create_product('a.b.art Fashion Pulse Pink', article='as1608-02', brand='Alpha', category='Часы')
        second = catalog.create_product('a.b.art Fashion Pulse Pink', article='AS1609-02', brand='Alpha', category='Часы')
        return first, second

    def patch(self, product, payload):
        return self.client.patch('/api/products/{}'.format(product['id']), json=payload)

    def test_update_unchanged_name_and_sku_with_existing_imported_name(self):
        first, product = self.legacy_pair()
        response = self.patch(product, {'name': product['excel_name_raw'], 'article': product['excel_article'], 'brand_id': product['brand_id'], 'category_id': product['category_id']})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotEqual(first['id'], response.get_json()['data']['id'])

    def test_update_price_only_with_existing_imported_name(self):
        _, product = self.legacy_pair()
        response = self.patch(product, {'price': '123.45'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.catalog().get_product(product['id'])['bitrix_price_amount'], '123.45')

    def test_update_brand_category_and_self_exclusion(self):
        _, product = self.legacy_pair()
        for fields in ({'brand': 'New brand', 'category': 'New category'}, {'name': product['excel_name_raw'], 'article': product['excel_article']}):
            response = self.patch(product, fields)
            self.assertEqual(response.status_code, 200, response.get_json())

    def test_bundle_configuration_and_card_save(self):
        _, product = self.legacy_pair()
        catalog = self.catalog()
        component = catalog.create_product('Component', article='COMPONENT')
        response = self.client.put('/api/v1/products/{}/bundle'.format(product['id']), json={'is_bundle': True, 'components': [{'component_id': component['id'], 'quantity': 1}]})
        self.assertEqual(response.status_code, 200, response.get_json())
        response = self.patch(product, {'name': product['excel_name_raw'], 'article': product['excel_article'], 'price': '100'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(ProductBundles(catalog.database).get(product['id'])['components'][0]['component_id'], component['id'])

    def test_new_name_and_article_conflicts_identify_other_product(self):
        first, product = self.legacy_pair()
        other = self.catalog().create_product('Other name', article='OTHER', brand='Alpha', category='Часы')
        for fields, conflict in (({'article': first['excel_article']}, first), ({'name': other['excel_name_raw']}, other)):
            response = self.patch(product, fields)
            self.assertEqual(response.status_code, 409, response.get_json())
            self.assertEqual(response.get_json()['fields']['existing']['id'], str(conflict['id']))
        self.assertEqual(self.catalog().get_product(product['id'])['excel_article'], 'AS1609-02')

    def test_taxonomy_change_cannot_introduce_name_collision(self):
        first, _ = self.legacy_pair()
        product = self.catalog().create_product(first['excel_name_raw'], article='OTHER', brand='Beta', category='Ремешки')
        response = self.patch(product, {'brand_id': first['brand_id'], 'category_id': first['category_id']})
        self.assertEqual(response.status_code, 409, response.get_json())

    def test_create_real_duplicate_name_or_sku_still_rejected(self):
        first, _ = self.legacy_pair()
        for name, article in ((first['excel_name_raw'], 'NEW-SKU'), ('New name', first['excel_article'])):
            with self.assertRaises(DuplicateCatalogValueError):
                self.catalog().create_product(name, article=article, brand='Alpha', category='Часы', enforce_unique=True)

    def test_import_duplicate_bitrix_id_is_not_created_again(self):
        service = BitrixERPProductSync(self.catalog().database)
        source = bitrix_product()
        created = service.apply_single(source, 'create', quantity=2)
        duplicate = service.apply_single(source, 'create', quantity=2)
        self.assertEqual(created['status'], 'created')
        self.assertEqual(duplicate['status'], 'stock_added')
        self.assertEqual(duplicate['erp_product_id'], created['erp_product_id'])
