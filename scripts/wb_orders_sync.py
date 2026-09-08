"""One isolated WB sync; never imports Flask or starts a web worker."""
import argparse
import json
import logging
import os
import resource
import sys
import time

from app.catalog_db import CatalogDatabase
from app.clients.wildberries_orders import WildberriesOrdersReadOnlyClient
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.wildberries_sync import run_sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('fast', 'full'), default='fast')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    started, cpu = time.monotonic(), time.process_time()
    client = WildberriesOrdersReadOnlyClient(os.getenv('WB_API_TOKEN'), timeout=(3.05, 10), max_retries=2)
    result = run_sync(client, OrdersSnapshotStore(), CatalogDatabase().path, args.mode)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    summary = {key: result.get(key) for key in ('outcome', 'added', 'statuses_updated', 'recovered', 'errors', 'message')}
    summary.update(mode=args.mode, api_requests=len(client.request_audit),
                   wall_seconds=round(time.monotonic()-started, 3),
                   cpu_seconds=round(time.process_time()-cpu, 3), peak_rss=usage.ru_maxrss)
    print('WB_SYNC ' + json.dumps(summary, ensure_ascii=True))
    return 0 if result['outcome'] in ('success', 'skipped') else 1


if __name__ == '__main__':
    sys.exit(main())
