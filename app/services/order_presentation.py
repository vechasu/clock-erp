"""Read-only order presentation; never writes or reinterprets source payloads."""
import json

TTT = {'N': ('Не подтверждён', 'attention'), 'A': ('Подтверждён', 'work'),
       'D': ('Собран', 'assembled'), 'C': ('Отказ', 'error'),
       'UNKNOWN': ('Статус не определён', 'neutral')}
ERP_CODES = {'unconfirmed': 'N', 'confirmed': 'A', 'assembled': 'D', 'refused': 'C'}
# WB API: https://dev.wildberries.ru/docs/openapi-other/sandbox-environment
WB = {'NEW': ('Новое задание', 'attention'), 'CONFIRM': ('На сборке', 'work'),
      'COMPLETE': ('В доставке', 'work'), 'SORTED': ('Принят Wildberries', 'work'),
      'READY_FOR_PICKUP': ('В пункте выдачи', 'work'), 'SOLD': ('Получен покупателем', 'success'),
      'CANCEL': ('Отменён продавцом', 'error'), 'CANCELED': ('Отменён Wildberries', 'error'),
      'CANCELED_BY_CLIENT': ('Отказ покупателя', 'error'),
      'DECLINED_BY_CLIENT': ('Отменён покупателем', 'error'),
      'DEFECT': ('Отмена из-за брака', 'error'),
      'UNKNOWN': ('Статус WB не определён', 'neutral')}


def status_key(order, overrides=None):
    if order.get('source') == 'wildberries':
        wb = str(order.get('wb_status') or '').upper()
        supplier = str(order.get('supplier_status') or order.get('status') or '').upper()
        key = wb if wb in WB and wb != 'UNKNOWN' else supplier if wb in ('', 'WAITING') else 'UNKNOWN'
        return 'WB_' + (key if key in WB else 'UNKNOWN')
    oid = str(order.get('id') or order.get('ID') or '')
    saved = (overrides or {}).get(oid)
    if saved in ERP_CODES:
        return ERP_CODES[saved]
    code = str(order.get('status') or '').upper()
    return {'O': 'N', '0': 'N'}.get(code, code if code in TTT else 'UNKNOWN')


def status_label(key):
    return (WB.get(key[3:], WB['UNKNOWN']) if key.startswith('WB_') else TTT.get(key, TTT['UNKNOWN']))[0]


def present_order(order, overrides=None):
    result = dict(order)
    key = status_key(order, overrides)
    label, tone = WB.get(key[3:], WB['UNKNOWN']) if key.startswith('WB_') else TTT.get(key, TTT['UNKNOWN'])
    result.update(ui_status=key, ui_status_label=label, ui_status_tone=tone)
    return result


def navigation_counts(rows, overrides=None, source='all'):
    sources = {'all': 0, 'tictactoy': 0, 'wildberries': 0}
    statuses = {}
    for row in rows:
        origin = 'wildberries' if row.get('source') == 'wildberries' else 'tictactoy'
        sources['all'] += 1
        sources[origin] += 1
        if source not in ('tictactoy', 'wildberries') or source == origin:
            key = status_key(row, overrides)
            statuses[key] = statuses.get(key, 0) + 1
    return sources, statuses


def sqlite_status(payload, overrides):
    return status_key(json.loads(payload), overrides)
