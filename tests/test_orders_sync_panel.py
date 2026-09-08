"""Manual sync delegates to existing import logic and reports cached outcomes."""
import unittest
from unittest.mock import patch
from app import web


class OrdersSyncPanelTest(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()
        for name in ('can_view_orders', 'require_csrf_when_authenticated'):
            stub = patch.object(web, name, return_value=True)
            stub.start()
            self.addCleanup(stub.stop)
        auth = patch.dict(web.app.config, TESTING=True, AUTH_ENABLED=False)
        auth.start()
        self.addCleanup(auth.stop)

    def test_state_read_never_calls_import_and_has_no_invented_success(self):
        with patch.dict(web.ORDERS_CACHE, loaded_at=0, error=''), patch.object(web, 'get_orders') as sync:
            response = self.client.get('/api/orders/tictactoy/sync')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['result']['outcome'], 'unknown')
        self.assertIsNone(response.json['result']['last_success_at'])
        sync.assert_not_called()

    def test_manual_sync_reuses_import_and_preserves_error(self):
        for error, status in [('', 200), ('BitrixReadOnlyError', 503)]:
            with self.subTest(error=error), patch.dict(web.ORDERS_CACHE, loaded_at=1234, error=error), patch.object(web, 'get_orders') as sync:
                response = self.client.post('/api/orders/tictactoy/sync')
                sync.assert_called_once_with(force=True)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json['result']['error'], error)
                self.assertFalse(web.ORDERS_REFRESH_LOCK.locked())

    def test_existing_refresh_lock_prevents_duplicate_manual_sync(self):
        with web.ORDERS_REFRESH_LOCK, patch.object(web, 'get_orders') as sync:
            response = self.client.post('/api/orders/tictactoy/sync')
            self.assertEqual(response.status_code, 409)
            sync.assert_not_called()

    def test_forbidden_reader_cannot_sync(self):
        with patch.object(web, 'can_view_orders', return_value=False), patch.object(web, 'get_orders') as sync:
            self.assertEqual(self.client.post('/api/orders/tictactoy/sync').status_code, 403)
            sync.assert_not_called()
