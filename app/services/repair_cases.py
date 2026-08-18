import copy
import fcntl
import json
import shutil
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


REPAIR_SCHEMA_VERSION = 4

REPAIR_STATUS_LABELS = {
    "new": "Новая",
    "waiting_customer_shipment": "Ожидаем отправку клиентом",
    "inbound_transit": "В пути к нам",
    "waiting_diagnostics": "Ожидает диагностики",
    "diagnostics": "На диагностике",
    "waiting_decision": "Ожидает решения клиента",
    "waiting_payment": "Ожидается оплата",
    "in_repair": "В ремонте",
    "ready_return": "Готов к возврату",
    "outbound_transit": "В пути к клиенту",
    "completed": "Завершено",
    "cancelled": "Отменено",
}

REPAIR_TYPE_LABELS = {
    "diagnostics": "Диагностика",
    "paid_repair": "Платный ремонт",
    "warranty_repair": "Гарантийный ремонт",
    "repeat_repair": "Повторный ремонт",
}

REPAIR_LOCATION_LABELS = {
    "with_customer": "У клиента",
    "inbound_transit": "В пути к нам",
    "at_us": "У нас",
    "with_master": "У мастера/в сервисе",
    "outbound_transit": "В пути к клиенту",
    "delivered": "Выдан клиенту",
    "unknown": "Неизвестно",
}

REPAIR_CHANNEL_LABELS = {
    "phone": "Телефон",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "email": "Почта",
    "cdek": "СДЭК",
    "other": "Другое",
}

SHIPMENT_DIRECTION_LABELS = {
    "inbound": "К нам",
    "outbound": "Клиенту",
    "unknown": "Требует уточнения",
}

REPAIR_RESPONSIBILITY_LABELS = {
    "us": "От нас",
    "customer": "От клиента",
    "delivery": "От доставки",
}

REPAIR_RESPONSIBILITY_GROUPS = {
    "new": "us",
    "waiting_customer_shipment": "customer",
    "inbound_transit": "delivery",
    "waiting_diagnostics": "us",
    "diagnostics": "us",
    "waiting_decision": "customer",
    "waiting_payment": "customer",
    "in_repair": "us",
    "ready_return": "us",
    "outbound_transit": "delivery",
    "completed": "completed",
    "cancelled": "cancelled",
}

RETURN_METHOD_LABELS = {
    "cdek": "СДЭК",
    "pickup": "Самовывоз",
    "courier": "Курьер",
    "other": "Другое",
}

COMPLETION_RESULT_LABELS = {
    "repaired": "Отремонтировано",
    "replaced": "Заменено",
    "returned_unrepaired": "Возвращено без ремонта",
    "impossible": "Ремонт невозможен",
    "customer_declined": "Клиент отказался от ремонта",
    "other": "Другое",
}

REPAIR_ACTION_LABELS = {
    "add_incoming_waybill": "Добавить входящую накладную",
    "request_shipment": "Ожидать отправку клиентом",
    "mark_customer_sent": "Отметить отправку клиентом",
    "receive": "Принять товар",
    "start_diagnostics": "Передать на диагностику",
    "finish_diagnostics": "Зафиксировать диагностику",
    "request_decision": "Запросить решение клиента",
    "accept_paid": "Зафиксировать решение клиента",
    "accept_free": "Согласовать бесплатный ремонт",
    "record_payment": "Зафиксировать оплату",
    "start_repair": "Начать ремонт",
    "mark_ready": "Отметить готовность",
    "send_to_customer": "Отправить клиенту",
    "complete": "Завершить",
    "cancel": "Отменить",
    "reopen": "Возобновить",
}

