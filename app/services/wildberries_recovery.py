"""Read-only previews and explicit imports using the existing WB order store.

No sale or stock-write service is used. Preview opens SQLite with mode=ro.
"""
import hashlib
import json
import math
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.clients.wildberries_orders import WildberriesReadOnlyError
from app.services.wildberries_matching import order_product_candidates
from app.services.wildberries_orders import normalize_wildberries_order

KNOWN_SUPPLY = "WB-GI-275015200"
STATES = ("READY", "ALREADY_IMPORTED", "ALREADY_SOLD", "PRODUCT_NOT_FOUND",
          "AMBIGUOUS_PRODUCT", "NO_STOCK", "API_ERROR")


def readonly(path):
    connection = sqlite3.connect("file:{}?mode=ro".format(Path(path).resolve()), uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def diagnostics(path):
    with closing(readonly(path)) as connection:
        row = connection.execute("SELECT value FROM orders_snapshot_meta WHERE key='wb_recovery'").fetchone()
    return json.loads(row["value"]) if row else {}


def save_diagnostics(store, data):
    with store.connection() as connection:
        connection.execute("INSERT OR REPLACE INTO orders_snapshot_meta(key,value) VALUES ('wb_recovery',?)",
                           (json.dumps(data, ensure_ascii=False),))


class WildberriesRecovery:
    def __init__(self, client, orders_path, catalog_path, now=None):
        self.client = client
        self.orders_path = Path(orders_path)
        self.catalog_path = Path(catalog_path)
        self.now = int(time.time() if now is None else now)

    def recent_orders(self, days=14):
        if not 1 <= int(days) <= 30:
            raise ValueError("Период проверки должен составлять 1–30 дней")
        return self.client.get_orders(self.now - int(days) * 86400, self.now)

    def classify(self, order_ids, raw_orders, supply_id="", statuses=None, status_error=""):
        rows = []
        by_id = {str(row['id']): row for row in raw_orders}
        statuses = statuses or {}
        with closing(readonly(self.orders_path)) as orders, closing(readonly(self.catalog_path)) as catalog:
            for external_id in map(str, order_ids):
                raw = dict(by_id.get(external_id) or {})
                present = orders.execute("SELECT order_id FROM orders_snapshot WHERE source='wildberries' AND external_order_id=?", (external_id,)).fetchone()
                sale = catalog.execute("SELECT id FROM erp_sales WHERE lower(source)='wildberries' AND external_order_id=? LIMIT 1", (external_id,)).fetchone()
                status = statuses.get(external_id)
                error = ""
                if not raw:
                    error = "WB не вернул данные заказа за выбранный период"
                elif not status:
                    error = status_error or "WB не вернул текущий статус заказа"
                if status:
                    raw.update({key: status[key] for key in ('supplierStatus', 'wbStatus') if key in status})
                if supply_id:
                    raw['supplyId'] = supply_id
                normalized = normalize_wildberries_order(raw) if raw.get('id') else None
                if normalized and (not normalized.get('created_at') or normalized.get('price') is None
                                   or not math.isfinite(normalized['price']) or normalized['price'] < 0):
                    error = error or "WB не вернул исходную дату или цену заказа"
                product = (normalized or {}).get('products', [{}])[0]
                saved = catalog.execute("SELECT product_id FROM erp_order_product_mappings WHERE order_id=? AND order_item_id=?", ('wb:' + external_id, external_id)).fetchone()
                candidates, method = order_product_candidates(catalog, product, saved['product_id'] if saved else None)
                match = candidates[0] if len(candidates) == 1 and candidates[0]['active'] else None
                match_state = 'AMBIGUOUS_PRODUCT' if len(candidates) > 1 else ('READY' if match else 'PRODUCT_NOT_FOUND')
                no_stock = bool(match and float(match['stock'] or 0) <= 0)
                state = ('ALREADY_SOLD' if sale else 'ALREADY_IMPORTED' if present else 'API_ERROR' if error
                         else match_state if match_state != 'READY' else 'NO_STOCK' if no_stock else 'READY')
                rows.append(dict(wb_order_id=external_id, supply_id=supply_id or raw.get('supplyId', ''),
                    article=product.get('article', ''), barcode=product.get('barcode', ''), nm_id=product.get('nm_id'),
                    supplier_status=raw.get('supplierStatus'), wb_status=raw.get('wbStatus'),
                    erp_product=match, candidates=candidates, mapping_method=method,
                    matching_status=match_state, no_stock=no_stock, already_imported=bool(present),
                    sale_id=sale['id'] if sale else None, status=state, error=error, order=normalized))
        return rows

    @staticmethod
    def report(supply_id, ids, rows, expected_count=None, errors=None):
        errors = list(errors or [])
        if expected_count is not None and len(ids) != expected_count:
            errors.append("STOP: ожидалось {} заказов, WB вернул {}".format(expected_count, len(ids)))
        counts = Counter(row['status'] for row in rows)
        result = dict(supply_id=supply_id, wb_count=len(ids), order_ids=list(map(str, ids)), rows=rows,
                      counts={state: counts[state] for state in STATES}, errors=errors,
                      expected_count=expected_count, checked_at=stamp())
        result['importable'] = sum(not row['already_imported'] and not row['sale_id'] and not row['error'] for row in rows)
        result['attention'] = sum(bool(row['error']) or row['matching_status'] != 'READY' or row['no_stock'] for row in rows)
        result['can_import'] = not errors and result['importable'] > 0
        # Compare identities and mapping, not transient checked_at or stock.
        fingerprint = [[row['wb_order_id'], row['article'], row['barcode'], row['nm_id'],
                        [item['id'] for item in row['candidates']], row['sale_id'], row['already_imported'], row['error'],
                        (row['order'] or {}).get('price'), (row['order'] or {}).get('created_at'),
                        row['supplier_status'], row['wb_status']]
                       for row in rows]
        result['digest'] = hashlib.sha256(json.dumps([supply_id, sorted(map(str, ids)), fingerprint],
                                                      sort_keys=True).encode()).hexdigest()
        return result

    def preview_supply(self, supply_id, expected_count=None, days=14):
        if supply_id == KNOWN_SUPPLY:
            expected_count = 8
        supply = self.client.get_supply(supply_id)
        ids = self.client.get_supply_order_ids(supply['id'])
        try:
            raw = self.recent_orders(days)
        except WildberriesReadOnlyError:
            raw = []
        try:
            statuses = self.client.get_order_statuses(ids)
            status_error = ''
        except WildberriesReadOnlyError as error:
            statuses, status_error = {}, str(error)
        rows = self.classify(ids, raw, supply['id'], statuses, status_error)
        result = self.report(supply['id'], ids, rows, expected_count)
        result['supply'] = supply
        result['days'] = days
        return result

    def import_report(self, report, store, actor='WB recovery'):
        if report['errors']:
            raise ValueError('Импорт заблокирован: ' + '; '.join(report['errors']))
        from app.services.audit_journal import AuditJournal
        result = {'imported': 0, 'skipped': 0, 'failed': [], 'order_ids': []}
        for row in report['rows']:
            if row['already_imported'] or row['sale_id']:
                result['skipped'] += 1
                continue
            if row['error'] or not row['order']:
                result['failed'].append({'wb_order_id': row['wb_order_id'], 'error': row['error']})
                continue
            order = dict(row['order'])
            notice = 'Заказ восстановлен из Wildberries после пропущенной синхронизации. Поставка: ' + (row['supply_id'] or 'не указана')
            order.update(recovered_from_wb=True, recovery_notice=notice, recovery_supply_id=row['supply_id'],
                         recovered_at=stamp(), requires_matching=row['matching_status'] != 'READY')
            try:
                store.initialize()
                with store.connection() as connection:
                    # Journal and snapshot commit/rollback together. No inventory writes.
                    connection.execute('ATTACH DATABASE ? AS recovery_catalog', (str(self.catalog_path.resolve()),))
                    connection.execute('BEGIN IMMEDIATE')
                    sold = connection.execute("SELECT id FROM recovery_catalog.erp_sales WHERE lower(source)='wildberries' AND external_order_id=? LIMIT 1", (row['wb_order_id'],)).fetchone()
                    if sold:
                        result['skipped'] += 1
                        continue
                    def journal(target, inserted):
                        AuditJournal().record('order', inserted['id'], 'system_created',
                            'Заказ WB №' + row['wb_order_id'], source='wildberries', actor_type='system',
                            actor_name=actor, metadata={'text_snapshot': notice, 'external_order_id': row['wb_order_id'],
                                                       'supply_id': row['supply_id']}, connection=target)
                    outcome = store.upsert_wildberries([order], only_missing=True, connection=connection, on_insert=journal)
                result['imported'] += outcome['added']
                result['skipped'] += int(not outcome['added'])
                if outcome['added']:
                    result['order_ids'].append(row['wb_order_id'])
            except Exception as error:
                result['failed'].append({'wb_order_id': row['wb_order_id'], 'error': type(error).__name__})
        return result

    def reconcile(self, store, days=14):
        raw = self.recent_orders(days)
        previous_pending = diagnostics(self.orders_path).get('pending') or []
        known_raw = {str(row['id']) for row in raw}
        for pending in previous_pending:
            previous_raw = (pending.get('order') or {}).get('wb_raw')
            if previous_raw and str(previous_raw['id']) not in known_raw:
                raw.append(previous_raw)
        supplies, errors, cursor, seen = [], [], 0, set()
        for _ in range(100):
            page = self.client.get_supplies(limit=1000, next_value=cursor)
            for supply in page['supplies']:
                if not isinstance(supply, dict) or not supply.get('id'):
                    raise WildberriesReadOnlyError('Некорректная поставка WB', 'WB_INVALID_RESPONSE')
                created = str(supply.get('createdAt') or '')
                cutoff = datetime.fromtimestamp(self.now - days * 86400, timezone.utc).isoformat()[:10]
                if created >= cutoff or not supply.get('done'):
                    supplies.append(supply)
            next_value = page.get('next')
            if not page['supplies'] or next_value == 0:
                break
            if type(next_value) is not int or next_value in seen or next_value == cursor:
                raise WildberriesReadOnlyError('Нарушена пагинация поставок WB', 'WB_INVALID_RESPONSE')
            seen.add(cursor)
            cursor = next_value
        else:
            raise WildberriesReadOnlyError('Превышен лимит страниц поставок WB', 'WB_PAGE_LIMIT')
        ids = {str(row['id']) for row in raw}
        ids.update(row['wb_order_id'] for row in previous_pending)
        memberships = {}
        for supply in supplies:
            try:
                values = self.client.get_supply_order_ids(supply['id'])
                if supply['id'] == KNOWN_SUPPLY and len(values) != 8:
                    raise ValueError('STOP: поставка WB-GI-275015200 содержит не 8 заказов')
                memberships[supply['id']] = list(map(str, values))
                ids.update(map(str, values))
            except WildberriesReadOnlyError as error:
                errors.append({'supply_id': supply['id'], 'error': str(error)})
        statuses = {}
        ordered_ids = sorted(ids)
        for offset in range(0, len(ordered_ids), 100):
            try:
                statuses.update(self.client.get_order_statuses(ordered_ids[offset:offset + 100]))
            except WildberriesReadOnlyError as error:
                errors.append({'order_ids': ordered_ids[offset:offset + 100], 'error': str(error)})
        rows = self.classify(ordered_ids, raw, statuses=statuses)
        for row in rows:
            for supply_id, members in memberships.items():
                if row['wb_order_id'] in members:
                    row['supply_id'] = supply_id
                    if row['order']:
                        row['order'].update(supply_id=supply_id, wb_supply_id=supply_id)
        report = self.report('', ordered_ids, rows)
        warnings = []
        for supply_id, members in memberships.items():
            known = sum(row['already_imported'] for row in rows if row['wb_order_id'] in members)
            if known < len(members):
                warnings.append({'supply_id': supply_id, 'wb_count': len(members), 'erp_count': known, 'missing': len(members)-known})
        outcome = self.import_report(report, store)
        return dict(recovered=outcome['imported'], attention=report['attention'] + len(errors) + len(outcome['failed']),
                    supplies=warnings, errors=errors + outcome['failed'],
                    missing=[row['wb_order_id'] for row in rows if not row['already_imported']],
                    pending=[row for row in rows if row['error'] and not row['already_imported'] and not row['sale_id']], checked_at=stamp())
