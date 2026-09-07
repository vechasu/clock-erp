"""Contracts for the read-only presentation and authoritative ERP filters."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.services.order_presentation import present_order, status_key
from app.services.orders_snapshot import OrdersSnapshotStore, order_item_units
from app.services.wildberries_orders import normalize_wildberries_order
from app.domain_schema_migrations import apply_domain_migrations


class OrdersProgressiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'orders.db'
        apply_domain_migrations(path, 'orders', 'test')
        self.store = OrdersSnapshotStore(path)
        self.ttt = [dict(id=str(i), number=str(i), source='tictactoy', status='C' if i == 1 else 'F',
                         created_at='2026-09-07', products=[dict(name='Часы', quantity=2)]) for i in range(1, 4)]
        self.store.replace(self.ttt, 100)
        self.wb = [normalize_wildberries_order(dict(id=100+i, supplierStatus='complete', wbStatus=state,
                   article='WATCH', price=860100, createdAt='2026-09-07')) for i, state in enumerate(['waiting','sold','ready_for_pickup','canceled_by_client'])]
        self.store.upsert_wildberries(self.wb)

    def test_erp_override_controls_filter_and_count_without_changing_snapshot(self):
        before = self.store.get('1')
        result = self.store.query({'source':'tictactoy','status':'D'}, status_overrides={'1':'assembled'})
        self.assertEqual([row['id'] for row in result['rows']], ['1'])
        self.assertEqual(result['status_counts'], {'D':1,'UNKNOWN':2})
        self.assertEqual(self.store.get('1'), before)
        shown = present_order(before, {'1':'assembled'})
        self.assertEqual(shown['status'], 'C')
        self.assertEqual(shown['ui_status_label'], 'Собран')

    def test_unknown_codes_remain_separate_from_unconfirmed(self):
        self.assertEqual(status_key({'status':'P'}), 'UNKNOWN')
        self.assertEqual(status_key({'status':'W'}), 'UNKNOWN')
        self.assertEqual(self.store.query({'status':'N'})['total'], 0)
        self.assertEqual(self.store.query({'status':'UNKNOWN'})['total'], 2)

    def test_both_wb_statuses_are_used_and_sale_is_independent(self):
        keys = ['WB_COMPLETE','WB_SOLD','WB_READY_FOR_PICKUP','WB_CANCELED_BY_CLIENT']
        for row,key in zip(self.wb, keys):
            shown = present_order(dict(row, sale_completed=True))
            self.assertEqual(shown['ui_status'], key)
            self.assertEqual(shown['supplier_status'], 'complete')
            self.assertEqual(shown['wb_status'], row['wb_status'])
            self.assertEqual(self.store.query({'source':'wildberries','status':key})['total'], 1)
        self.assertEqual(status_key(dict(self.wb[0], wb_status='future')), 'WB_UNKNOWN')

    def test_all_sources_counts_scope_and_exact_search(self):
        state = self.store.query({})
        self.assertEqual(state['source_counts'], {'all':7,'tictactoy':3,'wildberries':4})
        self.assertEqual(self.store.query({'source':'tictactoy','q':'100'})['total'], 0)
        self.assertEqual(self.store.query({'source':'wildberries','q':'100'})['total'], 1)
        self.assertEqual(self.store.query({'source':'wildberries','q':'WATCH'})['total'], 4)
        self.assertEqual(self.store.query({'source':'tictactoy','q':'WATCH'})['total'], 0)

    def test_memory_and_sql_filters_agree(self):
        for source in ['all','tictactoy','wildberries']:
            for status in ['all','D','UNKNOWN','WB_SOLD']:
                args = {'source':source,'status':status}
                sql = self.store.query(args, status_overrides={'1':'assembled'})
                memory = web.prepare_orders_list(self.ttt+self.wb, args, status_overrides={'1':'assembled'})
                self.assertEqual(sql['total'], memory['total'])
                self.assertEqual(sql['source_counts'], memory['source_counts'])
                self.assertEqual(sql['status_counts'], memory['status_counts'])

    def test_quantity_is_units_not_lines_or_assembly(self):
        self.assertEqual(order_item_units({'products':[{'quantity':2},{'quantity':3}]}), 5)
        self.assertEqual(order_item_units(self.wb[0]), 1)
        self.assertIsNone(order_item_units({'products':[]}))

    def test_wb_exact_miss_never_queries_bitrix(self):
        with web.app.test_request_context('/api/orders?source=wildberries&q=888'), mock.patch.object(web,'bitrix_orders_client') as client:
            self.assertIsNone(web.exact_order_search_state('888', []))
            client.assert_not_called()

    def test_bulk_sale_lookup_preserves_source_identifiers(self):
        database = mock.MagicMock()
        connection = database.connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchall.return_value = [
            {'id':'wb-sale', 'source':'wildberries', 'external_order_id':'123'},
            {'id':'ttt-sale', 'source':'tictactoy', 'external_order_id':'123'},
        ]
        self.assertEqual(web.bulk_conducted_order_sales(['wb:123', '123'], database),
                         {'wb:123':'wb-sale', '123':'ttt-sale'})
        self.assertEqual(connection.execute.call_args[0][1], ['123', '123'])

    def test_distant_pages_remain_reachable(self):
        rows = [dict(self.ttt[0], id=str(i),number=str(i)) for i in range(1,242)]
        self.store.replace(rows, 101)
        result = self.store.query({'source':'tictactoy','page':13,'page_size':20})
        self.assertEqual((result['page'],result['page_count'],len(result['rows'])), (13,13,1))
        self.assertIn(1,result['page_items'])
        self.assertIn(13,result['page_items'])
