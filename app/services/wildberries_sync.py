"""Bounded WB orchestration shared by HTTP and the oneshot CLI (Python 3.6)."""
import fcntl
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from app.clients.wildberries_orders import WildberriesReadOnlyError
from app.services.wildberries_orders import synchronize_wildberries_orders
from app.services.wildberries_recovery import WildberriesRecovery, diagnostics, save_diagnostics, stamp

LOGGER = logging.getLogger(__name__)
# Official FBS wbStatus values; supplier complete/sorted are NOT terminal.
# https://dev.wildberries.ru/docs/openapi/orders-fbs
TERMINAL = frozenset(('sold', 'canceled', 'canceled_by_client', 'declined_by_client', 'defect'))


class SyncLock:
    """One inode shared by CLI, both Gunicorn processes and recovery import."""
    def __init__(self, path=None):
        self.path = path
        self.guard = threading.Lock()
        self.handle = None

    def acquire(self, blocking=False):
        if not self.guard.acquire(blocking):
            return False
        try:
            path = Path(self.path or os.getenv('WB_SYNC_LOCK_PATH') or
                        str(Path(os.getenv('ORDERS_DATABASE_PATH', 'instance/orders.db')).resolve().parent / '.wb-sync.lock'))
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = path.open('a+')
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.handle.close()
                self.handle = None
                self.guard.release()
                LOGGER.warning('WB_SYNC skipped: another synchronization holds the lock')
                return False
            return True
        except Exception:
            self.guard.release()
            raise

    def release(self):
        if self.handle:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
        self.guard.release()


def status_ids(store, mode, cursor=''):
    """Active orders every pass, plus a rotating 100 terminal orders per FULL."""
    active, terminal = [], []
    with store.connection() as connection:
        for row in connection.execute("SELECT external_order_id,payload_json FROM orders_snapshot WHERE source='wildberries' ORDER BY external_order_id"):
            payload = json.loads(row['payload_json'])
            (terminal if payload.get('wb_status') in TERMINAL else active).append(row['external_order_id'])
    selected = []
    if mode == 'full' and terminal:
        selected = [value for value in terminal if value > cursor][:100]
        if not selected:
            selected = terminal[:100]
    return active + selected, selected[-1] if selected else cursor


def run_sync(client, store, catalog_path, mode='fast', locked=False):
    if mode not in ('fast', 'full'):
        raise ValueError('Unknown WB sync mode')
    lock = SyncLock()
    if not locked and not lock.acquire():
        return dict(outcome='skipped', message='Синхронизация WB уже выполняется')
    started = time.monotonic()
    client.sync_deadline = started + (180 if mode == 'fast' else 420)
    client.sync_request_limit = 100 if mode == 'fast' else 300
    client.sync_min_interval = 0.21
    result = dict(added=0, updated=0, received=0, errors=0, statuses_updated=0, recovered=0)
    previous = {}
    failures = []
    completed = 0
    try:
        store.initialize()
        previous = diagnostics(store.path)
        attempt = stamp()
        info = dict(previous, last_attempt_at=attempt, outcome='running', mode=mode)
        save_diagnostics(store, info)
        try:
            result.update(synchronize_wildberries_orders(client, store))
            completed += 1
            if result['errors']:
                failures.append({'error': 'Некорректные данные новых WB-заказов'})
        except WildberriesReadOnlyError as error:
            failures.append({'error': str(error), 'code': error.code})
        ids, cursor = status_ids(store, mode, previous.get('terminal_cursor', ''))
        status_failed = False
        for offset in range(0, len(ids), 100):
            chunk = ids[offset:offset + 100]
            try:
                statuses = client.get_order_statuses(chunk)
                missing = [value for value in chunk if value not in statuses]
                if missing:
                    status_failed = True
                    failures.append({'error': 'WB не вернул статусы', 'order_ids': missing})
                result['statuses_updated'] += store.update_wildberries_statuses(statuses)
                completed += 1
            except WildberriesReadOnlyError as error:
                status_failed = True
                failures.append({'error': str(error), 'code': error.code})
                if error.code in ('WB_SYNC_BUDGET', 'WB_UNAUTHORIZED', 'WB_FORBIDDEN', 'WB_RATE_LIMITED'):
                    break
        if mode == 'full':
            try:
                recovery = WildberriesRecovery(client, store.path, catalog_path).reconcile(store)
                info.update(recovery)
                result['recovered'] = recovery['recovered']
                result['statuses_updated'] += recovery.get('statuses_updated', 0)
                failures.extend(recovery['errors'])
                completed += 1
            except (WildberriesReadOnlyError, ValueError) as error:
                failures.append({'error': str(error)})
        if not status_failed:
            info['terminal_cursor'] = cursor
        result['errors'] = max(result['errors'], len(failures))
        result['outcome'] = 'partial' if failures and completed else 'error' if failures else 'success'
        if not failures:
            info['last_success_at'] = stamp()
        if mode == 'full':
            info['full_outcome'] = result['outcome']
            info['full_errors'] = failures
            info['last_full_attempt_at'] = attempt
            if not failures:
                info['last_full_success_at'] = stamp()
        info.update(outcome=result['outcome'], checked_at=stamp(), errors=failures,
                    new_orders=result['added'], statuses_updated=result['statuses_updated'],
                    recovered=result['recovered'], error_count=result['errors'],
                    duration_seconds=round(time.monotonic()-started, 3),
                    api_requests=len(client.request_audit) if isinstance(getattr(client, 'request_audit', None), list) else 0)
        save_diagnostics(store, info)
        result['recovery'] = info
        if result['outcome'] == 'error':
            result['error'] = failures[0]
        return result
    except Exception as error:
        # Never expose a raw exception/response or silently claim success.
        LOGGER.error('WB_SYNC failed type=%s', type(error).__name__)
        info = dict(previous, last_attempt_at=stamp(), outcome='error', mode=mode,
                    errors=[{'error': 'Ошибка синхронизации: ' + type(error).__name__}], error_count=1)
        try:
            save_diagnostics(store, info)
        except (sqlite3.Error, OSError):
            LOGGER.error('WB_SYNC could not persist failure diagnostics')
        return dict(result, outcome='error', error={'code':'WB_SYNC_FAILED', 'error':info['errors'][0]['error']}, errors=1, recovery=info)
    finally:
        if not locked:
            lock.release()
