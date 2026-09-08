"""Behavioral coverage for the actual production SQLite API contract."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.domain_schema_migrations import apply_domain_migrations
from app.services.orders_snapshot import OrdersSnapshotStore
from app.sqlite_compat import register_deterministic_function


class LegacyConnection(sqlite3.Connection):
    calls = []
    flag_error = TypeError

    def create_function(self, name, count, function, **kwargs):
        self.calls.append(dict(kwargs))
        if kwargs:
            raise self.flag_error('deterministic is unsupported')
        return super().create_function(name, count, function)


class OldSQLiteConnection(LegacyConnection):
    flag_error = sqlite3.NotSupportedError


class OrdersSQLiteCompatibilityTest(unittest.TestCase):
    def test_modern_runtime_keeps_deterministic_flag(self):
        connection = mock.Mock()
        function = lambda value: value.upper()
        register_deterministic_function(connection, 'upper_status', 1, function)
        connection.create_function.assert_called_once_with('upper_status', 1, function, deterministic=True)

    def test_registration_executes_sql_on_actual_runtime(self):
        with sqlite3.connect(':memory:') as connection:
            register_deterministic_function(connection, 'upper_status', 1, lambda value: value.upper())
            self.assertEqual(connection.execute("SELECT upper_status('sold')").fetchone()[0], 'SOLD')

    def test_legacy_fallback_executes_orders_filter_and_counts(self):
        original_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'orders.db'
            apply_domain_migrations(path, 'orders', 'compat-test')
            store = OrdersSnapshotStore(path)
            store.replace([dict(id='1', number='1', source='tictactoy', status='C',
                                products=[dict(name='Watch', quantity=2)])], 100)
            for factory in (LegacyConnection, OldSQLiteConnection):
                factory.calls = []
                def connect(*args, **kwargs):
                    kwargs['factory'] = factory
                    return original_connect(*args, **kwargs)
                with self.subTest(runtime=factory.__name__), mock.patch('sqlite3.connect', side_effect=connect):
                    result = store.query({'source':'tictactoy', 'status':'D'}, status_overrides={'1':'assembled'})
                self.assertEqual(factory.calls, [])  # Indexed SQL no longer needs a Python UDF.
                self.assertEqual(result['total'], 1)
                self.assertEqual(result['rows'][0]['id'], '1')
                self.assertEqual(result['rows'][0]['status'], 'C')
                self.assertEqual(result['status_counts'], {'D':1})
                self.assertEqual(result['source_counts'], {'all':1, 'tictactoy':1, 'wildberries':0})

    def test_unrelated_registration_errors_propagate(self):
        connection = mock.Mock()
        connection.create_function.side_effect = sqlite3.OperationalError('broken connection')
        with self.assertRaises(sqlite3.OperationalError):
            register_deterministic_function(connection, 'status', 1, str)
        self.assertEqual(connection.create_function.call_count, 1)