REPAIR_TRANSITIONS = {
    "add_incoming_waybill": (
        set(REPAIR_STATUS_LABELS) - {"completed", "cancelled"},
        None,
    ),
    "request_shipment": ({"new"}, "waiting_customer_shipment"),
    "mark_customer_sent": ({"waiting_customer_shipment"}, "inbound_transit"),
    "receive": ({"new", "inbound_transit"}, "waiting_diagnostics"),
    "start_diagnostics": ({"waiting_diagnostics"}, "diagnostics"),
    "finish_diagnostics": ({"diagnostics"}, "waiting_decision"),
    "request_decision": ({"diagnostics"}, "waiting_decision"),
    "accept_paid": ({"waiting_decision"}, "waiting_payment"),
    "accept_free": ({"waiting_decision"}, "in_repair"),
    "record_payment": ({"waiting_payment"}, "in_repair"),
    "start_repair": ({"waiting_decision"}, "in_repair"),
    "mark_ready": ({"in_repair"}, "ready_return"),
    "send_to_customer": ({"ready_return"}, "outbound_transit"),
    "complete": ({"ready_return", "outbound_transit"}, "completed"),
    "cancel": (set(REPAIR_STATUS_LABELS) - {"completed", "cancelled"}, "cancelled"),
    "reopen": ({"completed", "cancelled"}, "new"),
}

LEGACY_STATUS_MAP = {
    "done": "completed",
    "at_us": "waiting_diagnostics",
    "waiting_master": "waiting_diagnostics",
    "at_master": "diagnostics",
    "waiting": "waiting_decision",
    "in_progress": "in_repair",
    "ready": "ready_return",
    "issued": "completed",
}

LEGACY_TYPE_MAP = {
    "paid": "paid_repair",
    "warranty": "warranty_repair",
    "service": "diagnostics",
}

STATUS_LOCATION_MAP = {
    "new": "with_customer",
    "waiting_customer_shipment": "with_customer",
    "inbound_transit": "inbound_transit",
    "waiting_diagnostics": "at_us",
    "diagnostics": "with_master",
    "waiting_decision": "with_master",
    "waiting_payment": "at_us",
    "in_repair": "with_master",
    "ready_return": "at_us",
    "outbound_transit": "outbound_transit",
    "completed": "delivered",
    "cancelled": "unknown",
}

DEFAULT_FIELDS = {
    "repair_number": "",
    "created_at": "",
    "updated_at": "",
    "archived_at": "",
    "archived_by": "",
    "responsible": "",
    "order_number": "",
    "order_id": "",
    "order_item_id": "",
    "order_item_name": "",
    "order_source": "none",
    "client_name": "",
    "client_phone": "",
    "client_email": "",
    "client_messenger": "",
    "product_id": "",
    "product_name": "",
    "brand": "",
    "model": "",
    "article": "",
    "serial_number": "",
    "product_url": "",
    "product_image_url": "",
    "communication_channel": "other",
    "contact": "",
    "problem": "",
    "problem_details": "",
    "external_condition": "",
    "note": "",
    "diagnostic_result": "",
    "proposed_solution": "",
    "customer_decision": "",
    "agreed_cost": "",
    "payment_amount": "",
    "paid_at": "",
    "work_result": "",
    "completion_result": "",
    "completion_comment": "",
    "cancellation_reason": "",
    "incoming_waybill": "",
    "return_method": "",
    "outgoing_waybill": "",
    "next_action": "",
    "waiting_for": "us",
    "control_date": "",
    "parent_repair_id": "",
    "repeat_repair_id": "",
    "completed_at": "",
    "cancelled_at": "",
    "created_by": "",
    "updated_by": "",
    "last_idempotency_key": "",
    "location": "unknown",
    "request_at": "",
    "customer_sent_at": "",
    "accepted_at": "",
    "master_handoff_at": "",
    "repair_completed_at": "",
    "returned_at": "",
    "due_date": "",
    "master": "",
    "equipment": "",
    "communication": "",
    "internal_comment": "",
}


class RepairDataError(RuntimeError):
    pass


def repair_now():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _text(value):
    return str(value or "").strip()


def _date_part(value):
    return _text(value)[:10]


def _stable_event_id(case_id, index, event):
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"repair:{case_id}:{index}:{payload}"))


