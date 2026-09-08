"""Bounded WB orchestration shared by HTTP and the short-lived timer process."""
import fcntl
import json
import logging
import time
from pathlib import Path

from app.clients.wildberries_orders import WildberriesReadOnlyError
from app.services.wildberries_orders import synchronize_wildberries_orders
from app.services.wildberries_recovery import WildberriesRecovery, diagnostics, save_diagnostics, stamp

# FBS /api/v3/orders/status: https://dev.wildberries.ru/en/docs/openapi/orders-fbs
TERMINAL_WB_STATUSES = frozenset(('sold', 'canceled', 'canceled_by_client', 'declined_by_client', 'defect'))


class SyncBusy(Exception):
    pass


class WBSyncLock:
    """flock covers both Gunicorn workers and CLI processes using the same DB."""
    def __init__(self, orders_path):
        self.path = str(Path(orders_path).resolve()) + '.wb-sync.lock'
        self.handle = None

    def acquire(self, blocking=False):
        handle = open(self.path, 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def run_sync(client, store, catalog_path, mode='fast', locked=False):
    if mode not in ('fast', 'full'):
        raise ValueError('Unknown WB sync mode')
    lock = WBSyncLock(store.path)
    if not locked and not lock.acquire():
        logging.getLogger(__name__).info('WB_SYNC_RUNNING: another synchronization owns the lock')
        raise SyncBusy('Синхронизация WB уже выполняется')
    try:
        return _run_sync(client, store, catalog_path, mode)
    finally:
        if not locked:
            lock.release()


def _run_sync(client, store, catalog_path, mode):
    started = time.monotonic()
    client.request_budget = 30 if mode == 'fast' else 200
    client.deadline = started + (200 if mode == 'fast' else 840)
    previous = diagnostics(store.path)
    info = dict(previous, last_attempt_at=stamp(), result='running', mode=mode,
                new_orders=0, updated=0, recovered=0, errors=[])
    save_diagnostics(store, info)
    outcome = {'received': 0, 'added': 0, 'updated': 0, 'errors': 0}
    failures = []
    completed = 0
    try:
        try:
            outcome = synchronize_wildberries_orders(client, store, only_missing=True)
            completed += 1
            if outcome['errors']:
                failures.append({'error': 'WB_INVALID_NEW_ORDERS', 'count': outcome['errors']})
        except WildberriesReadOnlyError as error:
            failures.append({'error': error.code})
        # One read of known snapshots; no DB transaction held across HTTP calls.
        with store.connection() as connection:
            known = [json.loads(row['payload_json']) for row in connection.execute(
                "SELECT payload_json FROM orders_snapshot WHERE source='wildberries'")]
        ids = [order['wb_order_id'] for order in known if mode == 'full'
               or order.get('wb_status') not in TERMINAL_WB_STATUSES]
        for offset in range(0, len(ids), 100):
            batch = ids[offset:offset + 100]
            try:
                statuses = client.get_order_statuses(batch)
                with store.connection() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    for external_id in batch:
                        status = statuses.get(str(external_id)) or {}
                        if not status.get('supplierStatus') or not status.get('wbStatus'):
                            failures.append({'order_id': str(external_id), 'error': 'WB_STATUS_MISSING'})
                            continue
                        outcome['updated'] += store.update_wildberries_status(external_id, status, connection)
                completed += 1
            except WildberriesReadOnlyError as error:
                failures.append({'error': error.code, 'count': len(batch)})
        if mode == 'full':
            try:
                recovery = WildberriesRecovery(client, store.path, catalog_path).reconcile(store)
                info.update(recovery)
                outcome['updated'] += recovery.get('updated', 0)
                failures.extend(recovery['errors'])
                completed += 1
            except (WildberriesReadOnlyError, ValueError) as error:
                failures.append({'error': getattr(error, 'code', type(error).__name__)})
    except Exception as error:
        failures.append({'error': type(error).__name__})
    info.update(new_orders=outcome['added'], updated=outcome['updated'], errors=failures,
                checked_at=stamp(), duration_seconds=round(time.monotonic() - started, 3),
                api_requests=len(getattr(client, 'request_audit', [])))
    info['result'] = ('partial' if completed else 'error') if failures else 'success'
    if not failures:
        info['last_success_at'] = stamp()
        info['last_' + mode + '_success_at'] = info['last_success_at']
    save_diagnostics(store, info)
    outcome.update(errors=len(failures), recovery=info, sync_status=info['result'])
    return outcome
