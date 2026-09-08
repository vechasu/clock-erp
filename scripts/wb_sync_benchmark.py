"""Isolated FAST benchmark with synthetic WB payloads; never accesses production."""
import argparse
import json
import os
import resource
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from unittest import mock

from app.domain_schema_migrations import apply_domain_migrations
from app.schema_migrations import apply_migrations
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.wildberries_orders import normalize_wildberries_order
from app.services.wildberries_sync import run_sync


class Client:
    def __init__(self):
        self.request_audit = []

    def get_new_orders(self):
        self.request_audit.append('new')
        return []

    def get_order_statuses(self, ids):
        self.request_audit.append('status')
        return {i:dict(id=int(i), supplierStatus='complete', wbStatus='waiting') for i in ids}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--orders', type=int, default=100)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='wb-benchmark-') as directory:
        root = Path(directory)
        apply_domain_migrations(root/'orders.db', 'orders', 'benchmark')
        apply_migrations(root/'catalog.db')
        store = OrdersSnapshotStore(root/'orders.db')
        store.initialize()
        store.upsert_wildberries([normalize_wildberries_order(dict(id=i, supplierStatus='complete', wbStatus='waiting',
                                  createdAt='2026-09-06T10:00:00Z', price=10000)) for i in range(1,args.orders+1)])
        samples = []
        original = store.connect
        counts = {}
        def trace(sql):
            verb = sql.lstrip().split()[0].upper()
            counts[verb] = counts.get(verb,0)+1
        def connect():
            c = original(); c.set_trace_callback(trace); return c
        with mock.patch.dict(os.environ, {'WB_SYNC_LOCK_PATH':str(root/'lock')}), mock.patch.object(store,'connect',side_effect=connect):
            for iteration in range(5):
                counts.clear(); client = Client()
                start, cpu = time.monotonic(), time.process_time()
                # Different polling timestamps: unchanged status still records freshness.
                with mock.patch('app.services.wildberries_recovery.stamp',return_value='2026-09-08T10:{:02d}:00+00:00'.format(iteration)):
                    result = run_sync(client,store,root/'catalog.db')
                assert result['outcome']=='success', result
                samples.append(dict(wall_ms=round((time.monotonic()-start)*1000,2),
                    cpu_ms=round((time.process_time()-cpu)*1000,2),api_calls=len(client.request_audit), sql=dict(counts)))
        print(json.dumps(dict(transport='FAKE, zero network latency',orders=args.orders,samples=samples,
            median_wall_ms=statistics.median(s['wall_ms'] for s in samples),median_cpu_ms=statistics.median(s['cpu_ms'] for s in samples),
            note='Plus 1 diagnostics SELECT per run on a separate read-only connection; schema validation excluded',
            peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, sqlite=sqlite3.sqlite_version),indent=2))


if __name__=='__main__':
    main()
