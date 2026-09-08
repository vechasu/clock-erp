"""Offline FAST benchmark; synthetic orders, temporary SQLite, no API credentials."""
import json
import resource
import tempfile
import time
from collections import Counter
from pathlib import Path

from app.domain_schema_migrations import apply_domain_migrations
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.wildberries_orders import normalize_wildberries_order
from app.services.wildberries_sync import run_sync


class OfflineWB:
    def __init__(self):
        self.request_audit = []
    def get_new_orders(self):
        self.request_audit.append({'path': '/orders/new'})
        return []
    def get_order_statuses(self, ids):
        self.request_audit.append({'path': '/orders/status'})
        return {str(i): {'id': int(i), 'supplierStatus': 'complete', 'wbStatus': 'waiting'} for i in ids}


def main():
    with tempfile.TemporaryDirectory(prefix='wb-offline-benchmark-') as directory:
        path = Path(directory) / 'orders.db'
        apply_domain_migrations(path, 'orders', 'offline-benchmark')
        store = OrdersSnapshotStore(path)
        store.upsert_wildberries([normalize_wildberries_order({
            'id': i, 'createdAt': '2026-09-08T00:00:00Z', 'price': 10000,
            'supplierStatus': 'complete', 'wbStatus': 'waiting'}) for i in range(1, 101)])
        counts = Counter()
        original = store.connect
        def connect():
            connection = original()
            connection.set_trace_callback(lambda sql: counts.update([sql.strip().split()[0].upper()]))
            return connection
        store.connect = connect
        client = OfflineWB()
        cpu, wall = time.process_time(), time.monotonic()
        result = run_sync(client, store, Path(directory) / 'unused.db')
        print(json.dumps({'scenario': '100 active orders, unchanged statuses, fake WB transport',
              'cpu_ms': round((time.process_time()-cpu)*1000, 2),
              'wall_seconds': round(time.monotonic()-wall, 4),
              'peak_rss_platform_units': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'api_calls': len(client.request_audit), 'sqlite_statements': dict(counts),
              'result': result['sync_status']}))


if __name__ == '__main__':
    main()
