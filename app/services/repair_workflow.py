"""Read-only repair presentation; never infer a completed repair from location."""
from datetime import date, datetime

QUEUE_LABELS = {
    '': 'Все', 'new': 'Новые', 'attention': 'Нужно действие',
    'master': 'У мастера', 'customer': 'Ждём клиента',
    'transit': 'В пути', 'review': 'Требует уточнения',
}
# Locations are deliberately not rewritten. Some existing transitions preserve
# location (including payment/approval), so both at_us/with_master are legitimate.
STATE_RULES = {
    'new': ({'with_customer', 'at_us'}, 'new', 'Новый', 'request_shipment'),
    'waiting_customer_shipment': ({'with_customer'}, 'customer', 'Ждём отправку клиента', 'mark_customer_sent'),
    'inbound_transit': ({'inbound_transit'}, 'transit', 'В пути к нам', 'receive'),
    'waiting_diagnostics': ({'at_us'}, 'attention', 'Нужно передать мастеру', 'start_diagnostics'),
    'diagnostics': ({'with_master'}, 'master', 'На диагностике у мастера', 'finish_diagnostics'),
    'waiting_decision': ({'at_us', 'with_master'}, 'customer', 'Ждём решения клиента', 'accept_paid'),
    'waiting_payment': ({'at_us', 'with_master'}, 'customer', 'Ждём оплаты клиента', 'record_payment'),
    'in_repair': ({'at_us', 'with_master'}, 'master', 'В ремонте', 'mark_ready'),
    'ready_return': ({'at_us'}, 'attention', 'Готов к возврату', 'send_to_customer'),
    'outbound_transit': ({'outbound_transit'}, 'transit', 'В пути к клиенту', 'complete'),
    'completed': ({'delivered'}, 'completed', 'Завершён', ''),
    'cancelled': (set(), 'cancelled', 'Отменён', ''),
}
ACTION_LABELS = {
    'receive_and_start_diagnostics': 'Передать мастеру',
    'request_shipment': 'Согласовать отправку клиента',
    'mark_customer_sent': 'Клиент отправил товар',
    'receive': 'Принять товар', 'start_diagnostics': 'Передать мастеру',
    'finish_diagnostics': 'Получить результат диагностики',
    'accept_paid': 'Зафиксировать решение клиента',
    'record_payment': 'Зафиксировать оплату',
    'mark_ready': 'Получили готовый товар',
    'send_to_customer': 'Оформить возврат клиенту', 'complete': 'Завершить ремонт',
}
HINTS = {
    'new': 'Согласуйте с клиентом передачу товара.',
    'waiting_customer_shipment': 'Ждём отправку. Отметьте её, когда клиент передаст товар перевозчику.',
    'inbound_transit': 'Товар в пути. Принимайте его только после фактического получения.',
    'waiting_diagnostics': 'Передайте товар мастеру для диагностики.',
    'diagnostics': 'Ждём мастера. Зафиксируйте результат, когда диагностика закончится.',
    'waiting_decision': 'Свяжитесь с клиентом и зафиксируйте его решение о ремонте.',
    'waiting_payment': 'Ждём клиента. Фиксируйте оплату только после её получения.',
    'in_repair': 'Ждём готовый товар. Отметьте готовность после получения товара у нас.',
    'ready_return': 'Выберите выдачу у нас или доставку клиенту.',
    'outbound_transit': 'Дождитесь подтверждения получения товара клиентом.',
    'completed': 'Ремонт завершён. Результат и история сохранены.',
    'cancelled': 'Ремонт отменён. Причина и местонахождение доступны в карточке.',
}


def repair_workflow(case, today=None):
    status = str(case.get('status') or '')
    location = str(case.get('location') or '')
    rule = STATE_RULES.get(status)
    archived = bool(case.get('archived_at'))
    closed = status in {'completed', 'cancelled'}
    conflict = not rule or (status != 'cancelled' and location not in rule[0])
    migration = case.get('migration') or {}
    notes = migration.get('review_notes', []) if isinstance(migration, dict) else []
    if closed and case.get('completion_result') in {'impossible', 'customer_declined'}:
        conflict = False
    conflict = conflict or any('Неизвестный старый статус' in str(note) for note in notes)
    if conflict:
        state, label, action = 'review', 'Требует уточнения', ''
        hint = 'Статус и местонахождение не позволяют определить следующий шаг. Проверьте данные карточки.'
    else:
        _, state, label, action = rule
        hint = HINTS[status]
        if status == 'new' and location == 'at_us':
            state, label, action = 'attention', 'Нужно действие', 'receive_and_start_diagnostics'
            hint = 'Передайте товар мастеру. Кнопка подтвердит приём и передачу на диагностику.'
        if status == 'ready_return' and case.get('return_method') in {'pickup', 'other'}:
            action = 'complete'
    if archived:
        action = ''
        hint = 'Архивная запись. Данные и история доступны для просмотра.'
    overdue_days = 0
    due_today = False
    try:
        due = datetime.strptime(str(case.get('control_date') or '')[:10], '%Y-%m-%d').date()
        due_today = due == (today or date.today())
        overdue_days = max(0, ((today or date.today()) - due).days) if not (closed or archived) else 0
    except ValueError:
        pass
    return {
        'state': state, 'label': label, 'action': action,
        'action_label': ACTION_LABELS.get(action, 'Открыть карточку'),
        'hint': hint, 'needs_review': bool(conflict),
        'needs_attention': not (closed or archived) and (state in {'new', 'attention', 'review'} or overdue_days > 0 or due_today),
        'overdue_days': overdue_days, 'closed': closed,
    }
