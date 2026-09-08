import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from flask import Flask, g, render_template_string, request
from app import request_timing


class OrderRequestTimingTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'isolated-timing-test'
        request_timing.register_order_request_timing(self.app)

        @self.app.before_request
        def user():
            g.current_user = {'id': 'test'} if request.headers.get('X-Test-User') else None

        @self.app.route('/orders')
        def orders():
            connection = sqlite3.connect(':memory:')
            try:
                connection.row_factory = sqlite3.Row
                for number in range(int(request.args.get('queries', '1'))):
                    row = connection.execute('SELECT ? AS value', (number,)).fetchone()
                    self.assertEqual(row['value'], number)
                return render_template_string('{{ value }}', value='safe')
            finally:
                connection.close()

    def metrics(self, response):
        return {item.split(';')[0]: float(item.split('=')[1]) for item in response.headers['Server-Timing'].split(', ')}

    def test_opt_in_includes_database_template_and_full_backend(self):
        response = self.app.test_client().get('/orders?orders_profile=1&queries=3', headers={'X-Test-User': '1'})
        metrics = self.metrics(response)
        self.assertEqual(metrics['queries'], 3)
        self.assertGreaterEqual(metrics['backend'], metrics['sql'])
        self.assertGreaterEqual(metrics['backend'], metrics['template'])
        self.assertEqual(metrics['external_calls'], 0)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertNotIn('SELECT', response.headers['Server-Timing'])
        self.assertIsNone(getattr(request_timing._local, 'metrics', None))

    def test_disabled_requests_use_native_sqlite_and_emit_nothing(self):
        response = self.app.test_client().get('/orders', headers={'X-Test-User': '1'})
        self.assertNotIn('Server-Timing', response.headers)
        connection = sqlite3.connect(':memory:')
        try:
            self.assertNotIsInstance(connection, request_timing.TimedConnection)
        finally:
            connection.close()

    def test_anonymous_profile_cannot_expose_timings_or_enable_cookie(self):
        response = self.app.test_client().get('/orders?orders_profile=1')
        self.assertNotIn('Server-Timing', response.headers)
        self.assertNotIn('Set-Cookie', response.headers)

    def test_parallel_requests_do_not_share_query_counts(self):
        def run(count):
            response = self.app.test_client().get('/orders?orders_profile=1&queries='+str(count), headers={'X-Test-User': '1'})
            return self.metrics(response)['queries']
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(run, [2, 7])), [2, 7])

    def test_external_wrapper_counts_without_recording_request_details(self):
        request_timing._local.metrics = {'external': 0, 'external_calls': 0}
        try:
            with mock.patch.object(request_timing, '_send', return_value='local stub'):
                self.assertEqual(request_timing._timed_send(None, object()), 'local stub')
            self.assertEqual(request_timing._local.metrics['external_calls'], 1)
            self.assertEqual(set(request_timing._local.metrics), {'external', 'external_calls'})
        finally:
            request_timing._local.metrics = None
