import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.catalog_db import CatalogDatabase
from app.services.supplies import SupplyEngine, SupplyError
from app.services.receipt_inventory import ReceiptInventoryError
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import SalesInventory


class SupplyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CatalogDatabase(Path(self.temp.name) / 'catalog.db')
        self.db.initialize()
        self.engine = SupplyEngine(self.db)
        self.catalog = ExcelProductCatalog(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def product(self, stock=3, external='1'):
        p = self.engine.resolve_bitrix({'external_product_id':external, 'name':'Watch '+external, 'external_sku':'SKU-'+external, 'brand':'Casio', 'stock':999})
        with self.db.transaction() as c:
            c.execute('UPDATE catalog_excel_products SET stock = ? WHERE id = ?', (stock, p['id']))
        return int(p['id'])

    def stock(self, product):
        with self.db.connect() as c:
            return c.execute('SELECT stock FROM catalog_excel_products WHERE id = ?', (product,)).fetchone()[0]

    def draft(self, products=None):
        return self.engine.create('Casio сентябрь', actor='Creator', items=[{'product_id':p, 'quantity':q} for p,q in products or []])

    def test_empty_draft_and_delete_do_not_move_stock(self):
        p = self.product()
        d = self.draft()
        self.engine.update(d['id'], 'New title', 'Comment', [{'product_id':p,'quantity':5}])
        self.assertEqual(self.stock(p), 3)
        self.engine.delete(d['id'])
        self.assertEqual(self.stock(p), 3)
        self.assertEqual(self.engine.list(), [])
        self.assertEqual(self.engine.movements(), [])

    def test_post_three_items_snapshots_and_retry(self):
        ps = [self.product(3,str(i)) for i in range(3)]
        d = self.draft([(p,5) for p in ps])
        self.assertEqual(d['total_quantity'], 15)
        result = self.engine.post(d['id'], 'Poster')
        self.assertEqual(result['created_by'], 'Creator')
        self.assertEqual(result['posted_by'], 'Poster')
        self.assertEqual(result['status'], 'posted')
        rows = self.engine.movements()
        self.assertEqual(len(rows),3)
        self.assertEqual({r['source_id'] for r in rows},{d['id']})
        self.assertEqual(len({r['source_line_id'] for r in rows}),3)
        self.assertTrue(all(r['stock_before']==3 and r['stock_after']==8 for r in rows))
        self.engine.post(d['id'])
        self.assertEqual(self.stock(ps[0]),8)
        self.assertEqual(len(self.engine.movements()),3)
        for fn in [lambda:self.engine.delete(d['id']),lambda:self.engine.update(d['id'],'x','',[])]:
            with self.assertRaises(SupplyError): fn()

    def test_concurrent_double_post(self):
        p = self.product()
        d = self.draft([(p,5)])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.engine.post(d['id']),range(2)))
        self.assertTrue(all(r['status']=='posted' for r in results))
        self.assertEqual(self.stock(p),8)
        self.assertEqual(len(self.engine.movements()),1)

    def test_post_uses_current_stock(self):
        p = self.product()
        d = self.draft([(p,5)])
        with self.db.transaction() as c: c.execute('UPDATE catalog_excel_products SET stock = stock - 1 WHERE id = ?', (p,))
        result=self.engine.post(d['id'])
        self.assertEqual(result['items'][0]['stock_before'],2)
        self.assertEqual(self.stock(p),7)

    def test_atomic_failure(self):
        ps=[self.product(3,str(i)) for i in range(3)]
        d=self.draft([(p,5) for p in ps])
        def fail(_): raise RuntimeError('injected')
        with self.assertRaises(RuntimeError): self.engine.post(d['id'],failure_hook=fail)
        self.assertEqual([self.stock(p) for p in ps],[3,3,3])
        self.assertEqual(self.engine.get(d['id'])['status'],'draft')
        self.assertEqual(self.engine.movements(),[])

    def test_validation(self):
        p=self.product()
        d=self.draft()
        for q in (0,-1,1.5,'5',None,True,float('nan'),float('inf')):
            with self.subTest(q=q),self.assertRaises(SupplyError):
                self.engine.update(d['id'],'x','',[{'product_id':p,'quantity':q}])
        with self.assertRaises(SupplyError): self.draft([(p,1),(p,2)])
        self.assertEqual(self.stock(p),3)

    def test_bitrix_stock_never_used(self):
        product={'external_product_id':'12345','name':'Watch','external_sku':'CASIO-A','stock':47,'brand':'Casio','properties':[],'images':[]}
        p=self.engine.resolve_bitrix(product)
        self.assertEqual(p['stock'],0)
        self.assertEqual(self.engine.resolve_bitrix(dict(product,name='Changed'))['id'],p['id'])
        d=self.draft([(p['id'],6)])
        self.engine.post(d['id'])
        self.assertEqual(self.stock(p['id']),6)
        self.engine.resolve_bitrix(dict(product,stock=999))
        self.assertEqual(self.stock(p['id']),6)

    def test_new_existing_post_retry_and_append_preserve_cards(self):
        from app.services.receipt_inventory import ReceiptInventory
        source = {'external_product_id': '901', 'external_sku': 'TEST-901',
                  'name': 'Original', 'brand': 'A', 'category': {'name': 'B'}, 'stock': 999}
        first = self.engine.resolve_bitrix(source)
        self.assertEqual(first['stock'], 0)
        first_doc = self.draft([(first['id'], 2)])
        self.engine.post(first_doc['id'])
        self.assertEqual(self.stock(first['id']), 2)
        existing_id = self.product(3, '902')
        with self.db.connect() as c:
            before = dict(c.execute('SELECT * FROM catalog_excel_products WHERE id = ?', (existing_id,)).fetchone())
        matched = self.engine.resolve_bitrix(dict(source, external_product_id='902', brand='Changed', category={'name': 'Changed'}, name='Changed', stock=998))
        self.assertEqual(matched['id'], existing_id)
        document = self.draft([(existing_id, 2)])
        posted = self.engine.post(document['id'])
        self.assertEqual(posted['items'][0]['quantity'], 2)
        self.assertEqual(self.stock(existing_id), 5)
        self.engine.get(document['id'])
        self.engine.post(document['id'])
        with self.assertRaises(SupplyError):
            self.engine.update(document['id'], 'Saved', '', [{'product_id': existing_id, 'quantity': 2}])
        self.assertEqual(self.stock(existing_id), 5)
        self.engine.add_item(document['id'], first['id'], 2, 'append-once')
        self.engine.add_item(document['id'], first['id'], 2, 'append-once')
        self.assertEqual(self.stock(existing_id), 5)
        self.assertEqual(self.stock(first['id']), 4)
        with self.db.connect() as c:
            after = dict(c.execute('SELECT * FROM catalog_excel_products WHERE id = ?', (existing_id,)).fetchone())
            self.assertEqual(c.execute('SELECT count(*) FROM catalog_stock_movements WHERE receipt_id = ?', (document['id'],)).fetchone()[0], 2)
        for key in before.keys() - {'stock', 'stock_source', 'updated_at'}:
            self.assertEqual(after[key], before[key], key)
        with self.assertRaises(ReceiptInventoryError):
            ReceiptInventory(self.db).cancel_receipt(document['id'])
        self.assertEqual(self.stock(existing_id), 5)
        self.assertEqual(self.stock(first['id']), 4)

    def test_legacy_visible_without_invented_history(self):
        rows=self.engine.movements([{'id':'old','number':'PR-1','positions':[{'product_name':'Old watch','quantity':1}]}])
        self.assertEqual(rows[0]['source_type'],'legacy')
        self.assertIsNone(rows[0]['stock_before'])

    def test_supply_routes_no_remote_writes(self):
        import os
        import app.web as web
        web.app.config['TESTING']=True
        with patch.dict(os.environ,{'CATALOG_DATABASE_PATH':str(self.db.path)}), patch.object(web,'load_receipts',return_value=[]), patch.object(web,'MoySkladClient',side_effect=AssertionError('MoySklad called')):
            client=web.app.test_client()
            response=client.post('/api/v1/receipts/supplies',json={'title':'New'})
            self.assertEqual(response.status_code,201,response.get_data(as_text=True))
            product_id=self.product()
            invalid=client.post('/api/v1/receipts/supplies',json={'title':'Invalid','items':[{'product_id':product_id,'quantity':0}]})
            self.assertEqual(invalid.status_code,422)
            self.assertEqual(self.stock(product_id),3)
            self.assertEqual(client.get('/app/receipts').status_code,200)
            self.assertEqual(client.post('/receipts/create').status_code,410)
            self.assertEqual(client.post('/api/v1/receipts').status_code,410)

    def test_cancellation_remains_exactly_once_and_visible(self):
        p=self.product()
        inventory=SalesInventory(self.db)
        inventory.create_sale({'id':'sale-21096','source':'Tictactoy','order_number':'21096'},p,1,100,user_name='Seller')
        self.assertEqual(self.stock(p),2)
        inventory.cancel_sale('sale-21096',reason='duplicate',user_name='Maxim')
        inventory.cancel_sale('sale-21096',reason='duplicate',user_name='Maxim')
        self.assertEqual(self.stock(p),3)
        rows=self.engine.movements()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['source_type'],'sale_cancellation')
        self.assertIn('21096', rows[0]['title'])
        self.assertEqual(self.engine.list(),[])

    def test_concurrent_sale_and_supply_preserve_both_movements(self):
        p=self.product()
        d=self.draft([(p,5)])
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(self.engine.post,d['id'])
            b=pool.submit(SalesInventory(self.db).create_sale,{'id':'sale-concurrent','source':'Tictactoy'},p,1,100)
            a.result(); b.result()
        self.assertEqual(self.stock(p),7)

    def test_excel_uses_same_draft_engine(self):
        import json
        from io import BytesIO
        from openpyxl import Workbook
        # Seed the existing normalized Bitrix catalog via its real importer.
        from test_excel_receipt_import import ExcelReceiptImportTest
        fixture=ExcelReceiptImportTest('test_preview_does_not_change_cards_stock_or_operations')
        fixture.setUp()
        try:
            fixture.seed_bitrix_rows(fixture.valid_file())
            with fixture.database.transaction() as c:
                for row in c.execute('SELECT * FROM catalog_products').fetchall():
                    payload={'external_product_id':row['external_product_id'],'name':row['name'],'external_sku':row['article'],'brand':row['brand'],'stock':47}
                    c.execute('UPDATE catalog_products SET normalized_payload_json = ? WHERE id = ?', (json.dumps(payload),row['id']))
            engine=SupplyEngine(fixture.database)
            with fixture.database.connect() as c:
                card=dict(c.execute('SELECT * FROM catalog_products LIMIT 1').fetchone())
            book=Workbook(); sheet=book.active
            sheet.append(['Название','Бренд','Количество','Bitrix ID'])
            sheet.append([card['name'],card['brand'] or 'Brand',6,card['external_product_id']])
            data=BytesIO();book.save(data)
            draft=engine.import_excel(data.getvalue(),'supply.xlsx','Importer')
            self.assertEqual(draft['status'],'draft')
            self.assertEqual(engine.movements(),[])
            product_id=draft['items'][0]['product_id']
            before=draft['items'][0]['stock_before']
            result=engine.post(draft['id'])
            self.assertEqual(result['items'][0]['stock_after'],before+6)
            self.assertEqual(len(engine.movements()),1)
        finally:
            fixture.tearDown()

    def test_missing_product_rolls_back_entire_supply(self):
        a,b=self.product(3,'a'),self.product(4,'b')
        d=self.draft([(a,5),(b,6)])
        with self.db.transaction() as c: c.execute('UPDATE catalog_excel_products SET active=0 WHERE id=?',(b,))
        with self.assertRaises(ReceiptInventoryError): self.engine.post(d['id'])
        self.assertEqual(self.stock(a),3)
        self.assertEqual(self.engine.get(d['id'])['status'],'draft')
        self.assertEqual(self.engine.movements(),[])

    def test_posted_snapshot_survives_card_edit(self):
        p=self.product()
        d=self.draft([(p,5)])
        posted=self.engine.post(d['id'])
        with self.db.transaction() as c: c.execute("UPDATE catalog_excel_products SET excel_name_raw='Renamed', stock=1 WHERE id=?",(p,))
        historical=self.engine.get(d['id'])
        self.assertEqual(historical['items'][0]['name'],posted['items'][0]['name'])
        self.assertEqual(historical['items'][0]['stock_after'],8)
        self.assertEqual(self.engine.movements()[0]['stock_after'],8)

    def test_empty_post_and_legacy_mutation_are_rejected(self):
        from app.services.receipt_inventory import ReceiptInventory
        d=self.draft()
        with self.assertRaises(ReceiptInventoryError): self.engine.post(d['id'])
        p=self.product()
        self.engine.update(d['id'],'x','',[{'product_id':p,'quantity':1}])
        self.engine.post(d['id'])
        legacy=ReceiptInventory(self.db)
        with self.assertRaises(ReceiptInventoryError): legacy.update_receipt(d['id'],{},[{'product_id':p,'quantity':2}])
        with self.assertRaises(ReceiptInventoryError): legacy.cancel_receipt(d['id'])
        self.assertEqual(self.stock(p),4)

    def test_create_idempotency_key(self):
        a=self.engine.create('One',key='request-1')
        b=self.engine.create('One',key='request-1')
        self.assertEqual(a['id'],b['id'])
        self.assertEqual(len(self.engine.list()),1)

    def test_ambiguous_identity_rejected_without_new_card(self):
        a=self.product(3,'one')
        b=self.product(4,'two')
        with self.db.transaction() as c:
            c.execute("UPDATE catalog_excel_products SET bitrix_external_product_id='ambiguous' WHERE id IN (?,?)",(a,b))
        with self.assertRaises(SupplyError): self.engine.resolve_bitrix({'external_product_id':'ambiguous','name':'Whatever','stock':99})
        self.assertEqual(self.stock(a),3)
        self.assertEqual(self.stock(b),4)

    def test_web_permissions_csrf_and_retired_entrypoints(self):
        import os
        import app.web as web
        from app.auth import require_csrf
        original=dict(web.app.config)
        web.app.config.update(TESTING=True,AUTH_TESTING=False)
        try:
            with patch.dict(os.environ,{'CATALOG_DATABASE_PATH':str(self.db.path)}),patch.object(web,'MoySkladClient',side_effect=AssertionError('remote write')):
                client=web.app.test_client()
                for path in ['/receipts/create','/receipts/update','/receipts/delete','/receipts/catalog/create','/receipts/import/preview','/products/receipts/drafts/old/post']:
                    self.assertEqual(client.post(path).status_code,410,path)
                with patch('app.supply_routes.require_csrf_when_authenticated',side_effect=require_csrf):
                    self.assertEqual(client.post('/api/v1/receipts/supplies',json={'title':'x'}).status_code,403)
                    with client.session_transaction() as session: session['_csrf_token']='test-csrf'
                    self.assertEqual(client.post('/api/v1/receipts/supplies',json={'title':'x'},headers={'X-CSRF-Token':'test-csrf'}).status_code,201)
                with patch('app.supply_routes.auth_is_enabled',return_value=True),patch('app.supply_routes.current_auth_user',return_value={'role':'viewer'}):
                    d=self.draft()
                    for method,path,body in [('post','/api/v1/receipts/supplies',{'title':'x'}),('patch','/api/v1/receipts/supplies/'+d['id'],{}),('post','/api/v1/receipts/supplies/'+d['id']+'/post',{}),('delete','/api/v1/receipts/supplies/'+d['id'],{})]:
                        self.assertEqual(getattr(client,method)(path,json=body).status_code,403)
                    self.assertEqual(client.get('/api/v1/receipts/movements').status_code,200)
        finally:
            web.app.config.clear();web.app.config.update(original)

    def test_add_new_and_existing_only_delta_and_preserve_history(self):
        a, b = self.product(7, 'a'), self.product(4, 'b')
        d = self.draft([(a, 3)])
        posted = self.engine.post(d['id'], 'Poster')
        original = self.engine.movements()
        result = self.engine.add_item(d['id'], b, 2, 'request-new', 'Максим')
        self.assertEqual((result['position_count'], result['total_quantity']), (2, 5))
        self.assertEqual((self.stock(a), self.stock(b)), (10, 6))
        result = self.engine.add_item(d['id'], a, 2, 'request-existing', 'Максим')
        self.assertEqual((result['position_count'], result['total_quantity']), (2, 7))
        self.assertEqual(result['items'][0]['quantity'], 5)
        self.assertEqual(result['items'][0]['stock_after'], 12)
        self.assertEqual(result['posted_at'], posted['posted_at'])
        self.assertEqual(result['posted_by'], 'Poster')
        self.engine.post(d['id'])
        self.assertEqual((self.stock(a), self.stock(b)), (12, 6))
        movements = self.engine.movements()
        self.assertIn(original[0], movements)
        self.assertEqual(len(movements), 3)
        self.assertEqual(len(self.engine.list()), 1)
        with self.db.connect() as c:
            movement = c.execute("SELECT * FROM catalog_stock_movements WHERE operation_kind='add' ORDER BY rowid LIMIT 1").fetchone()
            self.assertEqual((movement['source_id'], movement['product_id'], movement['quantity_delta'], movement['user_name']), (d['id'], b, 2, 'Максим'))
            self.assertTrue(movement['created_at'])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM erp_audit_events WHERE entity_type='receipt'").fetchone()[0], 2)

    def test_add_draft_then_post_once(self):
        p = self.product()
        d = self.draft()
        self.engine.add_item(d['id'], p, 2, 'draft-request')
        self.engine.add_item(d['id'], p, 2, 'draft-request')
        self.assertEqual(self.stock(p), 3)
        self.assertEqual(self.engine.get(d['id'])['total_quantity'], 2)
        self.engine.post(d['id'])
        self.engine.add_item(d['id'], p, 2, 'draft-request')
        self.assertEqual(self.stock(p), 5)

    def test_add_retry_and_key_conflict(self):
        p = self.product()
        d = self.draft([(p, 1)])
        self.engine.post(d['id'])
        for _ in range(3): self.engine.add_item(d['id'], p, 2, 'same-request')
        self.assertEqual(self.stock(p), 6)
        with self.assertRaises(SupplyError): self.engine.add_item(d['id'], p, 3, 'same-request')
        self.assertEqual(self.engine.get(d['id'])['total_quantity'], 3)

    def test_add_validation_and_deleted(self):
        p = self.product()
        d = self.draft([(p, 1)])
        self.engine.post(d['id'])
        for q in (0, -1, 1.5, '2', None, True, 2147483648):
            with self.subTest(q=q), self.assertRaises(SupplyError): self.engine.add_item(d['id'], p, q, 'invalid-request')
        for pid in (None, True, '1', 999999, 10**30):
            with self.subTest(pid=pid), self.assertRaises(SupplyError): self.engine.add_item(d['id'], pid, 2, 'invalid-request')
        for key in (None, '', 'x', 'x'*97):
            with self.subTest(key=key), self.assertRaises(SupplyError): self.engine.add_item(d['id'], p, 2, key)
        with self.assertRaises(SupplyError): self.engine.add_item('missing', p, 2, 'invalid-request')
        deleted = self.draft()
        self.engine.delete(deleted['id'])
        with self.assertRaises(SupplyError): self.engine.add_item(deleted['id'], p, 2, 'invalid-request')
        with self.db.transaction() as c: c.execute('UPDATE catalog_excel_products SET active=0 WHERE id=?', (p,))
        with self.assertRaises(SupplyError): self.engine.add_item(d['id'], p, 2, 'invalid-request')
        self.assertEqual(self.stock(p), 4)
        self.assertEqual(self.engine.get(d['id'])['total_quantity'], 1)

    def test_add_rollback_after_all_writes(self):
        p, other = self.product(), self.product(7, 'other')
        d = self.draft([(p, 1)])
        self.engine.post(d['id'])
        before = self.engine.get(d['id'])
        movements = self.engine.movements()
        def fail(_): raise RuntimeError('injected')
        for pid in (p, other):
            with self.assertRaises(RuntimeError): self.engine.add_item(d['id'], pid, 3, 'rollback-request', failure_hook=fail)
            self.assertEqual(self.engine.get(d['id']), before)
            self.assertEqual(self.engine.movements(), movements)
            self.assertEqual((self.stock(p), self.stock(other)), (4, 7))
        self.engine.add_item(d['id'], other, 3, 'rollback-request')
        self.assertEqual(self.stock(other), 10)

    def test_add_concurrent_distinct_and_identical(self):
        p = self.product()
        d = self.draft([(p, 1)])
        self.engine.post(d['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(self.engine.add_item, d['id'], p, 2, 'concurrent-a')
            b = pool.submit(self.engine.add_item, d['id'], p, 3, 'concurrent-b')
            a.result(); b.result()
        self.assertEqual(self.stock(p), 9)
        self.assertEqual(self.engine.get(d['id'])['items'][0]['quantity'], 6)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.engine.add_item(d['id'], p, 2, 'concurrent-same'), range(2)))
        self.assertEqual(self.stock(p), 11)
        self.assertTrue(all(r['total_quantity'] == 8 for r in results))

    def test_add_api_permissions_csrf_and_idempotency(self):
        import os
        import app.web as web
        from app.auth import require_csrf
        config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.addCleanup(lambda: (web.app.config.clear(), web.app.config.update(config)))
        p = self.product()
        d = self.draft([(p, 1)])
        self.engine.post(d['id'])
        path = '/api/v1/receipts/supplies/' + d['id'] + '/items'
        with patch.dict(os.environ, {'CATALOG_DATABASE_PATH': str(self.db.path)}), patch.object(web, 'MoySkladClient', side_effect=AssertionError('remote')), patch.object(web, '_bitrix_single_client', side_effect=AssertionError('remote')):
            client = web.app.test_client()
            with patch('app.supply_routes.auth_is_enabled', return_value=True), patch('app.supply_routes.current_auth_user', return_value={'role': 'viewer'}):
                self.assertEqual(client.post(path, json={'product_id':p,'quantity':2}).status_code, 403)
            with patch('app.supply_routes.require_csrf_when_authenticated', side_effect=require_csrf):
                self.assertEqual(client.post(path, json={'product_id':p,'quantity':2}).status_code, 403)
            for _ in range(2):
                response = client.post(path, json={'product_id':p,'quantity':2}, headers={'Idempotency-Key':'http-request'})
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(client.post(path, json=[]).status_code, 422)
        self.assertEqual(self.stock(p), 6)

    def test_add_local_card_without_bitrix_and_keep_purchase_price(self):
        p = self.product()
        d = self.draft([(p, 1)])
        with self.db.transaction() as c:
            c.execute('UPDATE catalog_excel_products SET bitrix_external_product_id=NULL, bitrix_catalog_product_id=NULL WHERE id=?', (p,))
            c.execute('UPDATE erp_receipt_items SET purchase_price=100 WHERE receipt_id=?', (d['id'],))
        self.engine.add_item(d['id'], p, 2, 'local-card-request')
        self.engine.post(d['id'])
        result = self.engine.add_item(d['id'], p, 3, 'local-card-posted')
        self.assertEqual(result['items'][0]['purchase_price'], 100)
        self.assertEqual(self.stock(p), 9)

    def test_add_physical_component_and_concurrent_sale(self):
        from app.services.product_bundles import ProductBundles
        from app.services.component_inventory import ComponentInventory, balance
        p, sku = self.product(99), self.product(99, 'sku')
        ProductBundles(self.db).configure(sku, [{'component_id':p, 'quantity':1}])
        ComponentInventory(self.db).confirm(p, 7, 'Tester')
        d = self.draft([(p, 1)])
        self.engine.post(d['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(self.engine.add_item, d['id'], p, 3, 'physical-request')
            b = pool.submit(SalesInventory(self.db).create_sale, {'id':'physical-sale','source':'Tictactoy'}, p, 2, 100)
            a.result(); b.result()
        self.assertEqual(self.stock(p), 99)
        with self.db.connect() as c: self.assertEqual(balance(c, p), 9)
        with self.assertRaises(ReceiptInventoryError): self.engine.add_item(d['id'], sku, 1, 'bundle-request')
