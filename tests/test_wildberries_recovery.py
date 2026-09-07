import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.domain_schema_migrations import apply_domain_migrations
from app.schema_migrations import apply_migrations
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.sales_inventory import SalesInventory
from app.services.wildberries_orders import synchronize_wildberries_orders
from app.services.wildberries_recovery import WildberriesRecovery, KNOWN_SUPPLY, readonly
from app.clients.wildberries_orders import WildberriesOrdersReadOnlyClient, WildberriesReadOnlyError


class FakeWB:
    def __init__(self):
        self.ids = list(range(101,109))
        self.rows = [dict(id=value, article='WATCH', nmId=77, skus=['4601'], createdAt='2026-09-06T10:00:00Z',
                          price=123400, convertedPrice=123400, supplyId=KNOWN_SUPPLY) for value in self.ids]
        self.statuses = {str(value):dict(id=value, supplierStatus='complete', wbStatus='waiting') for value in self.ids}

    def get_new_orders(self):
        return []

    def get_supply(self, value):
        return dict(id=value, done=True, createdAt='2026-09-07T10:37:05Z')

    def get_supply_order_ids(self, value):
        return self.ids[:]

    def get_orders(self, start, end):
        return copy.deepcopy(self.rows)

    def get_order_statuses(self, ids):
        return {str(value): self.statuses[str(value)] for value in ids if str(value) in self.statuses}

    def get_supplies(self, **kwargs):
        return dict(next=0, supplies=[self.get_supply(KNOWN_SUPPLY)])


class WildberriesRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog_db = CatalogDatabase(self.root/'catalog.db')
        apply_migrations(self.catalog_db.path)
        self.orders_db = self.root/'orders.db'
        apply_domain_migrations(self.orders_db, 'orders', 'test')
        self.store = OrdersSnapshotStore(self.orders_db)
        with self.catalog_db.transaction() as connection:
            connection.execute("INSERT INTO catalog_excel_batches (id,file_sha256,source_filename,row_count,total_stock,positive_rows,zero_rows,status,created_at,applied_at) VALUES ('wb','wb','test.xlsx',0,0,0,0,'active','2026-09-07','2026-09-07')")
        self.catalog = ExcelProductCatalog(self.catalog_db)
        self.product = self.catalog.create_product('Часы тест', article='WATCH', brand='Brand', category='Часы', stock=3)
        self.client = FakeWB()
        self.recovery = WildberriesRecovery(self.client, self.orders_db, self.catalog_db.path, now=1788796800)

    def effects(self):
        with self.catalog_db.connect() as connection:
            return (connection.execute('SELECT COUNT(*) FROM erp_sales').fetchone()[0],
                    connection.execute('SELECT COUNT(*) FROM catalog_stock_movements').fetchone()[0],
                    tuple(tuple(row) for row in connection.execute('SELECT id,stock FROM catalog_excel_products ORDER BY id')))

    def preview(self):
        return self.recovery.preview_supply(KNOWN_SUPPLY)

    def test_missed_orders_in_closed_supply_dry_run_does_not_write(self):
        before = {path.name:path.read_bytes() for path in (self.orders_db, self.catalog_db.path)}
        report = self.preview()
        self.assertEqual(report['wb_count'], 8)
        self.assertEqual(report['counts']['READY'], 8)
        self.assertEqual(report['rows'][0]['supplier_status'], 'complete')
        self.assertTrue(report['can_import'])
        for path in (self.orders_db, self.catalog_db.path):
            self.assertEqual(path.read_bytes(), before[path.name])

    def test_import_uses_snapshot_store_preserves_dates_and_stock(self):
        effects = self.effects()
        with mock.patch.object(self.store, 'upsert_wildberries', wraps=self.store.upsert_wildberries) as upsert:
            result = self.recovery.import_report(self.preview(), self.store)
        self.assertEqual(result['imported'], 8)
        self.assertEqual(upsert.call_count, 8)
        order = self.store.get('wb:101')
        self.assertTrue(order['recovered_from_wb'])
        self.assertEqual(order['wb_supply_id'], KNOWN_SUPPLY)
        self.assertEqual(order['created_at'], '2026-09-06T10:00:00Z')
        self.assertEqual(int(order['products'][0]['product_id']), int(self.product['id']))
        self.assertEqual(self.effects(), effects)
        with self.catalog_db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM erp_order_product_mappings WHERE product_id=?', (self.product['id'],)).fetchone()[0], 8)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM erp_audit_events WHERE entity_type='order'").fetchone()[0], 8)

    def test_retries_and_concurrency_do_not_duplicate_orders_or_journal(self):
        report = self.preview()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.recovery.import_report(report, self.store), range(2)))
        self.assertEqual(sum(value['imported'] for value in results), 8)
        for _ in range(3):
            self.assertEqual(self.recovery.import_report(report, self.store)['imported'], 0)
        self.assertEqual(self.store.count(), 8)
        with self.catalog_db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM erp_audit_events WHERE entity_type='order'").fetchone()[0], 8)

    def test_already_sold_is_never_recreated(self):
        SalesInventory(self.catalog_db).create_sale_batch(
            {'source':'wildberries','external_order_id':'101'},
            [{'product_id':self.product['id'],'quantity':1,'unit_price':1234}], enforce_external_unique=True)
        effects = self.effects()
        report = self.preview()
        self.assertEqual(report['counts']['ALREADY_SOLD'], 1)
        result = self.recovery.import_report(report, self.store)
        self.assertEqual(result['imported'], 7)
        self.assertEqual(self.effects(), effects)

    def test_unmatched_ambiguous_and_no_stock_are_preserved_without_guessing(self):
        self.client.rows[0]['article'] = 'UNKNOWN'
        other = self.catalog.create_product('Другие часы', article='OTHER', brand='Brand', category='Часы', stock=2)
        with self.catalog_db.transaction() as connection:
            connection.execute("UPDATE catalog_excel_products SET excel_article='DUP' WHERE id=?",(other['id'],))
            connection.execute("UPDATE catalog_excel_products SET stock=0 WHERE id=?",(self.product['id'],))
        second = self.catalog.create_product('Дубль', article='SECOND', brand='Brand', category='Часы', stock=1)
        with self.catalog_db.transaction() as connection:
            connection.execute("UPDATE catalog_excel_products SET excel_article='DUP' WHERE id=?",(second['id'],))
        self.client.rows[1]['article'] = 'DUP'
        report = self.preview()
        self.assertEqual(report['counts']['PRODUCT_NOT_FOUND'], 1)
        self.assertEqual(report['counts']['AMBIGUOUS_PRODUCT'], 1)
        self.assertEqual(report['counts']['NO_STOCK'], 6)
        self.assertEqual(self.recovery.import_report(report, self.store)['imported'], 8)
        self.assertTrue(self.store.get('wb:101')['requires_matching'])
        self.assertTrue(self.store.get('wb:102')['requires_matching'])
        self.assertIsNone(self.store.get('wb:101')['products'][0]['product_id'])
        self.assertIsNone(self.store.get('wb:102')['products'][0]['product_id'])
        with self.catalog_db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM erp_order_product_mappings').fetchone()[0], 6)

    def test_imported_orders_visible_in_list_and_exact_search(self):
        self.recovery.import_report(self.preview(), self.store)
        with mock.patch.dict(web.app.config, TESTING=True, AUTH_TESTING=False, ORDERS_SNAPSHOT_TESTING=True), \
                mock.patch.object(web, 'OrdersSnapshotStore', return_value=self.store), \
                mock.patch.object(web, 'CatalogDatabase', return_value=self.catalog_db), \
                mock.patch.object(web, 'get_orders', return_value=[]), \
                mock.patch.object(self.store, 'ensure'), \
                mock.patch.object(web, 'schedule_order_item_unit_backfill'):
            client = web.app.test_client()
            response = client.get('/api/orders?source=wildberries')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['total_filtered'], 8)
            for order_id in self.client.ids:
                self.assertIn(str(order_id), response.json['html'])
            search = client.get('/api/orders?q=101&source=wildberries&status=N&period=today')
            self.assertEqual(search.status_code, 200)
            self.assertEqual(search.json['total_filtered'], 1)
            self.assertIn('101', search.json['html'])
            self.assertEqual(client.get('/app/orders?source=wildberries').status_code, 200)

    def test_partial_status_failure_imports_only_known_orders(self):
        del self.client.statuses['101']
        report = self.preview()
        self.assertEqual(report['counts']['API_ERROR'], 1)
        outcome = self.recovery.import_report(report, self.store)
        self.assertEqual(outcome['imported'], 7)
        self.assertEqual(len(outcome['failed']), 1)

    def test_wrong_supply_count_stops_all_imports(self):
        self.client.ids.pop()
        report = self.preview()
        self.assertFalse(report['can_import'])
        self.assertIn('STOP', report['errors'][0])
        with self.assertRaises(ValueError):
            self.recovery.import_report(report, self.store)
        self.assertEqual(self.store.count(), 0)
        with self.assertRaises(ValueError):
            self.recovery.reconcile(self.store)
        self.assertEqual(self.store.count(), 0)

    def test_journal_failure_rolls_back_only_failed_order(self):
        from app.services.audit_journal import AuditJournal
        original = AuditJournal.record
        def record(journal, *args, **kwargs):
            if args[1] == 'wb:103':
                raise RuntimeError('injected')
            return original(journal, *args, **kwargs)
        effects = self.effects()
        with mock.patch.object(AuditJournal, 'record', record):
            outcome = self.recovery.import_report(self.preview(), self.store)
        self.assertEqual(outcome['imported'], 7)
        self.assertIsNone(self.store.get('wb:103'))
        self.assertEqual(self.effects(), effects)

    def test_reconciliation_finds_missed_orders_without_sales(self):
        before = self.effects()
        result = self.recovery.reconcile(self.store)
        self.assertEqual(result['recovered'], 8)
        self.assertEqual(result['supplies'][0]['missing'], 8)
        self.assertEqual(self.recovery.reconcile(self.store)['recovered'], 0)
        self.assertEqual(self.effects(), before)

    def test_ordinary_sync_preserves_recovery_marker_and_other_sources(self):
        self.recovery.import_report(self.preview(), self.store)
        self.client.get_new_orders = lambda: [self.client.rows[0]]
        result = synchronize_wildberries_orders(self.client, self.store)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['added'], 0)
        self.assertTrue(self.store.get('wb:101')['recovered_from_wb'])

    def test_preview_confirmation_is_required_and_stale_data_rejected(self):
        with mock.patch.dict(web.app.config, TESTING=True, AUTH_TESTING=False), mock.patch.object(web,'wb_recovery_service',return_value=self.recovery), mock.patch.object(web,'OrdersSnapshotStore',return_value=self.store):
            client = web.app.test_client()
            self.assertEqual(client.post('/api/orders/wildberries/recovery/import', json={}).status_code,400)
            result = client.post('/api/orders/wildberries/recovery/preview',json={'supply_id':KNOWN_SUPPLY})
            self.assertEqual(result.status_code,200)
            self.assertEqual(self.store.count(),0)
            confirmation = result.json['report']['confirmation']
            self.client.ids.pop()
            self.assertEqual(client.post('/api/orders/wildberries/recovery/import',json={'confirmation':confirmation}).status_code,409)
            self.assertEqual(self.store.count(),0)


    def test_other_order_sources_remain_unchanged(self):
        self.store.upsert_bitrix([{'id':'900','number':'900','status':'A','products':[]}], loaded_at=1)
        before = self.store.get('900')
        self.recovery.import_report(self.preview(), self.store)
        self.assertEqual(self.store.get('900'), before)

    def test_confirmed_http_import_and_retry_do_not_create_sales(self):
        before = self.effects()
        with mock.patch.dict(web.app.config, TESTING=True, AUTH_TESTING=False), mock.patch.object(web,'wb_recovery_service',return_value=self.recovery), mock.patch.object(web,'OrdersSnapshotStore',return_value=self.store):
            client = web.app.test_client()
            preview = client.post('/api/orders/wildberries/recovery/preview',json={'supply_id':KNOWN_SUPPLY}).json
            token = preview['report']['confirmation']
            result = client.post('/api/orders/wildberries/recovery/import',json={'confirmation':token})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json['result']['imported'],8)
            self.assertEqual(client.post('/api/orders/wildberries/recovery/import',json={'confirmation':token}).status_code,409)
        self.assertEqual(self.store.count(),8)
        self.assertEqual(self.effects(),before)


