import gzip
import unittest
from unittest import mock
from flask import Flask
from app import web
from app.orders_response import register_orders_response


class OrdersTransportTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        with mock.patch.object(self.app.jinja_env, 'get_template'):
            register_orders_response(self.app)
        self.body = ('Заказы <p>read-only</p>' * 200).encode('utf-8')
        self.app.add_url_rule('/orders', 'orders_page', lambda: self.body)
        self.app.add_url_rule('/other', 'other', lambda: self.body)
        self.client = self.app.test_client()

    def test_compression_negotiation_and_body_parity(self):
        plain = self.client.get('/orders')
        compressed = self.client.get('/orders', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(gzip.decompress(compressed.data), plain.data)
        self.assertLess(len(compressed.data), len(plain.data) // 5)
        self.assertIn('Accept-Encoding', compressed.headers['Vary'])
        for value in ('identity', 'gzip;q=0, identity;q=1'):
            response = self.client.get('/orders', headers={'Accept-Encoding': value})
            self.assertNotIn('Content-Encoding', response.headers)
            self.assertEqual(response.data, plain.data)
        self.assertNotIn('Content-Encoding', self.client.get('/other', headers={'Accept-Encoding': 'gzip'}).headers)

    def test_sidebar_computes_badges_once_and_retains_hidden_preferences(self):
        rows = [{'key': 'orders', 'enabled': True}, {'key': 'mail', 'enabled': False}]
        with web.app.test_request_context('/orders'), \
                mock.patch.object(web, 'current_auth_user', return_value=None), \
                mock.patch.object(web, 'get_navigation_items', return_value=rows) as navigation, \
                mock.patch.object(web, '_task_users', return_value=[]), \
                mock.patch.object(web, 'sms_permissions', return_value={}):
            result = web.inject_sidebar_navigation()
        navigation.assert_called_once_with(include_disabled=True)
        self.assertEqual(result['sidebar_navigation_items'], rows[:1])
        self.assertEqual(result['navigation_preference_items'], rows)
