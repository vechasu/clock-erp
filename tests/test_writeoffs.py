import unittest
from concurrent.futures import ThreadPoolExecutor
from app.services.writeoffs import Writeoffs
from app.services.sales_inventory import SalesInventoryError
from app.services.product_bundles import ProductBundles
from app.services.component_inventory import ComponentInventory, balance
from tests.test_sales_inventory import SalesInventoryTest


class WriteoffsTest(unittest.TestCase):
    setUp = SalesInventoryTest.setUp
    tearDown = SalesInventoryTest.tearDown
    create_product = SalesInventoryTest.create_product
    stock = SalesInventoryTest.stock
    # Reuse the migrated fixture, not the unrelated sales test cases.
    def payload_writeoff(self, product, quantity=2):
        return {'product_id':product['id'],'quantity':quantity,'reason':'Брак','comment':'test'}

    actor = {'actor_id':'tester','actor_name':'Тест','actor_type':'user'}

    def test_writeoff_boundaries(self):
        service = Writeoffs(self.database)
        for stock, quantity, expected in [(10,2,8),(1,1,0),(0,1,None),(2,3,None),(2,0,None),(2,-1,None),(2,1.5,None),(2,True,None),(2,'1.0',None)]:
            with self.subTest(stock=stock, quantity=quantity):
                product = self.create_product(stock,article='A'+str(stock)+str(quantity))
                if expected is None:
                    with self.assertRaises(ValueError):
                        service.create(self.payload_writeoff(product,quantity),self.actor)
                    self.assertEqual(self.stock(product['id']),stock)
                else:
                    service.create(self.payload_writeoff(product,quantity),self.actor)
                    self.assertEqual(self.stock(product['id']),expected)
        with self.database.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM erp_sales').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM erp_sale_items').fetchone()[0],0)

    def test_cancel_idempotent_and_persistent(self):
        p=self.create_product(10);w=Writeoffs(self.database)
        row=w.create(self.payload_writeoff(p),self.actor,'same-key')
        self.assertEqual(w.create(self.payload_writeoff(p),self.actor,'same-key')['id'],row['id'])
        self.assertEqual(self.stock(p['id']),8)
        for _ in range(2):
            self.assertEqual(Writeoffs(self.database).cancel(row['id'],self.actor)['status'],'cancelled')
        self.assertEqual(self.stock(p['id']),10)
        self.assertEqual(w.list({})['total'],1)
        movements=self.inventory.list_movements(p['id'])
        self.assertEqual({r['label'] for r in movements if r['source'] in ('Списание','Отмена списания')},{'Списание','Отмена списания'})

    def test_failure_rolls_back_everything(self):
        p=self.create_product(10);w=Writeoffs(self.database)
        def fail(c): raise RuntimeError('injected')
        with self.assertRaises(RuntimeError):
            w.create(self.payload_writeoff(p),self.actor,failure_hook=fail)
        self.assertEqual(self.stock(p['id']),10); self.assertEqual(w.list({})['total'],0)
        row=w.create(self.payload_writeoff(p),self.actor)
        with self.assertRaises(RuntimeError): w.cancel(row['id'],self.actor,failure_hook=fail)
        self.assertEqual(self.stock(p['id']),8);self.assertEqual(w.list({})['rows'][0]['status'],'posted')

    def test_concurrent_overdraw_and_cancel(self):
        p=self.create_product(3);w=Writeoffs(self.database)
        def create(_):
            try: return w.create(self.payload_writeoff(p),self.actor)
            except SalesInventoryError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: rows=list(pool.map(create,range(2)))
        self.assertEqual(sum(r is not None for r in rows),1);self.assertEqual(self.stock(p['id']),1)
        row=next(r for r in rows if r)
        with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _:w.cancel(row['id'],self.actor),range(2)))
        self.assertEqual(self.stock(p['id']),3)

    def test_bundle_snapshot_and_physical_balance(self):
        sku=self.create_product(900,article='bundle');component=self.create_product(800,article='part')
        bundles=ProductBundles(self.database);bundles.configure(sku['id'],[{'component_id':component['id'],'quantity':2}])
        ComponentInventory(self.database).confirm(component['id'],10,'Tester')
        w=Writeoffs(self.database);row=w.create(self.payload_writeoff(sku),self.actor)
        with self.database.connect() as c:self.assertEqual(balance(c,component['id']),6)
        bundles.configure(sku['id'],[{'component_id':component['id'],'quantity':3}])
        w.cancel(row['id'],self.actor)
        with self.database.connect() as c:self.assertEqual(balance(c,component['id']),10)
        self.assertEqual(self.stock(sku['id']),900)

    def test_validation_and_pagination(self):
        p=self.create_product(10);w=Writeoffs(self.database)
        for extra in ({'reason':'unknown'},{'reason':'Прочее','comment':''},{'product_id':999999},{'product_id':True}):
            with self.assertRaises(ValueError):w.create(dict(self.payload_writeoff(p),**extra),self.actor)
        for _ in range(3):w.create(self.payload_writeoff(p,1),self.actor)
        self.assertEqual(len(w.list({'per_page':'2','page':'2'})['rows']),1)
        self.assertEqual(w.list({'status':'cancelled'})['total'],0)

    def test_upgrade_is_repeatable_preserves_stock(self):
        import sqlite3
        from app.schema_migrations import apply_migrations, WRITEOFF_MIGRATION_ID
        p=self.create_product(7)
        with sqlite3.connect(str(self.database.path)) as c:
            before=c.execute('SELECT * FROM catalog_excel_products ORDER BY id').fetchall()
            c.execute('DROP TABLE erp_writeoff_items')
            c.execute('DROP TABLE erp_writeoffs')
            c.execute('DELETE FROM erp_migration_ledger WHERE migration_id=?',(WRITEOFF_MIGRATION_ID,))
        for _ in range(2): apply_migrations(self.database.path,app_commit='writeoff-upgrade-test')
        with self.database.connect() as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM catalog_excel_products ORDER BY id')],before)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())

    def test_inventory_lock_blocks_creation_and_cancel(self):
        from unittest import mock
        p=self.create_product(5);w=Writeoffs(self.database)
        with mock.patch('app.services.writeoffs.assert_products_unlocked',side_effect=SalesInventoryError('Инвентаризация')):
            with self.assertRaises(SalesInventoryError): w.create(self.payload_writeoff(p),self.actor)
        self.assertEqual(self.stock(p['id']),5)
        row=w.create(self.payload_writeoff(p),self.actor)
        with mock.patch('app.services.writeoffs.assert_products_unlocked',side_effect=SalesInventoryError('Инвентаризация')):
            with self.assertRaises(SalesInventoryError): w.cancel(row['id'],self.actor)
        self.assertEqual(self.stock(p['id']),3)