class RecoveryTransportTest(unittest.TestCase):
    def test_only_status_post_is_allowed_and_is_read_only(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {'orders':[{'id':101,'supplierStatus':'confirm'}]}
        session = mock.Mock()
        session.post.return_value=response
        client = WildberriesOrdersReadOnlyClient('test',session=session)
        self.assertEqual(client.get_order_statuses([101])['101']['supplierStatus'],'confirm')
        self.assertEqual(session.post.call_args.kwargs['json'],{'orders':[101]})
        with self.assertRaises(WildberriesReadOnlyError):
            client.request_json('POST','marketplace','/api/v3/orders/status')
        with self.assertRaises(WildberriesReadOnlyError):
            client._read_json('PATCH','marketplace','/api/v3/orders/101/cancel')
        self.assertEqual(session.post.call_count,1)

    def test_pagination_and_nonadvancing_cursor(self):
        client = WildberriesOrdersReadOnlyClient('test')
        with mock.patch.object(client,'request_json',side_effect=[{'next':5,'orders':[{'id':1}]},{'next':0,'orders':[{'id':2}]}]) as request:
            self.assertEqual(len(client.get_orders(1,86401)),2)
            self.assertEqual(request.call_args.kwargs['params']['next'],5)
        with mock.patch.object(client,'request_json',return_value={'next':5,'orders':[{'id':1}]}):
            with self.assertRaises(WildberriesReadOnlyError):
                client.get_orders(1,86401)
