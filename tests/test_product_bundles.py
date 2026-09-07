import unittest
from concurrent.futures import ThreadPoolExecutor
import test_sales_inventory as sales_tests
from app.services.component_inventory import ComponentInventory, balance
from app.services.product_bundles import ProductBundles, BundleError
from app.services.sales_inventory import InsufficientStockError


class ProductBundlesTest(unittest.TestCase):
    setUp = sales_tests.SalesInventoryTest.setUp
    tearDown = sales_tests.SalesInventoryTest.tearDown
    create_product = sales_tests.SalesInventoryTest.create_product
    def stock(self, product_id):
        with self.database.connect() as connection:
            return balance(connection, product_id)
    payload = staticmethod(sales_tests.SalesInventoryTest.payload)

    def setup_bundle(self, head_stock=5, strap_stock=8, head_quantity=1):
        head = self.create_product(head_stock, 'Head', 'HEAD')
        strap = self.create_product(strap_stock, 'Strap', 'STRAP')
        sku = self.create_product(0, 'Klok-07 Milanese Black', 'SKU')
        service = ProductBundles(self.database)
        parts = [{'component_id': head['id'], 'quantity': head_quantity},
                 {'component_id': strap['id'], 'quantity': 1}]
        service.configure(sku['id'], parts)
        ComponentInventory(self.database).confirm(head['id'], head_stock)
        ComponentInventory(self.database).confirm(strap['id'], strap_stock)
        return service, sku, head, strap, parts

    def test_unconfigured_is_ordinary_and_local(self):
        product = self.create_product(3, 'Klokers TID M2Zet', 'LOCAL')
        self.assertFalse(ProductBundles(self.database).get(product['id'])['is_bundle'])
        self.assertFalse(product.get('bitrix_external_product_id'))
        self.inventory.create_sale(self.payload(product), product['id'], 1, 100)
        self.assertEqual(self.stock(product['id']), 2)

    def test_validation_and_nonzero_conversion(self):
        service, sku, head, strap, parts = self.setup_bundle()
        for invalid in ([], [parts[0], parts[0]],
                        [{'component_id': sku['id'], 'quantity': 1}]):
            with self.subTest(invalid=invalid), self.assertRaises(BundleError):
                service.configure(sku['id'], invalid)
        for quantity in (0, -1, 0.5, 'NaN', 'Infinity', True):
            with self.subTest(quantity=quantity), self.assertRaises(BundleError):
                service.configure(sku['id'], [{'component_id': head['id'], 'quantity': quantity}])
        with self.assertRaises(BundleError):
            service.configure(head['id'], [{'component_id': strap['id'], 'quantity': 1}])
        self.assertEqual(self.stock(head['id']), 5)
        self.assertEqual(len(service.get(sku['id'])['components']), 2)

    def test_cycles_and_nested_bundles_rejected(self):
        service, sku, head, strap, parts = self.setup_bundle(0)
        with self.assertRaises(BundleError):
            service.configure(head['id'], [{'component_id': sku['id'], 'quantity': 1}])
        other = self.create_product(0, 'Other', 'OTHER')
        with self.assertRaises(BundleError):
            service.configure(other['id'], [{'component_id': sku['id'], 'quantity': 1}])

    def test_availability_multiplier_sale_and_historical_cancellation(self):
        service, sku, head, strap, parts = self.setup_bundle(head_quantity=2)
        self.assertEqual(service.get(sku['id'])['available_to_assemble'], 2)
        sale = self.inventory.create_sale(self.payload(sku), sku['id'], 2, 100, idempotency_key='sale')
        self.assertEqual((self.stock(head['id']), self.stock(strap['id']), self.stock(sku['id'])), (1, 6, 0))
        repeated = self.inventory.create_sale(self.payload(sku), sku['id'], 2, 100, idempotency_key='sale')
        self.assertEqual(sale['id'], repeated['id'])
        service.configure(sku['id'], [parts[1]])
        self.inventory.cancel_sale(sale['id'])
        self.inventory.cancel_sale(sale['id'])
        self.assertEqual((self.stock(head['id']), self.stock(strap['id'])), (5, 8))
        with self.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM erp_sale_items').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT product_id FROM erp_sale_items').fetchone()[0], int(sku['id']))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM erp_sale_component_snapshots').fetchone()[0], 2)
            self.assertFalse(connection.execute('PRAGMA foreign_key_check').fetchall())

    def test_shortage_and_failure_roll_back_everything(self):
        service, sku, head, strap, parts = self.setup_bundle(5, 0)
        with self.assertRaises(InsufficientStockError):
            self.inventory.create_sale(self.payload(sku), sku['id'], 1, 100)
        self.assertEqual(self.stock(head['id']), 5)
        service.configure(sku['id'], [parts[0]])
        def fail(connection):
            raise RuntimeError('forced')
        with self.assertRaises(RuntimeError):
            self.inventory.create_sale(self.payload(sku), sku['id'], 1, 100, failure_hook=fail)
        self.assertEqual(self.stock(head['id']), 5)
        self.assertEqual(self.inventory.list_sales(), [])

    def test_concurrent_shared_component(self):
        service, sku, head, strap, parts = self.setup_bundle(1)
        other = self.create_product(0, 'Other', 'OTHER')
        service.configure(other['id'], parts)
        def sell(product):
            try:
                self.inventory.create_sale(self.payload(product, str(product['id'])), product['id'], 1, 100)
                return True
            except InsufficientStockError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(sell, [sku, other]))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(self.stock(head['id']), 0)
        self.assertEqual(service.get(other['id'])['available_to_assemble'], 0)
        self.assertEqual(service.get(sku['id'])['available_to_assemble'], 0)

    def test_batch_sharing_rolls_back(self):
        service, sku, head, strap, parts = self.setup_bundle(1)
        with self.assertRaises(InsufficientStockError):
            self.inventory.create_sale_batch(self.payload(sku), [
                {'product_id': sku['id'], 'quantity': 1, 'unit_price': 100},
                {'product_id': head['id'], 'quantity': 1, 'unit_price': 10},
            ])
        self.assertEqual(self.stock(head['id']), 1)
        self.assertEqual(self.inventory.list_sales(), [])

    def test_partial_return_then_cancel_uses_snapshot(self):
        service, sku, head, strap, parts = self.setup_bundle()
        self.inventory.create_sale(self.payload(sku), sku['id'], 2, 100)
        service.configure(sku['id'], [], enabled=False)
        self.inventory.return_sale('sale-1', 1, idempotency_key='return')
        self.inventory.return_sale('sale-1', 1, idempotency_key='return')
        self.assertEqual(self.stock(head['id']), 4)
        self.inventory.cancel_sale('sale-1')
        self.assertEqual(self.stock(head['id']), 5)

    def test_bitrix_sync_preserves_bundle_components_and_movements(self):
        from test_bitrix_erp_product_sync import product
        from app.services.bitrix_erp_product_sync import BitrixERPProductSync
        sync = BitrixERPProductSync(self.database)
        incoming = product(identity="777", name="Commercial", stock=0)
        from app.services.bitrix_catalog_importer import BitrixCatalogImporter
        importer = BitrixCatalogImporter(self.database)
        importer.import_products([incoming], mode="full_sync")
        result = sync.apply_products([incoming])[0]
        sku_id = result['erp_product_id']
        local = self.catalog.create_product(name='Local Head', article='LOCAL-HEAD', stock=5,
                                            brand='Brand', category='Components', local_component=True)
        strap = self.create_product(8, 'Strap', 'STRAP')
        service = ProductBundles(self.database)
        parts = [{'component_id': local['id'], 'quantity': 1},
                 {'component_id': strap['id'], 'quantity': 1}]
        service.configure(sku_id, parts)
        ComponentInventory(self.database).confirm(local['id'], 5)
        ComponentInventory(self.database).confirm(strap['id'], 8)
        self.inventory.create_sale({'id': 'sync-sale'}, sku_id, 1, 100)
        changed = dict(incoming, name='Commercial Updated', stock=99)
        importer.import_products([changed], mode="full_sync")
        sync.apply_products([changed])
        self.assertEqual(len(service.get(sku_id)['components']), 2)
        self.assertEqual(service.get(sku_id)['available_to_assemble'], 4)
        self.assertEqual(self.stock(local['id']), 4)
        self.assertEqual(self.stock(sku_id), 0)
        self.assertTrue(self.inventory.list_movements(local['id']))
        collision = product(identity='778', name='Local Head', sku='LOCAL-HEAD', stock=7)
        importer.import_products([collision], mode='full_sync')
        imported = sync.apply_products([collision])[0]
        self.assertNotEqual(int(imported['erp_product_id']), int(local['id']))
        self.assertFalse(self.catalog.get_product(local['id']).get('bitrix_external_product_id'))

    def test_receipt_changes_availability_and_migration_preserves_stock(self):
        from app.services.receipt_inventory import ReceiptInventory
        from app.schema_migrations import apply_migrations
        service, sku, head, strap, parts = self.setup_bundle(0)
        ReceiptInventory(self.database).create_receipt({"id": "receipt-bundle"}, [
            {'product_id': head['id'], 'quantity': 3, 'unit_price': 10}
        ])
        self.assertEqual(service.get(sku['id'])['available_to_assemble'], 3)
        before = [self.stock(p['id']) for p in (sku, head, strap)]
        apply_migrations(self.database.path, app_commit='bundle-test')
        self.assertEqual([self.stock(p['id']) for p in (sku, head, strap)], before)

    def test_sales_picker_has_separate_assembly_availability(self):
        from app.services.shared_catalog import SharedCatalog
        service, sku, head, strap, parts = self.setup_bundle()
        picker = SharedCatalog(self.database)
        items = picker.list_products(in_stock=True, include_assemblable=True)
        bundle = next(item for item in items if int(item['id']) == int(sku['id']))
        self.assertEqual(bundle['stock'], 0)
        self.assertEqual(bundle['available_to_assemble'], 5)
        physical = picker.list_products(in_stock=True)
        self.assertNotIn(int(sku['id']), [int(item['id']) for item in physical])

    def test_archived_component_and_own_stock_protection(self):
        import sqlite3
        service, sku, head, strap, parts = self.setup_bundle()
        self.inventory.create_sale(self.payload(sku), sku['id'], 1, 100)
        self.catalog.delete_product(head['id'], force=True)
        self.assertEqual(service.get(sku['id'])['available_to_assemble'], 0)
        self.inventory.cancel_sale('sale-1')
        with self.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT stock FROM catalog_excel_products WHERE id=?', (head['id'],)).fetchone()[0], 5)
        with self.database.transaction() as connection:
            connection.execute('UPDATE catalog_excel_products SET stock=5 WHERE id=?', (sku['id'],))
        self.assertEqual(self.stock(sku['id']), 5)

    def test_configuration_and_local_component_forms(self):
        import os
        from unittest import mock
        from app import web
        service, sku, head, strap, parts = self.setup_bundle()
        with mock.patch.dict(os.environ, {'CATALOG_DATABASE_PATH': str(self.database_path)}), mock.patch.dict(web.app.config, {'TESTING': True}):
            client = web.app.test_client()
            page = client.get('/app/products/{}/bundle'.format(sku['id']))
            self.assertEqual(page.status_code, 200)
            self.assertIn('Доступно к сборке:'.encode(), page.data)
            rejected = client.put('/api/v1/products/{}/bundle'.format(sku['id']), json={
                'is_bundle': True, 'components': []})
            self.assertEqual(rejected.status_code, 422)
            saved = client.put('/api/v1/products/{}/bundle'.format(sku['id']), json={
                'is_bundle': True, 'components': parts})
            self.assertEqual(saved.status_code, 200)
            created = client.post('/app/products/{}/bundle'.format(sku['id']), data={
                'action':'create_component','name':'New local component','article':'NEW-LOCAL','brand':'Brand'})
            self.assertEqual(created.status_code, 200)
            with self.database.connect() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM erp_local_components').fetchone()[0], 1)
            ordinary = client.get('/api/v1/products/{}'.format(head['id']))
            self.assertEqual(ordinary.status_code, 200)
            self.assertEqual(ordinary.get_json()['data']['stock'], 5)
            picker = client.get('/api/v1/sales/catalog')
            self.assertEqual(picker.status_code, 200)
            selected = next(item for item in picker.get_json()['data'] if int(item['id']) == int(sku['id']))
            self.assertEqual(selected['available_to_assemble'], 5)
            with mock.patch.object(web, 'find_api_sale', return_value=None):
                payload = {'created_at':'2026-09-07','source':'Tictactoy',
                           'product_id':str(sku['id']),'quantity':2,'unit_price':100,'order_number':'BUNDLE-API'}
                sale = client.post('/api/v1/sales', json=payload, headers={'Idempotency-Key':'bundle-api'})
                self.assertEqual(sale.status_code, 201, sale.get_json())
                repeated = client.post('/api/v1/sales', json=payload, headers={'Idempotency-Key':'bundle-api'})
                self.assertEqual(repeated.status_code, 201)
                self.assertEqual(self.stock(head['id']), 3)
                shortage = client.post('/api/v1/sales', json=dict(payload, quantity=4, order_number='TOO-MANY'))
                self.assertEqual(shortage.status_code, 409)
                self.assertEqual(self.stock(head['id']), 3)


    def test_concurrent_retry_and_reposting_after_cancel(self):
        service, sku, head, strap, parts = self.setup_bundle()
        def sell(_):
            return self.inventory.create_sale(self.payload(sku), sku['id'], 1, 100, idempotency_key='retry')['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(sell, [1, 2])), ['sale-1', 'sale-1'])
        self.assertEqual(self.stock(head['id']), 4)
        self.inventory.cancel_sale('sale-1')
        self.inventory.create_sale(self.payload(sku, 'sale-2'), sku['id'], 1, 100)
        self.assertEqual(self.stock(head['id']), 4)

    def test_successful_multi_sku_sale_shared_components_and_cancel(self):
        service, sku, head, strap, parts = self.setup_bundle()
        other = self.create_product(0, 'Other bundle', 'OTHER-BUNDLE')
        service.configure(other['id'], [parts[0]])
        self.inventory.create_sale_batch({'id': 'batch'}, [
            {'product_id': sku['id'], 'quantity': 2, 'unit_price': 100},
            {'product_id': other['id'], 'quantity': 1, 'unit_price': 80},
        ])
        self.assertEqual(self.stock(head['id']), 2)
        self.assertEqual(self.stock(strap['id']), 6)
        self.inventory.return_sale('batch', 2)
        self.assertEqual(self.inventory.get_sale('batch')['status'], 'partially_returned')
        self.inventory.cancel_sale('batch')
        self.assertEqual(self.stock(head['id']), 5)
        self.assertEqual(self.stock(strap['id']), 8)

    def test_return_failure_is_atomic(self):
        service, sku, head, strap, parts = self.setup_bundle()
        self.inventory.create_sale(self.payload(sku), sku['id'], 1, 100)
        def fail(connection):
            raise RuntimeError('forced')
        with self.assertRaises(RuntimeError):
            self.inventory.return_sale('sale-1', 1, failure_hook=fail)
        self.assertEqual(self.stock(head['id']), 4)
        self.assertEqual(self.stock(strap['id']), 7)
        self.assertEqual(self.inventory.get_sale('sale-1')['returned_quantity'], 0)

    def test_local_component_cannot_be_manually_linked_to_bitrix(self):
        import sqlite3
        local = self.catalog.create_product(name='Local', stock=0, local_component=True)
        with self.assertRaises(sqlite3.IntegrityError), self.database.transaction() as connection:
            connection.execute("UPDATE catalog_excel_products SET bitrix_external_product_id='99' WHERE id=?", (local['id'],))

    def test_snapshot_prevents_destroying_component_history(self):
        import sqlite3
        service, sku, head, strap, parts = self.setup_bundle()
        self.inventory.create_sale(self.payload(sku), sku['id'], 1, 100)
        service.configure(sku['id'], [parts[1]])
        with self.assertRaises(sqlite3.IntegrityError), self.database.transaction() as connection:
            connection.execute('DELETE FROM catalog_excel_products WHERE id=?', (head['id'],))
        self.assertEqual(self.stock(head['id']), 4)

    def test_receiving_bundle_sku_is_rejected(self):
        from app.services.receipt_inventory import ReceiptInventory, ReceiptInventoryError
        service, sku, head, strap, parts = self.setup_bundle()
        with self.assertRaises(ReceiptInventoryError):
            ReceiptInventory(self.database).create_receipt({'id':'wrong-receipt'}, [
                {'product_id':sku['id'],'quantity':1,'unit_price':10}])
        self.assertEqual(self.stock(sku['id']), 0)
        self.assertEqual(self.stock(head['id']), 5)

    def test_inventory_component_count_recalculates_assembly_and_locks_sale(self):
        from app.services.brand_inventory import BrandInventory
        from app.services.sales_inventory import SalesInventoryError
        service, sku, head, strap, parts = self.setup_bundle()
        counter = BrandInventory(self.database)
        with self.database.connect() as connection:
            brand_id = connection.execute('SELECT brand_id FROM catalog_excel_products WHERE id=?', (head['id'],)).fetchone()[0]
        session = counter.start(brand_id, 'Tester')[0]
        with self.assertRaises(SalesInventoryError):
            self.inventory.create_sale(self.payload(sku), sku['id'], 1, 100)
        for item in counter.list_items(session['id']):
            actual = 3 if int(item['product_id']) == int(head['id']) else item['snapshot_stock']
            counter.confirm(session['id'], item['id'], actual, 'Tester', 'count-{}'.format(item['id']))
        counter.complete(session['id'], 'Tester', confirmation=True)
        self.assertEqual(service.get(sku['id'])['available_to_assemble'], 3)

    def test_additive_upgrade_of_existing_catalog_keeps_entire_product_row(self):
        import sqlite3
        from app.schema_migrations import apply_migrations, BUNDLE_MIGRATION_ID, COMPONENT_MIGRATION_ID
        product = self.create_product(17)
        with sqlite3.connect(str(self.database.path)) as connection:
            before = connection.execute('SELECT * FROM catalog_excel_products WHERE id=?', (product['id'],)).fetchone()
            for table in ('erp_physical_documents','erp_component_inventory_events','erp_bundle_transitions','erp_component_inventory'):
                connection.execute('DROP TABLE '+table)
            connection.execute('DELETE FROM erp_migration_ledger WHERE migration_id=?', (COMPONENT_MIGRATION_ID,))
            connection.execute('DROP TRIGGER trg_local_component_bitrix')
            for table in ('erp_sale_component_snapshots','erp_bundle_components','erp_product_bundles','erp_local_components'):
                connection.execute('DROP TABLE '+table)
            connection.execute('DELETE FROM erp_migration_ledger WHERE migration_id=?', (BUNDLE_MIGRATION_ID,))
        apply_migrations(self.database.path, app_commit='upgrade-test')
        with self.database.connect() as connection:
            self.assertEqual(tuple(connection.execute('SELECT * FROM catalog_excel_products WHERE id=?', (product['id'],)).fetchone()), before)
            self.assertFalse(connection.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