def make_history_event(
    action,
    actor="Система",
    comment="",
    field="",
    old_value="",
    new_value="",
    timestamp=None,
):
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": timestamp or repair_now(),
        "actor": _text(actor) or "Система",
        "action": _text(action) or "Изменение",
        "field": _text(field),
        "old_value": _text(old_value),
        "new_value": _text(new_value),
        "comment": _text(comment),
    }
    return event


def append_history_event(case, *args, **kwargs):
    history = case.setdefault("history", [])
    history.append(make_history_event(*args, **kwargs))


def normalize_money(value, field_label="Сумма"):
    text = _text(value).replace(" ", "").replace(",", ".")
    if not text:
        return ""
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"{field_label}: укажите корректное число") from error
    if amount < 0:
        raise ValueError(f"{field_label} не может быть отрицательной")
    return format(amount.quantize(Decimal("0.01")), "f")


def normalize_date(value, field_label="Дата"):
    text = _date_part(value)
    if not text:
        return ""
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{field_label}: укажите корректную дату") from error
    return text


def available_repair_actions(case):
    if _text(case.get("archived_at")):
        return []
    status = _text(case.get("status"))
    return [
        action
        for action, (allowed, _target) in REPAIR_TRANSITIONS.items()
        if status in allowed
    ]


def repair_attention_key(case, today=None):
    """Stable business sorting: overdue, our action, today, active, closed."""
    today = today or date.today().isoformat()
    status = _text(case.get("status"))
    control = _date_part(case.get("control_date"))
    active = status not in {"completed", "cancelled"}
    overdue = active and bool(control) and control < today
    ours = active and _text(case.get("waiting_for")) == "us"
    due_today = active and control == today
    if overdue:
        group = 0
    elif ours:
        group = 1
    elif due_today:
        group = 2
    elif active:
        group = 3
    elif status == "completed":
        group = 4
    else:
        group = 5
    return (
        group,
        control or "9999-12-31",
        _text(case.get("created_at")),
        _text(case.get("id")),
    )


