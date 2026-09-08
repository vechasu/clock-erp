import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import test_wildberries_recovery as fixtures
from app.clients.wildberries_orders import WildberriesReadOnlyError
from app.services.wildberries_orders import normalize_wildberries_order
from app.services.wildberries_recovery import diagnostics
from app.services.wildberries_sync import run_sync, WBSyncLock, SyncBusy


class AutoSyncTest(unittest.TestCase):
    setUp = fixtures.WildberriesRecoveryTest.setUp
    effects = fixtures.WildberriesRecoveryTest.effects

    def seed(self):
        order = normalize_wildberries_order(dict(self.client.rows[0], supplierStatus='complete', wbStatus='waiting'))
        order.update(sale_id='protected', comment='local', history=[{'local':True}])
        order['products'][0]['product_id'] = self.product['id']
        self.store.upsert_wildberries([order])
        return self.store.get('wb:101')

    def test_new_once_status_update_idempotence_and_local_fields(self):
        before = self.seed()
        effects = self.effects()
        self.client.get_new_orders = lambda: [dict(self.client.rows[0], supplierStatus='new', wbStatus='waiting')]
        self.client.statuses['101']['wbStatus'] = 'sold'
        result = run_sync(self.client, self.store, self.catalog_db.path)
        self.assertEqual(result['updated'], 1)
        after = self.store.get('wb:101')
        for key in ('sale_id','comment','history','products'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after['wb_status'], 'sold')
        self.assertEqual(len(after['wb_status_history']), 1)
        self.assertEqual(run_sync(self.client, self.store, self.catalog_db.path)['updated'], 0)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.get('wb:101'), after)
        self.assertEqual(self.effects(), effects)

    def test_new_order_created_once_without_sale(self):
        self.client.get_new_orders = lambda: copy.deepcopy(self.client.rows[:1])
        effects = self.effects()
        self.assertEqual(run_sync(self.client,self.store,self.catalog_db.path)['added'],1)
        self.assertEqual(run_sync(self.client,self.store,self.catalog_db.path)['added'],0)
        self.assertEqual(self.effects(),effects)

    def test_terminal_skipped_fast_rechecked_full(self):
        self.seed()
        self.store.update_wildberries_status('101',dict(supplierStatus='complete',wbStatus='sold'))
        with mock.patch.object(self.client,'get_order_statuses',wraps=self.client.get_order_statuses) as reader:
            run_sync(self.client,self.store,self.catalog_db.path)
            reader.assert_not_called()
            run_sync(self.client,self.store,self.catalog_db.path,'full')
            self.assertTrue(reader.called)
        self.assertEqual(self.store.get('wb:101')['wb_status'],'waiting')

    def test_recovery_updates_existing_without_clobber(self):
        before=self.seed()
        self.client.statuses['101']['wbStatus']='canceled_by_client'
        report=self.recovery.preview_supply('WB-GI-275015200')
        self.assertEqual(self.recovery.import_report(report,self.store)['updated'],1)
        self.assertEqual(self.store.get('wb:101')['products'],before['products'])
        self.assertEqual(self.recovery.import_report(report,self.store)['updated'],0)

    def test_existing_status_updates_without_recovery_order_details(self):
        self.seed()
        self.client.rows = []
        self.client.statuses['101']['wbStatus'] = 'sold'
        result = self.recovery.import_report(self.recovery.preview_supply('WB-GI-275015200'), self.store)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(self.store.get('wb:101')['wb_status'], 'sold')

    def test_partial_failure_preserves_last_success(self):
        self.seed()
        run_sync(self.client,self.store,self.catalog_db.path)
        before=diagnostics(self.store.path)['last_success_at']
        with mock.patch.object(self.client,'get_order_statuses',side_effect=WildberriesReadOnlyError('test','WB_TIMEOUT')):
            result=run_sync(self.client,self.store,self.catalog_db.path)
        self.assertEqual(result['sync_status'],'partial')
        self.assertEqual(diagnostics(self.store.path)['last_success_at'],before)
        self.assertEqual(self.store.get('wb:101')['wb_status'],'waiting')

    def test_total_api_failure_controlled(self):
        with mock.patch.object(self.client,'get_new_orders',side_effect=WildberriesReadOnlyError('test','WB_TIMEOUT')):
            result=run_sync(self.client,self.store,self.catalog_db.path)
        self.assertEqual(result['sync_status'],'error')
        self.assertEqual(diagnostics(self.store.path)['result'],'error')

    def test_missing_status_is_partial(self):
        self.seed()
        self.client.statuses={}
        self.assertEqual(run_sync(self.client,self.store,self.catalog_db.path)['sync_status'],'partial')

    def test_lock_prevents_second_run_and_releases(self):
        lock=WBSyncLock(self.store.path)
        self.assertTrue(lock.acquire())
        try:
            with mock.patch.object(self.client,'get_new_orders') as reader:
                with self.assertRaises(SyncBusy):
                    run_sync(self.client,self.store,self.catalog_db.path)
                reader.assert_not_called()
        finally:
            lock.release()
        self.assertEqual(run_sync(self.client,self.store,self.catalog_db.path)['sync_status'],'success')

    def test_concurrent_read_and_sync(self):
        self.seed()
        self.client.statuses['101']['wbStatus']='sold'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda n: run_sync(self.client,self.store,self.catalog_db.path) if n else [self.store.get('wb:101') for _ in range(30)], range(2)))
        self.assertEqual(results[1]['sync_status'],'success')
