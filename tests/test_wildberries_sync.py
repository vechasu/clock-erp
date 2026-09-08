import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app.catalog_db import CatalogDatabase
from app.domain_schema_migrations import apply_domain_migrations
from app.schema_migrations import apply_migrations
from app.clients.wildberries_orders import WildberriesReadOnlyError
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.wildberries_orders import normalize_wildberries_order
from app.services.wildberries_sync import SyncLock, run_sync, status_ids


class FakeWB:
    def __init__(self):
        self.rows = [dict(id=101, article='TEST', createdAt='2026-09-06T10:00:00Z', price=10000)]
        self.statuses = {'101':dict(id=101, supplierStatus='complete', wbStatus='waiting')}
        self.request_audit = []
        self.polled = []

    def get_new_orders(self):
        self.request_audit.append('new')
        return copy.deepcopy(self.rows)

    def get_order_statuses(self, ids):
        self.polled.extend(ids)
        self.request_audit.append('status')
        return {i: copy.deepcopy(self.statuses[i]) for i in ids if i in self.statuses}

    def get_orders(self, *args):
        self.request_audit.append('orders')
        return copy.deepcopy(self.rows)

    def get_supplies(self, **kwargs):
        self.request_audit.append('supplies')
        return {'next':0, 'supplies':[]}


class WildberriesSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.orders = self.root/'orders.db'
        self.catalog = self.root/'catalog.db'
        apply_domain_migrations(self.orders, 'orders', 'test')
        apply_migrations(self.catalog)
        self.store = OrdersSnapshotStore(self.orders)
        self.client = FakeWB()
        self.env = mock.patch.dict(os.environ, {'WB_SYNC_LOCK_PATH':str(self.root/'sync.lock')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def sync(self, mode='fast'):
        return run_sync(self.client, self.store, self.catalog, mode)

    def effects(self):
        c = sqlite3.connect(str(self.catalog))
        try:
            return [c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in
                    ('erp_sales', 'catalog_stock_movements', 'erp_order_product_mappings')]
        finally:
            c.close()

    def test_new_once_changed_status_only_history_local_fields_and_no_sale(self):
        effects = self.effects()
        self.assertEqual(self.sync()['added'], 1)
        order = self.store.get('wb:101')
        order.update(sale_id='existing-sale', comment='local comment', history=[{'local':True}])
        order['products'][0]['product_id'] = 'manually-mapped'
        with self.store.connection() as c:
            c.execute('UPDATE orders_snapshot SET payload_json=? WHERE order_id=?', (json.dumps(order),'wb:101'))
        self.client.statuses['101']['wbStatus'] = 'ready_for_pickup'
        result = self.sync()
        self.assertEqual(result['statuses_updated'], 1)
        self.assertEqual(result['added'], 0)
        updated = self.store.get('wb:101')
        for key in ('sale_id','comment','history','products'):
            self.assertEqual(updated[key], order[key])
        self.assertEqual(updated['wb_status'], 'ready_for_pickup')
        count = len(updated['wb_status_history'])
        self.assertEqual(self.sync()['statuses_updated'], 0)
        self.assertEqual(len(self.store.get('wb:101')['wb_status_history']), count)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.effects(), effects)

    def test_terminal_excluded_fast_but_full_rechecks_late_change(self):
        self.sync()
        self.client.statuses['101']['wbStatus'] = 'sold'
        self.sync()
        self.client.rows = []
        self.client.polled = []
        self.sync()
        self.assertEqual(self.client.polled, [])
        self.client.statuses['101']['wbStatus'] = 'canceled_by_client'
        self.sync('full')
        self.assertIn('101', self.client.polled)
        self.assertEqual(self.store.get('wb:101')['wb_status'], 'canceled_by_client')
        self.assertEqual(self.effects()[0], 0)

    def test_full_rotation_not_limited_to_recent_creation(self):
        rows = []
        for i in range(1000, 1205):
            rows.append(normalize_wildberries_order(dict(id=i, createdAt='2020-01-01T00:00:00Z', supplierStatus='complete', wbStatus='sold')))
        self.store.upsert_wildberries(rows)
        first, cursor = status_ids(self.store, 'full')
        second, cursor = status_ids(self.store, 'full', cursor)
        third, cursor = status_ids(self.store, 'full', cursor)
        self.assertEqual((len(first),len(second),len(third)), (100,100,5))
        self.assertEqual(len(set(first+second+third)),205)
        self.assertEqual(status_ids(self.store,'full',cursor)[0],first)

    def test_api_error_preserves_previous_success_and_snapshot(self):
        self.sync()
        before = self.store.get('wb:101')
        with self.store.connection() as c:
            previous = json.loads(c.execute("SELECT value FROM orders_snapshot_meta WHERE key='wb_recovery'").fetchone()[0])
        failure = WildberriesReadOnlyError('temporary', 'WB_UNAVAILABLE')
        with mock.patch.object(self.client,'get_new_orders',side_effect=failure), mock.patch.object(self.client,'get_order_statuses',side_effect=failure):
            result = self.sync()
        self.assertEqual(result['outcome'],'error')
        self.assertEqual(self.store.get('wb:101'),before)
        self.assertEqual(result['recovery']['last_success_at'],previous['last_success_at'])

    def test_partial_missing_status_is_not_success(self):
        self.client.statuses = {}
        result = self.sync()
        self.assertEqual(result['outcome'],'partial')
        self.assertGreater(result['errors'],0)
        self.assertFalse(result['recovery'].get('last_success_at'))

    def test_lock_blocks_process_and_second_sync_without_api_calls(self):
        lock = SyncLock()
        self.assertTrue(lock.acquire())
        try:
            result = self.sync()
            self.assertEqual(result['outcome'],'skipped')
            self.assertEqual(self.client.request_audit,[])
            code = "from app.services.wildberries_sync import SyncLock; l=SyncLock(); print(l.acquire())"
            child = subprocess.check_output([sys.executable,'-c',code],env=dict(os.environ))
            self.assertEqual(child.strip(),b'False')
        finally:
            lock.release()
        self.assertEqual(self.sync()['outcome'],'success')

    def test_sqlite_short_writer_and_readers_do_not_fail_or_lose_local_data(self):
        self.sync()
        ready = threading.Event()
        def local_writer():
            with self.store.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                row = c.execute("SELECT payload_json FROM orders_snapshot WHERE order_id='wb:101'").fetchone()
                payload = json.loads(row[0]); payload['comment'] = 'concurrent employee'
                c.execute("UPDATE orders_snapshot SET payload_json=? WHERE order_id='wb:101'", (json.dumps(payload),))
                ready.set(); time.sleep(0.15)
        thread = threading.Thread(target=local_writer)
        thread.start(); ready.wait(2)
        self.client.statuses['101']['wbStatus'] = 'sold'
        try:
            result = self.sync()
        finally:
            thread.join()
        self.assertEqual(result['outcome'],'success')
        self.assertEqual(self.store.get('wb:101')['comment'],'concurrent employee')
        self.assertEqual(self.store.get('wb:101')['wb_status'],'sold')

    def test_recovery_refreshes_existing_without_erasing_payload(self):
        self.sync()
        self.client.statuses['101']['supplierStatus'] = 'sorted'
        from app.services.wildberries_recovery import WildberriesRecovery
        result = WildberriesRecovery(self.client,self.orders,self.catalog).reconcile(self.store)
        self.assertEqual(result['statuses_updated'],1)
        self.assertEqual(result['recovered'],0)
        self.assertEqual(self.store.get('wb:101')['supplier_status'],'sorted')

    def test_transport_budget_stops_before_network(self):
        from app.clients.wildberries_orders import WildberriesOrdersReadOnlyClient
        session = mock.Mock()
        client = WildberriesOrdersReadOnlyClient('fake', session=session)
        client.sync_deadline = 0
        with self.assertRaises(WildberriesReadOnlyError) as error:
            client.get_new_orders()
        self.assertEqual(error.exception.code, 'WB_SYNC_BUDGET')
        session.get.assert_not_called()

    def test_terminal_statuses_and_unknown_remain_conservative(self):
        from app.services.wildberries_sync import TERMINAL
        for i, state in enumerate(sorted(TERMINAL), 1):
            self.store.upsert_wildberries([normalize_wildberries_order(dict(id=i, supplierStatus='complete', wbStatus=state))])
        self.store.upsert_wildberries([normalize_wildberries_order(dict(id=999, supplierStatus='complete', wbStatus='future_wb_status'))])
        self.assertEqual(status_ids(self.store,'fast')[0], ['999'])

    def test_cli_does_not_import_web_and_units_do_not_restart_erp(self):
        source = Path('scripts/wb_orders_sync.py').read_text()
        self.assertNotIn('app.web',source)
        for unit in Path('deploy/systemd').glob('vechasu-wb*'):
            value = unit.read_text()
            self.assertNotIn('Restart=',value)
            self.assertNotIn('gunicorn',value)
            self.assertNotIn('WB_API_TOKEN=',value)