def apply_repair_action(case, action, payload, actor="Система"):
    """Validate and atomically mutate one repair workflow state."""
    if _text(case.get("archived_at")):
        raise ValueError("Архивный ремонт доступен только для просмотра")
    if action not in REPAIR_TRANSITIONS:
        raise ValueError("Неизвестное действие ремонта")
    payload = payload if isinstance(payload, dict) else {}
    idempotency_key = _text(payload.get("idempotency_key"))
    if idempotency_key and idempotency_key == _text(
        case.get("last_idempotency_key")
    ):
        return False
    before = copy.deepcopy(case)
    status = _text(case.get("status"))
    allowed, target = REPAIR_TRANSITIONS[action]
    if status not in allowed:
        raise ValueError("Действие недоступно на текущем этапе")
    target = target or status

    reason = _text(payload.get("reason"))
    old_incoming_waybill = _text(case.get("incoming_waybill"))

    if action == "add_incoming_waybill":
        incoming = _text(
            payload.get("incoming_waybill") or case.get("incoming_waybill")
        )
        if not incoming:
            raise ValueError("Укажите входящую накладную")
        case["incoming_waybill"] = incoming

    if action in {"finish_diagnostics", "request_decision"}:
        diagnostic = _text(
            payload.get("diagnostic_result") or case.get("diagnostic_result")
        )
        solution = _text(
            payload.get("proposed_solution") or case.get("proposed_solution")
        )
        if not diagnostic:
            raise ValueError("Укажите результат диагностики")
        if not solution:
            raise ValueError("Укажите предложенное решение")
        case["diagnostic_result"] = diagnostic
        case["proposed_solution"] = solution

    if action in {"accept_paid", "accept_free"}:
        decision = _text(
            payload.get("customer_decision") or case.get("customer_decision")
        )
        if not decision:
            raise ValueError("Укажите решение клиента")
        case["customer_decision"] = decision
        if action == "accept_paid":
            case["agreed_cost"] = normalize_money(
                payload.get("agreed_cost") or case.get("agreed_cost"),
                "Согласованная стоимость",
            )
            if not case["agreed_cost"]:
                raise ValueError("Укажите согласованную стоимость")

    if action == "record_payment":
        amount = normalize_money(
            payload.get("payment_amount") or case.get("agreed_cost"),
            "Сумма оплаты",
        )
        if not amount:
            raise ValueError("Укажите сумму оплаты")
        case["payment_amount"] = amount
        case["paid_at"] = repair_now()

    if action == "send_to_customer":
        return_method = _text(
            payload.get("return_method") or case.get("return_method")
        )
        if return_method not in RETURN_METHOD_LABELS:
            raise ValueError("Выберите способ возврата")
        outgoing = _text(
            payload.get("outgoing_waybill") or case.get("outgoing_waybill")
        )
        if return_method in {"cdek", "courier"} and not outgoing:
            raise ValueError("Укажите исходящую накладную")
        case["return_method"] = return_method
        case["outgoing_waybill"] = outgoing

    if action == "complete":
        if status == "ready_return" and _text(
            payload.get("return_method") or case.get("return_method")
        ) not in {"pickup", "other"}:
            raise ValueError("Для доставки сначала отметьте отправку клиенту")
        result = _text(
            payload.get("completion_result") or case.get("completion_result")
        )
        if result not in COMPLETION_RESULT_LABELS:
            raise ValueError("Выберите результат завершения")
        case["completion_result"] = result
        case["work_result"] = _text(
            payload.get("work_result") or case.get("work_result")
        )
        case["completion_comment"] = _text(payload.get("comment"))
        case["completed_at"] = repair_now()

    if action == "cancel":
        if not reason:
            raise ValueError("Укажите причину отмены")
        case["cancellation_reason"] = reason
        case["cancelled_at"] = repair_now()

    if action == "reopen":
        if not reason:
            raise ValueError("Укажите причину возобновления")
        requested_status = _text(payload.get("status")) or "new"
        if requested_status in {"completed", "cancelled"} or requested_status not in REPAIR_STATUS_LABELS:
            raise ValueError("Выберите активный статус")
        target = requested_status
        case["completed_at"] = ""
        case["cancelled_at"] = ""
        case["cancellation_reason"] = ""

    next_action = _text(payload.get("next_action"))
    waiting_for = _text(payload.get("waiting_for"))
    control_date = normalize_date(payload.get("control_date"), "Контрольная дата")
    if target not in {"completed", "cancelled"}:
        if not next_action:
            raise ValueError("Укажите следующее действие")
        if waiting_for not in REPAIR_RESPONSIBILITY_LABELS:
            raise ValueError("Укажите, от кого ожидается действие")
        if not control_date:
            raise ValueError("Укажите контрольную дату")
        case["next_action"] = next_action
        case["waiting_for"] = waiting_for
        case["control_date"] = control_date
    else:
        case["next_action"] = ""
        case["control_date"] = ""

    old_status = status
    old_location = _text(case.get("location"))
    case["status"] = target
    action_locations = {
        "request_shipment": "with_customer",
        "mark_customer_sent": "inbound_transit",
        "receive": "at_us",
        "start_diagnostics": "with_master",
        "start_repair": "with_master",
        "mark_ready": "at_us",
        "send_to_customer": "outbound_transit",
        "complete": "delivered",
    }
    if action in action_locations:
        case["location"] = action_locations[action]
    case["updated_at"] = repair_now()
    case["updated_by"] = actor
    case["last_idempotency_key"] = idempotency_key
    if action == "add_incoming_waybill":
        append_history_event(
            case,
            REPAIR_ACTION_LABELS[action],
            actor=actor,
            field="incoming_waybill",
            old_value=old_incoming_waybill,
            new_value=case["incoming_waybill"],
            comment=reason or _text(payload.get("comment")),
        )
    else:
        append_history_event(
            case,
            REPAIR_ACTION_LABELS[action],
            actor=actor,
            field="status",
            old_value=REPAIR_STATUS_LABELS.get(old_status, old_status),
            new_value=REPAIR_STATUS_LABELS[target],
            comment=reason or _text(payload.get("comment")),
        )
    if old_location != _text(case.get("location")):
        append_history_event(
            case,
            "Изменено местонахождение",
            actor=actor,
            field="location",
            old_value=REPAIR_LOCATION_LABELS.get(old_location, old_location),
            new_value=REPAIR_LOCATION_LABELS.get(
                case.get("location"), case.get("location")
            ),
        )
    field_labels = {
        "diagnostic_result": "Результат диагностики",
        "proposed_solution": "Предложенное решение",
        "customer_decision": "Решение клиента",
        "agreed_cost": "Согласованная стоимость",
        "payment_amount": "Сумма оплаты",
        "paid_at": "Дата оплаты",
        "incoming_waybill": "Входящая накладная",
        "return_method": "Способ возврата",
        "outgoing_waybill": "Исходящая накладная",
        "work_result": "Итог выполненных работ",
        "completion_result": "Результат завершения",
        "cancellation_reason": "Причина отмены",
        "next_action": "Следующее действие",
        "waiting_for": "Ожидаем действие",
        "control_date": "Контрольная дата",
    }
    for field, label in field_labels.items():
        old_value = _text(before.get(field))
        new_value = _text(case.get(field))
        if old_value == new_value:
            continue
        if action == "add_incoming_waybill" and field == "incoming_waybill":
            continue
        append_history_event(
            case,
            f"Изменено поле «{label}»",
            actor=actor,
            field=field,
            old_value=old_value,
            new_value=new_value,
        )
    return True


