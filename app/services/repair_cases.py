import copy
import fcntl
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path


REPAIR_SCHEMA_VERSION = 2

REPAIR_STATUS_LABELS = {
    "new": "Новая",
    "waiting_customer_shipment": "Ожидаем отправку клиентом",
    "inbound_transit": "В пути к нам",
    "at_us": "У нас",
    "waiting_master": "Ожидает передачи мастеру",
    "at_master": "У мастера",
    "waiting_decision": "Ожидает решения",
    "waiting_payment": "Ожидается оплата",
    "outbound_transit": "В пути к клиенту",
    "completed": "Завершено",
}

REPAIR_TYPE_LABELS = {
    "diagnostics": "Диагностика",
    "paid_repair": "Платный ремонт",
    "warranty_repair": "Гарантийный ремонт",
    "repeat_repair": "Повторный ремонт",
    "repeat_diagnostics": "Повторная диагностика / замена",
    "repair_completed": "Ремонт завершён",
    "surcharge_accessories": "Доплата / аксессуары",
}

REPAIR_LOCATION_LABELS = {
    "with_customer": "У клиента",
    "inbound_transit": "В пути к нам",
    "at_us": "У нас",
    "with_master": "У мастера",
    "outbound_transit": "В пути к клиенту",
    "with_customer_returned": "У клиента",
    "unknown": "Требует уточнения",
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

LEGACY_STATUS_MAP = {
    "done": "completed",
    "diagnostics": "at_us",
    "waiting": "waiting_decision",
    "in_progress": "at_master",
    "ready": "at_us",
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
    "at_us": "at_us",
    "waiting_master": "at_us",
    "at_master": "with_master",
    "waiting_decision": "with_master",
    "waiting_payment": "at_us",
    "outbound_transit": "outbound_transit",
    "completed": "with_customer_returned",
}

DEFAULT_FIELDS = {
    "repair_number": "",
    "created_at": "",
    "updated_at": "",
    "archived_at": "",
    "responsible": "",
    "order_number": "",
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
    "diagnostic_result": "",
    "master_conclusion": "",
    "decision": "",
    "estimate_cost": "",
    "final_cost": "",
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
    if location not in REPAIR_LOCATION_LABELS:
        location = STATUS_LOCATION_MAP.get(status, "unknown")
        mapped_fields += 1
    case["location"] = location

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

    if (
        previous_version < REPAIR_SCHEMA_VERSION
        and status == "completed"
        and not _text(case.get("archived_at"))
    ):
        case["archived_at"] = case["updated_at"]

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
