import base64
from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductBatchService, ExcelProductCatalog
from app.services.supplies import SupplyEngine


class ManualProductCreationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CatalogDatabase(Path(self.temp.name) / 'catalog.db')
        env = mock.patch.dict('os.environ', {'CATALOG_DATABASE_PATH': str(self.db.path)})
        env.start()
        self.addCleanup(env.stop)
        config = mock.patch.dict(web.app.config, TESTING=True, AUTH_TESTING=False)
        config.start()
        self.addCleanup(config.stop)
        self.db.initialize()
        # Existing catalog batch, as used by the restored manual creation model.
        ExcelProductBatchService(self.db).apply([{
            'excel_row': 2, 'excel_name': 'Seed', 'excel_name_raw': 'Seed',
            'excel_article': 'SEED', 'excel_brand': 'Known', 'category': 'Watches',
            'stock': 0, 'stock_valid': True, 'cell': '', 'match_status': 'not_found',
            'match_method': 'test', 'confidence': 0, 'alternatives': [],
        }], 'a' * 64, 'seed.xlsx')
        self.client = web.app.test_client()

    def create(self, **extra):
        response = self.client.post('/api/v1/products', json={
            'name': 'Manual watch', 'brand': 'Known', 'category': 'Watches',
            'article': 'MANUAL-1', 'model': 'M-1', 'cell': 'A-1', **extra,
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()['data']

    def test_manual_card_without_bitrix_has_zero_stock(self):
        with mock.patch.object(web, 'MoySkladClient') as remote, \
                mock.patch.object(web, '_bitrix_single_client') as bitrix:
            product = self.create()
            saved = ExcelProductCatalog(self.db).get_product(product['id'])
            self.assertFalse(saved.get('bitrix_external_product_id'))
            self.assertEqual(saved['stock'], 0)
            self.assertEqual(saved['excel_article'], 'MANUAL-1')
            self.assertEqual(SupplyEngine(self.db).movements(), [])
            remote.assert_not_called()
            bitrix.assert_not_called()

    def test_create_inside_new_supply_add_before_post_and_stock_after_post(self):
        draft = self.client.post('/api/v1/receipts/supplies', json={
            'title': 'New supply', 'comment': 'Unposted',
        }).get_json()['data']
        product = self.create()
        response = self.client.post('/api/v1/receipts/supplies/' + draft['id'] + '/items',
                                    json={'product_id': int(product['id']), 'quantity': 4},
                                    headers={'Idempotency-Key': 'manual-supply-item'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['data']['status'], 'draft')
        self.assertEqual(response.get_json()['data']['items'][0]['quantity'], 4)
        self.assertEqual(ExcelProductCatalog(self.db).get_product(product['id'])['stock'], 0)
        self.assertEqual(SupplyEngine(self.db).movements(), [])
        response = self.client.post('/api/v1/receipts/supplies/' + draft['id'] + '/post')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ExcelProductCatalog(self.db).get_product(product['id'])['stock'], 4)
        self.client.post('/api/v1/receipts/supplies/' + draft['id'] + '/post')
        self.assertEqual(ExcelProductCatalog(self.db).get_product(product['id'])['stock'], 4)

    def test_creation_cannot_set_stock(self):
        response = self.client.post('/api/v1/products', json={'name': 'Invalid', 'stock': 7})
        self.assertEqual(response.status_code, 422)

    def test_manual_photo_uses_local_store(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        response = self.client.post('/api/v1/products', data={
            'name': 'Photo watch', 'product_image': (BytesIO(png), 'photo.png'),
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        saved = ExcelProductCatalog(self.db).get_product(response.get_json()['data']['id'])
        self.assertTrue(saved['local_image_path'])
        self.assertEqual(saved['stock'], 0)

    def test_legacy_receipt_creation_remains_retired(self):
        self.assertEqual(self.client.post('/receipts/catalog/create').status_code, 410)

    def test_products_and_supplies_share_restored_manual_form(self):
        products = self.client.get('/warehouse').get_data(as_text=True)
        supplies = self.client.get('/app/receipts').get_data(as_text=True)
        for page in (products, supplies):
            self.assertIn('id="manual-product-form"', page)
            self.assertIn('id="manualProductBrandCombobox"', page)
            self.assertIn('id="manualProductCategoryCombobox"', page)
            self.assertIn('id="manualProductCombobox"', page)
            self.assertIn('Добавить новый товар', page)
