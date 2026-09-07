import copy
import unittest
from datetime import date

from app.services.repair_cases import REPAIR_STATUS_LABELS, REPAIR_LOCATION_LABELS, apply_repair_action, available_repair_actions
from app.services.repair_workflow import STATE_RULES, repair_workflow


class RepairWorkflowTest(unittest.TestCase):
    def test_every_status_and_location_has_safe_presentation(self):
        for status in list(REPAIR_STATUS_LABELS) + ['unexpected', '']:
            for location in list(REPAIR_LOCATION_LABELS) + ['unexpected', '']:
                for archived in ['', '2026-09-01']:
                    with self.subTest(status=status, location=location, archived=archived):
                        case = dict(status=status, location=location, archived_at=archived)
                        before = copy.deepcopy(case)
                        result = repair_workflow(case)
                        self.assertEqual(case, before)
                        if result['action']:
                            self.assertIn(result['action'], available_repair_actions(case))
                        if archived or result['needs_review'] or result['closed']:
                            self.assertFalse(result['action'])

    def test_status_rules_cover_backend(self):
        self.assertEqual(set(STATE_RULES), set(REPAIR_STATUS_LABELS))

    def test_delivered_new_is_not_invented_completion(self):
        case = dict(status='new', location='delivered', history=[])
        before = copy.deepcopy(case)
        self.assertEqual(repair_workflow(case)['state'], 'review')
        self.assertFalse(available_repair_actions(case))
        for action in ['receive', 'request_shipment', 'cancel', 'complete']:
            with self.assertRaises(ValueError):
                apply_repair_action(case, action, {'reason': 'test'})
            self.assertEqual(case, before)

    def test_attention_and_overdue_are_independent_of_stage(self):
        case = dict(status='diagnostics', location='with_master', control_date='2026-09-01')
        result = repair_workflow(case, date(2026, 9, 4))
        self.assertEqual(result['state'], 'master')
        self.assertEqual(result['overdue_days'], 3)
        self.assertTrue(result['needs_attention'])
        case['control_date'] = 'invalid'
        self.assertEqual(repair_workflow(case)['overdue_days'], 0)
        case.update(archived_at='2026-09-04', control_date='2026-09-01')
        self.assertFalse(repair_workflow(case)['needs_attention'])

    def test_local_receipt_and_free_repair_do_not_infer_payment(self):
        self.assertEqual(repair_workflow(dict(status='new', location='at_us'))['action'], 'receive_and_start_diagnostics')
        case = dict(status='waiting_decision', location='with_master', history=[])
        apply_repair_action(case, 'accept_free', dict(guided=True, customer_decision='Гарантия', control_date='2026-10-01'))
        self.assertEqual(case['status'], 'in_repair')
        self.assertNotIn('payment_amount', case)
        self.assertTrue(case['history'])

    def test_handover_combines_existing_transitions_atomically_and_is_idempotent(self):
        case = dict(status='new', location='at_us', history=[])
        before = copy.deepcopy(case)
        with self.assertRaises(ValueError):
            apply_repair_action(case, 'receive_and_start_diagnostics', {'guided': True})
        self.assertEqual(case, before)
        payload = dict(guided=True, control_date='2026-10-01', idempotency_key='handover')
        self.assertTrue(apply_repair_action(case, 'receive_and_start_diagnostics', payload))
        self.assertEqual((case['status'], case['location']), ('diagnostics', 'with_master'))
        self.assertEqual(len([e for e in case['history'] if e.get('field') == 'status']), 2)
        after = copy.deepcopy(case)
        self.assertFalse(apply_repair_action(case, 'receive_and_start_diagnostics', payload))
        self.assertEqual(case, after)
        case = dict(status='new', location='with_customer', history=[])
        before = copy.deepcopy(case)
        with self.assertRaises(ValueError):
            apply_repair_action(case, 'receive_and_start_diagnostics', payload)
        self.assertEqual(case, before)
