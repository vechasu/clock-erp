"""Regression budgets for the real SQL path, never external integrations."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.domain_schema_migrations import apply_domain_migrations, _apply_domain_baseline
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.order_presentation import status_key
from app.services.wildberries_orders import normalize_wildberries_order


class OrdersPerformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temporary.name) / 'orders.db'
        apply_domain_migrations(cls.path, 'orders', 'performance-test')
        cls.store = OrdersSnapshotStore(cls.path)
        rows = [normalize_wildberries_order(dict(
            id=900000 + i, supplierStatus='new', wbStatus='sold' if i % 2 else 'waiting',
            article='TEST', createdAt='2026-09-07T05:05:00Z', price=100000,
        )) for i in range(16471)]
        cls.store.upsert_wildberries(rows)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_page_deserializes_only_requested_orders_and_query_count_is_constant(self):
        original = self.store.connect
        statements = []
        def connect():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection
        counts = []
        with mock.patch.object(self.store, 'connect', side_effect=connect):
            for size in (20, 50, 200):
                statements.clear()
                with mock.patch('app.services.orders_snapshot.json.loads', wraps=json.loads) as loads:
                    result = self.store.query({'page_size': size})
                self.assertEqual(loads.call_count, size)
                self.assertEqual(result['total'], 16471)
                counts.append(sum(sql.startswith('SELECT') for sql in statements))
        self.assertEqual(counts, [3, 3, 3])

    def test_page_and_exact_identity_plans_use_indexes(self):
        with self.store.connection() as connection:
            page = [row[3] for row in connection.execute(
                'EXPLAIN QUERY PLAN SELECT payload_json FROM orders_snapshot '
                'ORDER BY created_sort DESC, order_id DESC LIMIT 50')]
            self.assertTrue(any('idx_orders_page' in row for row in page), page)
            self.assertFalse(any('TEMP B-TREE' in row for row in page), page)
            exact = [row[3] for row in connection.execute(
                'EXPLAIN QUERY PLAN SELECT order_id FROM orders_snapshot '
                'WHERE number_fold=? OR order_id=? OR external_order_id=?', ('900001',)*3)]
            self.assertFalse(any('SCAN ' in row for row in exact), exact)

    def test_projection_tracks_wb_refresh_without_mutating_source_status(self):
        order = normalize_wildberries_order(dict(id=999999, supplierStatus='confirm', wbStatus='waiting'))
        self.store.upsert_wildberries([order])
        try:
            order['wb_status'] = 'sold'
            self.store.update_wildberries_statuses({'999999': {'supplierStatus': 'confirm', 'wbStatus': 'sold'}})
            with self.store.connection() as connection:
                row = connection.execute('SELECT work_status,status,payload_json FROM orders_snapshot WHERE order_id=?', ('wb:999999',)).fetchone()
            self.assertEqual(row['work_status'], status_key(order))
            self.assertEqual(row['status'], 'confirm')
            self.assertEqual(json.loads(row['payload_json'])['wb_status'], 'sold')
        finally:
            with self.store.connection() as connection:
                connection.execute('DELETE FROM orders_snapshot WHERE order_id=?', ('wb:999999',))

    def test_explicit_list_refresh_does_not_propagate_retry_to_navigation(self):
        config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        try:
            with mock.patch.object(web, 'get_orders', return_value=[]) as refresh:
                response = web.app.test_client().get('/orders?retry=1&source=tictactoy')
                refresh.assert_called_once_with(force=True)
            self.assertEqual(response.status_code, 303)
            self.assertNotIn('retry=', response.headers['Location'])
            self.assertIn('source=tictactoy', response.headers['Location'])
        finally:
            web.app.config.clear()
            web.app.config.update(config)

    def test_explicit_refresh_updates_only_selected_local_snapshot(self):
        config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False, ORDERS_SNAPSHOT_TESTING=True)
        self.store.upsert_bitrix([{'id': '7002', 'status': 'N', 'products': []}], 1000)
        try:
            with mock.patch.dict('os.environ', {'ORDERS_DATABASE_PATH': str(self.path)}), \
                 mock.patch.object(web, 'get_order', return_value={'id': '7002', 'status': 'A', 'products': [{'name': 'Fresh', 'quantity': 2}]}) as fetch:
                response = web.app.test_client().post('/order/7002/refresh')
                fetch.assert_called_once_with(7002)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(self.store.get('7002')['products'][0]['name'], 'Fresh')
            self.assertEqual(self.store.get('7002')['status'], 'A')
        finally:
            with self.store.connection() as connection:
                connection.execute("DELETE FROM orders_snapshot WHERE order_id='7002'")
            web.app.config.clear()
            web.app.config.update(config)

    def test_fragment_never_loads_list_or_bitrix(self):
        config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False, ORDERS_SNAPSHOT_TESTING=True)
        self.store.upsert_bitrix([{'id': '7001', 'status': 'N', 'products': []}], 1000)
        try:
            with mock.patch.dict('os.environ', {'ORDERS_DATABASE_PATH': str(self.path)}), \
                 mock.patch.object(web, 'current_orders_list_state', side_effect=AssertionError('list loaded')), \
                 mock.patch.object(web, 'get_order', side_effect=AssertionError('external detail loaded')), \
                 mock.patch.object(web, 'get_orders', side_effect=AssertionError('external list loaded')):
                for path in ('/order/wildberries/900000', '/order/7001'):
                    response = web.app.test_client().get(path, headers={'X-Order-Detail': '1'})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('X-Order-Detail', response.headers['Vary'])
                    self.assertNotIn('orders-list-panel', response.get_data(as_text=True))
                    self.assertNotIn('<html', response.get_data(as_text=True))
        finally:
            with self.store.connection() as connection:
                connection.execute("DELETE FROM orders_snapshot WHERE order_id='7001'")
            web.app.config.clear()
            web.app.config.update(config)


class OrdersProjectionMigrationTest(unittest.TestCase):
    def test_old_baseline_upgrade_is_atomic_idempotent_and_preserves_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'orders.db'
            _apply_domain_baseline(path, 'orders')
            payload = json.dumps({'id': '1', 'status': 'O', 'source': 'tictactoy'})
            with sqlite3.connect(str(path)) as connection:
                connection.execute('INSERT INTO orders_snapshot '
                    '(order_id,source_position,number_fold,customer_fold,phone_digits,amount_search,date_search,created_sort,status,payload_json,loaded_at,external_order_id) '
                    "VALUES ('1',0,'1','','','','','','O',?,0,'1')", (payload,))
            def interrupt(statement):
                if 'idx_orders_source_page' in statement:
                    raise RuntimeError('simulated migration interruption')
            with self.assertRaises(RuntimeError):
                apply_domain_migrations(path, 'orders', observer=interrupt)
            with sqlite3.connect(str(path)) as connection:
                self.assertNotIn('work_status', [row[1] for row in connection.execute('PRAGMA table_info(orders_snapshot)')])
                self.assertEqual(connection.execute('SELECT payload_json FROM orders_snapshot').fetchone()[0], payload)
            apply_domain_migrations(path, 'orders')
            apply_domain_migrations(path, 'orders')
            with sqlite3.connect(str(path)) as connection:
                self.assertEqual(connection.execute('SELECT payload_json,status,work_status FROM orders_snapshot').fetchone(), (payload, 'O', 'N'))
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM erp_migration_ledger').fetchone()[0], 2)
