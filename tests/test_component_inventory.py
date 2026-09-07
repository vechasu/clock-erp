import unittest
from concurrent.futures import ThreadPoolExecutor
import test_sales_inventory as sales_tests
from app.services.component_inventory import ComponentInventory, balance
from app.services.product_bundles import ProductBundles
from app.services.sales_inventory import SalesInventoryError, InsufficientStockError
from app.services.receipt_inventory import ReceiptInventory


class ComponentInventoryTest(unittest.TestCase):
    setUp = sales_tests.SalesInventoryTest.setUp
    tearDown = sales_tests.SalesInventoryTest.tearDown
    create_product = sales_tests.SalesInventoryTest.create_product
    stock = sales_tests.SalesInventoryTest.stock
    payload = staticmethod(sales_tests.SalesInventoryTest.payload)

    def setup_components(self, initialize=True):
        sku = self.create_product(997, 'Commercial', 'BUNDLE')
        head = self.create_product(998, 'Head', 'HEAD')
        strap = self.create_product(999, 'Strap', 'STRAP')
        bundles = ProductBundles(self.database)
        parts = [{'component_id': head['id'], 'quantity': 1}, {'component_id': strap['id'], 'quantity': 1}]
        bundles.configure(sku['id'], parts)
        if initialize:
            ComponentInventory(self.database).confirm(head['id'], 3, 'Tester')
            ComponentInventory(self.database).confirm(strap['id'], 5, 'Tester')
        return bundles, sku, head, strap, parts

    def physical(self, product):
        with self.database.connect() as c:
            return balance(c, product['id'])

    def test_uninitialized_never_uses_legacy(self):
        b,sku,h,s,_ = self.setup_components(False)
        self.assertEqual(b.get(sku['id'])['available_to_assemble'], 0)
        self.assertFalse(b.get(sku['id'])['physical_inventory_initialized'])
        from app.services.shared_catalog import SharedCatalog
        available = SharedCatalog(self.database).list_products(in_stock=True, include_assemblable=True)
        self.assertFalse({int(sku['id']),int(h['id']),int(s['id'])} & {int(row['id']) for row in available})
        with self.assertRaises(SalesInventoryError):
            self.inventory.create_sale(self.payload(sku), sku['id'], 1, 10)
        self.assertEqual(self.stock(h['id']), 998)
        with self.database.connect() as c:
            self.assertIsNone(c.execute('SELECT physical_stock FROM erp_component_inventory WHERE product_id=?', (h['id'],)).fetchone()[0])

    def test_explicit_initialization_and_validation(self):
        b,sku,h,s,_=self.setup_components(False)
        for invalid in (-1,0.5,'NaN','Infinity',True,None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ComponentInventory(self.database).confirm(h['id'], invalid)
        ComponentInventory(self.database).confirm(h['id'],3)
        ComponentInventory(self.database).confirm(s['id'],5)
        self.assertEqual(b.get(sku['id'])['available_to_assemble'],3)
        self.assertEqual((self.stock(h['id']),self.stock(s['id'])),(998,999))

    def test_bundle_sale_cancel_and_no_double_deduction(self):
        b,sku,h,s,_=self.setup_components()
        sale=self.inventory.create_sale(self.payload(sku),sku['id'],1,10)
        self.assertEqual((self.physical(h),self.physical(s)),(2,4))
        self.assertEqual((self.stock(sku['id']),self.stock(h['id']),self.stock(s['id'])),(997,998,999))
        self.inventory.cancel_sale(sale['id']);self.inventory.cancel_sale(sale['id'])
        self.assertEqual((self.physical(h),self.physical(s)),(3,5))

    def test_partial_return_remains_physical(self):
        b,sku,h,s,_=self.setup_components()
        sale=self.inventory.create_sale(self.payload(sku),sku['id'],2,10)
        self.inventory.return_sale(sale['id'],1)
        self.assertEqual(self.physical(h),2)
        self.inventory.cancel_sale(sale['id'])
        self.assertEqual(self.physical(h),3)
        self.assertEqual(self.stock(h['id']),998)

    def test_historical_ordinary_sale_then_bundle_and_late_cancel(self):
        sku=self.create_product(999,'Old commercial','OLD')
        old=self.inventory.create_sale(self.payload(sku),sku['id'],1,10)
        h=self.create_product(998,'Head','HEAD')
        b=ProductBundles(self.database);b.configure(sku['id'],[{'component_id':h['id'],'quantity':1}])
        ComponentInventory(self.database).confirm(h['id'],3)
        self.inventory.cancel_sale(old['id'])
        self.assertEqual(self.stock(sku['id']),999)
        self.assertEqual(self.physical(h),3)
        self.inventory.create_sale(self.payload(sku,'new'),sku['id'],1,10)
        self.assertEqual(self.physical(h),2)
        self.assertEqual(self.stock(sku['id']),999)
        with self.database.connect() as c:
            self.assertEqual(c.execute('SELECT legacy_stock FROM erp_bundle_transitions WHERE product_id=?',(sku['id'],)).fetchone()[0],998)

    def test_old_component_sale_returns_legacy_after_component_setup(self):
        h=self.create_product(998,'Head','HEAD');sku=self.create_product(997,'SKU','SKU')
        old=self.inventory.create_sale(self.payload(h),h['id'],1,10)
        ProductBundles(self.database).configure(sku['id'],[{'component_id':h['id'],'quantity':1}])
        ComponentInventory(self.database).confirm(h['id'],3)
        self.inventory.return_sale(old['id'],1)
        self.assertEqual(self.stock(h['id']),998);self.assertEqual(self.physical(h),3)

    def test_component_direct_sale_uses_same_physical_balance(self):
        b,sku,h,s,_=self.setup_components()
        sale=self.inventory.create_sale(self.payload(h),h['id'],1,10)
        self.assertEqual(self.physical(h),2);self.assertEqual(b.get(sku['id'])['available_to_assemble'],2)
        self.inventory.return_sale(sale['id'],1)
        self.assertEqual(self.physical(h),3);self.assertEqual(self.stock(h['id']),998)

    def test_shared_component_concurrency_never_negative(self):
        b,sku,h,s,parts=self.setup_components()
        ComponentInventory(self.database).confirm(h['id'],1)
        other=self.create_product(999,'Other','OTHER');b.configure(other['id'],parts)
        def sell(product):
            try:self.inventory.create_sale(self.payload(product,str(product['id'])),product['id'],1,10);return True
            except InsufficientStockError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(sell,[sku,other])),1)
        self.assertEqual(self.physical(h),0)
        self.assertEqual(b.get(other['id'])['available_to_assemble'],0)
        self.assertEqual(self.stock(h['id']),998)

    def test_receipt_post_edit_cancel_physical(self):
        b,sku,h,s,_=self.setup_components()
        receipts=ReceiptInventory(self.database)
        receipts.create_receipt({'id':'r'},[{'product_id':h['id'],'quantity':2}])
        self.assertEqual(self.physical(h),5)
        receipts.update_receipt('r',{},[{'product_id':h['id'],'quantity':3}])
        self.assertEqual(self.physical(h),6)
        receipts.cancel_receipt('r');receipts.cancel_receipt('r')
        self.assertEqual(self.physical(h),3);self.assertEqual(self.stock(h['id']),998)

    def test_historical_receipt_cancel_stays_legacy(self):
        h=self.create_product(998,'Head','HEAD');sku=self.create_product(997,'SKU','SKU')
        receipts=ReceiptInventory(self.database);receipts.create_receipt({'id':'old'},[{'product_id':h['id'],'quantity':2}])
        ProductBundles(self.database).configure(sku['id'],[{'component_id':h['id'],'quantity':1}]);ComponentInventory(self.database).confirm(h['id'],3)
        receipts.cancel_receipt('old')
        self.assertEqual(self.stock(h['id']),998);self.assertEqual(self.physical(h),3)

    def test_bitrix_stock_sync_does_not_touch_physical(self):
        from app.services.bitrix_stock_sync import BitrixStockSync
        b,sku,h,s,_=self.setup_components()
        with self.database.transaction() as c:
            c.execute("UPDATE catalog_excel_products SET bitrix_external_product_id='700' WHERE id=?",(h['id'],))
        result=BitrixStockSync(self.database).synchronize([{'external_product_id':'700','name':'Head','brand':'Brand','stock':999,'stock_source_field':'CCatalogProduct.QUANTITY'}],apply=True)
        self.assertEqual(result['updated'],1)
        self.assertEqual(self.stock(h['id']),999);self.assertEqual(self.physical(h),3)
        self.assertEqual(b.get(sku['id'])['available_to_assemble'],3)

    def test_ordinary_sale_and_receipt_unchanged(self):
        p=self.create_product(7)
        sale=self.inventory.create_sale(self.payload(p),p['id'],1,10)
        self.assertEqual(self.stock(p['id']),6)
        ReceiptInventory(self.database).create_receipt({'id':'r'},[{'product_id':p['id'],'quantity':2}])
        self.assertEqual(self.stock(p['id']),8)
        self.inventory.cancel_sale(sale['id']);self.assertEqual(self.stock(p['id']),9)
        with self.database.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM erp_component_inventory').fetchone()[0],0)

    def test_brand_inventory_initializes_physical_and_preserves_legacy(self):
        from app.services.brand_inventory import BrandInventory
        b,sku,h,s,_=self.setup_components(False)
        counter=BrandInventory(self.database)
        with self.database.connect() as c:brand=c.execute('SELECT brand_id FROM catalog_excel_products WHERE id=?',(h['id'],)).fetchone()[0]
        session=counter.start(brand,'Tester')[0]
        items=counter.list_items(session['id'])
        self.assertNotIn(int(sku['id']),[int(i['product_id']) for i in items])
        for item in items:
            self.assertEqual(item['snapshot_stock'],0)
            counter.confirm(session['id'],item['id'],3 if int(item['product_id'])==int(h['id']) else 5,'Tester','count-'+item['id'])
        counter.complete(session['id'],'Tester',confirmation=True)
        self.assertEqual(b.get(sku['id'])['available_to_assemble'],3)
        self.assertEqual((self.stock(h['id']),self.stock(s['id'])),(998,999))

    def test_page_search_and_physical_confirmation_fallback_hidden(self):
        import os
        from unittest import mock
        from app import web
        b,sku,h,s,_=self.setup_components(False)
        with mock.patch.dict(os.environ,{'CATALOG_DATABASE_PATH':str(self.database_path)}),mock.patch.dict(web.app.config,{'TESTING':True}):
            client=web.app.test_client();url='/app/products/{}/bundle'.format(sku['id'])
            page=client.get(url)
            self.assertEqual(page.status_code,200)
            self.assertIn('Физический остаток не подтверждён'.encode(),page.data)
            self.assertIn('<details><summary>Не нашли компонент?'.encode(),page.data)
            for query in ('Head','HEAD'):
                result=client.get('/api/v1/products?include_component_inventory=1&q='+query).get_json()['data']
                self.assertIn(int(h['id']),[int(r['id']) for r in result])
            response=client.post(url,data={'action':'confirm_physical','physical_product_id':h['id'],'physical_stock':'3'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(self.physical(h),3);self.assertEqual(self.stock(h['id']),998)
            found=client.get('/api/v1/products?include_component_inventory=1&q=HEAD').get_json()['data']
            component=next(row for row in found if int(row['id'])==int(h['id']))
            self.assertTrue(component['physical_inventory_initialized'])
            self.assertEqual(component['physical_stock'],3)
            self.assertEqual(component['stock'],998)
            self.assertIn('Физический остаток ERP: 3'.encode(),client.get(url).data)

    def test_legacy_bundle_sale_preserves_original_inventory_domain(self):
        b,sku,h,s,_=self.setup_components()
        # A pre-upgrade sale has component snapshots, but no new physical-document marker.
        sale=self.inventory.create_sale(self.payload(sku),sku['id'],1,10)
        with self.database.transaction() as c:
            c.execute("DELETE FROM erp_physical_documents WHERE document_type='sale' AND document_id=?",(sale['id'],))
            c.execute('UPDATE erp_component_inventory SET physical_stock=3 WHERE product_id=?',(h['id'],))
            c.execute('UPDATE erp_component_inventory SET physical_stock=5 WHERE product_id=?',(s['id'],))
            c.execute('UPDATE catalog_excel_products SET stock=stock-1 WHERE id IN (?,?)',(h['id'],s['id']))
        self.inventory.cancel_sale(sale['id'])
        self.assertEqual((self.stock(h['id']),self.stock(s['id'])),(998,999))
        self.assertEqual((self.physical(h),self.physical(s)),(3,5))

    def test_upgrade_existing_bundle_never_copies_legacy_stock(self):
        import sqlite3
        from app.bundle_migration import BUNDLE_SQL
        from app.schema_migrations import apply_migrations, COMPONENT_MIGRATION_ID
        sku=self.create_product(0,'Old bundle','OLD');h=self.create_product(998,'Head','HEAD')
        with sqlite3.connect(str(self.database.path)) as c:
            c.execute("INSERT INTO erp_product_bundles VALUES (?,?)",(sku['id'],'2026-09-01'))
            c.execute("INSERT INTO erp_bundle_components VALUES (?,?,1)",(sku['id'],h['id']))
            before=c.execute('SELECT * FROM catalog_excel_products ORDER BY id').fetchall()
            for table in ('erp_physical_documents','erp_component_inventory_events','erp_bundle_transitions','erp_component_inventory'):
                c.execute('DROP TABLE '+table)
            c.execute(BUNDLE_SQL[1])
            c.execute('DELETE FROM erp_migration_ledger WHERE migration_id=?',(COMPONENT_MIGRATION_ID,))
        apply_migrations(self.database.path,app_commit='upgrade-physical-test')
        with self.database.connect() as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM catalog_excel_products ORDER BY id')],before)
            self.assertIsNone(c.execute('SELECT physical_stock FROM erp_component_inventory WHERE product_id=?',(h['id'],)).fetchone()[0])
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        self.assertEqual(ProductBundles(self.database).get(sku['id'])['available_to_assemble'],0)