def _normalize_existing_history(case_id, history):
    normalized = []
    for index, source in enumerate(history if isinstance(history, list) else []):
        if not isinstance(source, dict):
            continue
        event = {
            "id": _text(source.get("id")),
            "timestamp": _text(
                source.get("timestamp")
                or source.get("created_at")
                or source.get("date")
            ),
            "actor": _text(
                source.get("actor")
                or source.get("user")
                or source.get("responsible")
            )
            or "Система",
            "action": _text(
                source.get("action")
                or source.get("type")
                or source.get("event")
            )
            or "Комментарий",
            "field": _text(source.get("field")),
            "old_value": _text(
                source.get("old_value")
                if "old_value" in source
                else source.get("old")
            ),
            "new_value": _text(
                source.get("new_value")
                if "new_value" in source
                else source.get("new")
            ),
            "comment": _text(
                source.get("comment")
                or source.get("message")
                or source.get("text")
            ),
        }
        if not event["id"]:
            event["id"] = _stable_event_id(case_id, index, event)
        normalized.append(event)
    return normalized


def _normalize_shipment(source, direction="unknown"):
    source = source if isinstance(source, dict) else {}
    normalized_direction = _text(
        source.get("direction") or direction
    ).lower()
    direction_aliases = {
        "to_us": "inbound",
        "incoming": "inbound",
        "к нам": "inbound",
        "to_client": "outbound",
        "outgoing": "outbound",
        "клиенту": "outbound",
    }
    normalized_direction = direction_aliases.get(
        normalized_direction,
        normalized_direction,
    )
    if normalized_direction not in SHIPMENT_DIRECTION_LABELS:
        normalized_direction = "unknown"
    return {
        "id": _text(source.get("id")) or str(uuid.uuid4()),
        "direction": normalized_direction,
        "carrier": _text(
            source.get("carrier")
            or source.get("delivery_service")
            or source.get("service")
        ),
        "track_number": _text(
            source.get("track_number")
            or source.get("tracking_number")
            or source.get("waybill")
            or source.get("invoice_number")
        ),
        "sent_at": _date_part(
            source.get("sent_at")
            or source.get("shipped_at")
            or source.get("date")
        ),
        "status": _text(source.get("status")),
        "received_at": _date_part(
            source.get("received_at")
            or source.get("delivered_at")
        ),
    }


