"""The receipt browser lifecycle moved to frontend/e2e-supplies/supplies.spec.ts.

CI runs that suite against real local transactions and a fake Bitrix catalog.
These checks retain the route and responsive table shell contract.
"""
import unittest
from unittest.mock import patch
from app import web

class ReceiptWorkspaceTest(unittest.TestCase):
    def test_workspace_uses_local_supply_interface(self):
        with patch.dict(web.app.config, {'TESTING': True, 'AUTH_TESTING': False}), patch.object(web, 'MoySkladClient', side_effect=AssertionError('remote')):
            page=web.app.test_client().get('/app/receipts')
        self.assertEqual(page.status_code,200)
        text=page.get_data(as_text=True)
        for label in ('Все записи','Поставки','Отмены продаж','Новая поставка','Добавить из Bitrix'):
            self.assertIn(label,text)
        self.assertIn('class="table-scroll"',text)
        self.assertIn('aria-label="Таблица позиций поставки"',text)