del SalesInventoryTest

from tests.test_stage2_sales_api import Stage2SalesApiTest
from unittest import mock
from app import web


class WriteoffsApiTest(unittest.TestCase):
    setUp = Stage2SalesApiTest.setUp
    tearDown = Stage2SalesApiTest.tearDown

    def test_api_stock_sales_and_render(self):
        before=self.client.get('/api/v1/sales').get_json()['data']
        response=self.client.post('/api/v1/writeoffs',json={'product_id':self.product['id'],'quantity':2,'reason':'Брак'})
        self.assertEqual(response.status_code,201,response.get_json())
        row=response.get_json()['data']
        self.assertEqual(self.client.get('/api/v1/sales').get_json()['data'],before)
        page=self.client.get('/app/sales?source=writeoff')
        self.assertEqual(page.status_code,200)
        self.assertIn('Добавить списание',page.get_data(as_text=True))
        self.assertIn('Casio',page.get_data(as_text=True))
        self.assertEqual(self.client.get('/api/v1/writeoffs').get_json()['data']['total'],1)
        for _ in range(2):
            self.assertEqual(self.client.post('/api/v1/writeoffs/'+row['id']+'/cancel').status_code,200)

    def test_api_permissions(self):
        with mock.patch.object(web,'auth_is_enabled',return_value=True),mock.patch.object(web,'current_auth_user',return_value={'role':'viewer'}):
            self.assertEqual(self.client.post('/api/v1/writeoffs',json={}).status_code,403)
            self.assertEqual(self.client.get('/api/v1/writeoffs').status_code,403)


del Stage2SalesApiTest