def _legacy_shipments(case):
    result = []
    known_fields = (
        ("incoming_track_number", "inbound"),
        ("inbound_track_number", "inbound"),
        ("return_track_number", "outbound"),
        ("outgoing_track_number", "outbound"),
    )
    for field, direction in known_fields:
        track_number = _text(case.get(field))
        if track_number:
            result.append(_normalize_shipment({
                "track_number": track_number,
                "carrier": case.get("carrier") or case.get("delivery_service"),
            }, direction))

    generic_track = _text(
        case.get("track_number")
        or case.get("tracking_number")
        or case.get("waybill")
        or case.get("invoice_number")
    )
    if generic_track and not result:
        result.append(_normalize_shipment({
            "track_number": generic_track,
            "carrier": case.get("carrier") or case.get("delivery_service"),
            "direction": case.get("delivery_direction"),
        }))
    return result


def migrate_repair_case(source, migrated_at=None):
    if not isinstance(source, dict):
        raise RepairDataError("Запись ремонта должна быть объектом")

    migrated_at = migrated_at or repair_now()
    original = copy.deepcopy(source)
    case = copy.deepcopy(source)
    case_id = _text(case.get("id")) or str(uuid.uuid4())
    case["id"] = case_id
    previous_version = int(case.get("schema_version") or 1)
    mapped_fields = 0
    review_notes = list(
        case.get("migration", {}).get("review_notes", [])
        if isinstance(case.get("migration"), dict)
        else []
    )

    original_status = _text(case.get("status")) or "new"
    status = LEGACY_STATUS_MAP.get(original_status, original_status)
    if status not in REPAIR_STATUS_LABELS:
        status = "new"
        review_notes.append(
            f"Неизвестный старый статус «{original_status}» заменён на «Новая»"
        )
    if status != original_status:
        mapped_fields += 1
    case["status"] = status

    original_type = _text(
        case.get("request_type") or case.get("repair_type")
    ) or "paid_repair"
    request_type = LEGACY_TYPE_MAP.get(original_type, original_type)
    request_type = {
        "repeat_diagnostics": "repeat_repair",
        "repair_completed": "paid_repair",
        "surcharge_accessories": "paid_repair",
    }.get(request_type, request_type)
    if request_type not in REPAIR_TYPE_LABELS:
        request_type = "paid_repair"
        review_notes.append(
            f"Неизвестный тип обращения «{original_type}» требует проверки"
        )
    if request_type != original_type:
        mapped_fields += 1
    case["request_type"] = request_type
    case["repair_type"] = request_type

    for field, default in DEFAULT_FIELDS.items():
        if field not in case or case[field] is None:
            case[field] = default

    case["created_at"] = _text(case.get("created_at")) or migrated_at
    case["updated_at"] = (
        _text(case.get("updated_at")) or case["created_at"]
    )
    case["problem"] = _text(
        case.get("problem") or case.get("comment")
    )

    order_number = _text(case.get("order_number"))
    order_source = _text(case.get("order_source")).lower()
    if order_source not in {"our", "external", "none"}:
        legacy_our_order = case.get("is_our_order", case.get("our_order"))
        if legacy_our_order in {True, 1, "1", "true", "yes", "да"}:
            order_source = "our"
        elif order_number:
            order_source = "external"
            review_notes.append(
                "Источник старого заказа не указан; временно отмечен как внешний"
            )
        else:
            order_source = "none"
        mapped_fields += 1
    if not order_number:
        order_source = "none"
    case["order_source"] = order_source

    channel = _text(
        case.get("communication_channel") or case.get("channel")
    ).lower()
    channel_aliases = {
        "телефон": "phone",
        "почта": "email",
        "сдэк": "cdek",
        "другое": "other",
    }
    channel = channel_aliases.get(channel, channel)
    if channel not in REPAIR_CHANNEL_LABELS:
        channel = "phone" if _text(case.get("client_phone")) else "other"
    case["communication_channel"] = channel
    case["contact"] = _text(case.get("contact")) or {
        "phone": _text(case.get("client_phone")),
        "email": _text(case.get("client_email")),
        "telegram": _text(case.get("client_messenger")),
        "whatsapp": _text(case.get("client_messenger")),
    }.get(channel, "")

    location = _text(case.get("location")).lower()
    location = {
        "with_customer_returned": "delivered",
    }.get(location, location)
    if location not in REPAIR_LOCATION_LABELS:
        location = STATUS_LOCATION_MAP.get(status, "unknown")
        mapped_fields += 1
    case["location"] = location

    compatibility_fields = {
        "problem_details": ("communication",),
        "note": ("internal_comment",),
        "proposed_solution": ("decision", "master_conclusion"),
        "agreed_cost": ("final_cost", "estimate_cost"),
        "control_date": ("due_date",),
        "completed_at": ("repair_completed_at",),
    }
    for target, sources in compatibility_fields.items():
        if _text(case.get(target)):
            continue
        value = next(
            (_text(case.get(source)) for source in sources if _text(case.get(source))),
            "",
        )
        if value:
            case[target] = value
            if previous_version < REPAIR_SCHEMA_VERSION:
                mapped_fields += 1
    case["order_id"] = _text(case.get("order_id") or case.get("order_number"))
    case["order_item_id"] = _text(case.get("order_item_id"))
    case["order_item_name"] = _text(
        case.get("order_item_name") or case.get("product_name")
    )
    case["waiting_for"] = _text(case.get("waiting_for"))
    if case["waiting_for"] not in REPAIR_RESPONSIBILITY_LABELS:
        inferred = REPAIR_RESPONSIBILITY_GROUPS.get(status, "us")
        case["waiting_for"] = inferred if inferred in REPAIR_RESPONSIBILITY_LABELS else "us"
    if status in {"completed", "cancelled"}:
        case["next_action"] = ""
        case["control_date"] = ""

    request_at = case.get("request_at")
    if not request_at and previous_version < REPAIR_SCHEMA_VERSION:
        request_at = case["created_at"]
    case["request_at"] = _date_part(request_at)
    for field in (
        "customer_sent_at",
        "accepted_at",
        "master_handoff_at",
        "repair_completed_at",
        "returned_at",
        "due_date",
        "control_date",
    ):
        case[field] = _date_part(case.get(field))

    shipments = case.get("shipments")
    if isinstance(shipments, list):
        case["shipments"] = [
            _normalize_shipment(shipment)
            for shipment in shipments
            if isinstance(shipment, dict)
        ]
    else:
        case["shipments"] = _legacy_shipments(case)
        if case["shipments"]:
            mapped_fields += len(case["shipments"])
    if any(
        shipment["direction"] == "unknown"
        for shipment in case["shipments"]
    ):
        review_notes.append(
            "Направление одной или нескольких старых накладных требует уточнения"
        )

    attachments = case.get("attachments")
    case["attachments"] = [
        attachment
        for attachment in attachments
        if isinstance(attachment, dict)
    ] if isinstance(attachments, list) else []

    history = _normalize_existing_history(case_id, case.get("history"))
    if not history:
        history.append(make_history_event(
            "Карточка создана",
            actor=_text(case.get("responsible")) or "Система",
            comment="Исходная запись ремонта сохранена",
            timestamp=case["created_at"],
        ))
        mapped_fields += 1

    legacy_comments = (
        ("История общения", _text(case.get("communication"))),
        ("Внутренний комментарий", _text(case.get("internal_comment"))),
        ("Старый комментарий", _text(original.get("comment"))),
    )
    existing_comments = {
        _text(event.get("comment"))
        for event in history
        if isinstance(event, dict)
    }
    for action, comment in legacy_comments:
        if comment and comment not in existing_comments:
            history.append(make_history_event(
                action,
                actor=_text(case.get("responsible")) or "Система",
                comment=comment,
                timestamp=case["updated_at"],
            ))
            existing_comments.add(comment)
            mapped_fields += 1
    case["history"] = history

    case["schema_version"] = REPAIR_SCHEMA_VERSION
    case["migration"] = {
        "from_version": previous_version,
        "migrated_at": (
            _text(case.get("migration", {}).get("migrated_at"))
            if isinstance(case.get("migration"), dict)
            else ""
        ) or migrated_at,
        "mapped_fields": mapped_fields,
        "requires_review": bool(review_notes),
        "review_notes": list(dict.fromkeys(review_notes)),
    }
    case["legacy_snapshot"] = (
        case.get("legacy_snapshot")
        if isinstance(case.get("legacy_snapshot"), dict)
        else original
    )
    return case


