from concurrent.futures import ThreadPoolExecutor
import copy
import json
from datetime import datetime
from unittest import mock, TestCase

from app import web
from app.services.repair_cases import apply_repair_action, load_repair_file
from tests import test_repairs_full_cycle as fixtures


class RepairCompletionTest(TestCase):
    setUp = fixtures.RepairsFullCycleTest.setUp
    tearDown = fixtures.RepairsFullCycleTest.tearDown
    create = fixtures.RepairsFullCycleTest.create
    action = fixtures.RepairsFullCycleTest.action

    def ready(self, status='ready_return', location='at_us'):
        repair = self.create()
        cases = load_repair_file(self.store)
        cases[0].update(status=status, location=location)
        web.save_repair_cases(cases)
        return repair['id']

    def test_completion_outcomes_and_archive(self):
        for result in ['repaired', 'impossible', 'customer_declined']:
            with self.subTest(result=result):
                self.store.write_text('[]')
                repair_id = self.ready()
                before = load_repair_file(self.store)[0]['history']
                with mock.patch.object(web, 'current_repair_user_name', return_value='Максим Усачев'):
                    response = self.action(repair_id, 'complete', completion_result=result,
                                           return_method='pickup', final_cost='2500', comment='Заменён механизм')
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                case = response.get_json()['data']
                self.assertEqual(case['completion_result'], result)
                self.assertEqual(case['completed_by'], 'Максим Усачев')
                self.assertEqual(case['final_cost'], '2500.00')
                self.assertEqual(case['completion_comment'], 'Заменён механизм')
                self.assertEqual(case['archived_at'], case['completed_at'])
                self.assertIsNotNone(datetime.fromisoformat(case['completed_at']).tzinfo)
                self.assertEqual(case['history'][:len(before)], before)
                events = [e for e in case['history'] if e['action'] == 'Ремонт завершён']
                self.assertEqual(len(events), 1)
                self.assertIn('2500.00 ₽', events[0]['comment'])
                self.assertEqual(events[0]['timestamp'], case['completed_at'])
                active = self.client.get('/api/v1/repairs?view=active').get_json()
                archive = self.client.get('/api/v1/repairs?view=archive').get_json()
                self.assertEqual(active['data'], [])
                self.assertEqual(archive['data'][0]['id'], repair_id)
                self.assertEqual(active['meta']['stats']['archived'], 1)
                saved = self.store.read_bytes()
                self.assertEqual(self.action(repair_id, 'complete', completion_result='impossible').status_code, 409)
                self.assertEqual(self.store.read_bytes(), saved)

    def test_invalid_completion_is_atomic(self):
        repair_id = self.ready()
        for fields in [dict(completion_result='unknown'), dict(completion_result='replaced'),
                       dict(completion_result='repaired', final_cost='-1'),
                       dict(completion_result='repaired', final_cost='NaN'),
                       dict(completion_result='repaired', final_cost='Infinity'),
                       dict(completion_result='repaired', final_cost='1e999')]:
            before = self.store.read_bytes()
            response = self.action(repair_id, 'complete', return_method='pickup', **fields)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(self.store.read_bytes(), before)
        self.assertEqual(self.action('invalid-id', 'complete', completion_result='repaired').status_code, 404)

    def test_alternative_diagnostic_outcomes_and_zero_cost(self):
        for result in ['impossible', 'customer_declined']:
            case = dict(status='diagnostics', location='with_master', history=[])
            apply_repair_action(case, 'complete', dict(completion_result=result, final_cost=0))
            self.assertEqual(case['final_cost'], '0.00')
            self.assertEqual(case['location'], 'with_master')
            self.assertTrue(case['archived_at'])
        case = dict(status='diagnostics', location='with_master', history=[])
        before = copy.deepcopy(case)
        with self.assertRaises(ValueError):
            apply_repair_action(case, 'complete', dict(completion_result='repaired'))
        self.assertEqual(case, before)
        case.update(status='ready_return', location='at_us')
        apply_repair_action(case, 'complete', dict(completion_result='repaired', return_method='pickup'))
        self.assertFalse(case.get('final_cost'))

    def test_old_archive_is_read_only_and_has_fallback(self):
        self.store.write_text(json.dumps([dict(id='old', schema_version=4, status='completed',
                                              location='delivered', history=[], completed_at='2026-01-01 10:00')]))
        before = self.store.read_bytes()
        data = self.client.get('/api/v1/repairs?view=archive').get_json()['data']
        self.assertEqual(data[0]['completion_result_label'], 'Завершён')
        self.assertEqual(self.client.get('/api/v1/repairs?view=active').get_json()['data'], [])
        self.assertEqual(self.client.get('/app/repairs?view=archive').status_code, 200)
        self.assertEqual(self.store.read_bytes(), before)


    def test_concurrent_completion_adds_one_final_event(self):
        repair_id = self.ready()
        def finish(_):
            with web.app.test_client() as client:
                return client.post('/api/v1/repairs/' + repair_id + '/actions/complete',
                                   json=dict(completion_result='repaired', return_method='pickup')).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(finish, range(2))), [200, 409])
        case = load_repair_file(self.store)[0]
        self.assertEqual(len([e for e in case['history'] if e['action'] == 'Ремонт завершён']), 1)
