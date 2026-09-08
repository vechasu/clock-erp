"""Run with systemd-provided environment; never load dotenv or import app.web."""
import argparse
import json
import os
import signal

from app.clients.wildberries_orders import WildberriesOrdersReadOnlyClient
from app.services.orders_snapshot import OrdersSnapshotStore
from app.services.wildberries_sync import SyncBusy, run_sync


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('fast', 'full'), default='fast')
    args = parser.parse_args()
    def deadline(signum, frame):
        raise TimeoutError('WB_SYNC_DEADLINE')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(240 if args.mode == 'fast' else 900)
    try:
        result = run_sync(WildberriesOrdersReadOnlyClient(os.getenv('WB_API_TOKEN')),
                          OrdersSnapshotStore(), os.getenv('CATALOG_DATABASE_PATH', 'instance/catalog.db'), args.mode)
        print(json.dumps({key: result[key] for key in ('added', 'updated', 'errors', 'sync_status')}))
        return 0 if result['sync_status'] == 'success' else 1
    except SyncBusy:
        print('WB_SYNC_RUNNING: запуск пропущен, предыдущая синхронизация ещё выполняется')
        return 0
    except Exception as error:
        print('WB_SYNC_FAILED: ' + type(error).__name__)
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