def migrate_repair_cases(cases, migrated_at=None):
    if not isinstance(cases, list):
        raise RepairDataError("Хранилище ремонтов должно содержать список")

    migrated_at = migrated_at or repair_now()
    normalized = []
    report = {
        "schema_version": REPAIR_SCHEMA_VERSION,
        "total_records": len(cases),
        "migrated_records": 0,
        "mapped_fields": 0,
        "review_required": 0,
        "errors": [],
    }
    for index, source in enumerate(cases):
        try:
            previous_version = (
                int(source.get("schema_version") or 1)
                if isinstance(source, dict)
                else 1
            )
            case = migrate_repair_case(source, migrated_at=migrated_at)
        except Exception as error:
            report["errors"].append({
                "index": index,
                "error": str(error),
            })
            continue
        normalized.append(case)
        if previous_version < REPAIR_SCHEMA_VERSION:
            report["migrated_records"] += 1
        report["mapped_fields"] += int(
            case.get("migration", {}).get("mapped_fields") or 0
        )
        if case.get("migration", {}).get("requires_review"):
            report["review_required"] += 1
    if report["errors"]:
        raise RepairDataError(
            "Миграция остановлена: не удалось обработать "
            f"{len(report['errors'])} записей"
        )
    return normalized, report


def load_repair_file(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RepairDataError(
            f"Не удалось прочитать хранилище ремонтов: {error}"
        ) from error
    cases, _report = migrate_repair_cases(raw)
    return cases


def save_repair_file(path, cases):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized, _report = migrate_repair_cases(cases)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as error:
        raise RepairDataError(
            f"Не удалось сохранить хранилище ремонтов: {error}"
        ) from error


def mutate_repair_file(path, callback):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        cases = load_repair_file(path)
        result = callback(cases)
        save_repair_file(path, cases)
        return result


def migrate_repair_file(path, apply=False, backup_dir=None):
    path = Path(path)
    if not path.exists():
        source = []
    else:
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RepairDataError(
                f"Не удалось прочитать хранилище ремонтов: {error}"
            ) from error
    migrated_at = repair_now()
    normalized, report = migrate_repair_cases(
        source,
        migrated_at=migrated_at,
    )
    report["applied"] = bool(apply)
    report["backup_path"] = ""
    if not apply:
        return report

    backup_directory = Path(backup_dir) if backup_dir else path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if path.exists():
        backup_path = (
            backup_directory / f"repair_cases-{timestamp}.json"
        )
        shutil.copy2(path, backup_path)
        report["backup_path"] = str(backup_path)
    save_repair_file(path, normalized)
    report_path = path.parent / "repair_migration_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def latest_repair_event(case):
    history = case.get("history")
    if isinstance(history, list) and history:
        event = history[-1]
        if isinstance(event, dict):
            return (
                _text(event.get("comment"))
                or _text(event.get("new_value"))
                or _text(event.get("action"))
            )
    next_steps = {
        "new": "Зафиксировать способ отправки часов",
        "waiting_customer_shipment": "Ожидаем отправку клиентом",
        "inbound_transit": "Часы в пути к нам",
        "at_us": "Часы получены",
        "waiting_master": "Ожидают передачи мастеру",
        "at_master": "Ожидаем решение мастера",
        "waiting_decision": "Нужно согласовать решение",
        "waiting_payment": "Нужно получить оплату",
        "outbound_transit": "Часы отправлены клиенту",
        "completed": "Ремонт завершён",
    }
    return next_steps.get(case.get("status"), "Требуется уточнение")
