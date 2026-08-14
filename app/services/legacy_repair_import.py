import copy
import fcntl
import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from app.services.repair_cases import (
    LEGACY_STATUS_MAP,
    REPAIR_CHANNEL_LABELS,
    REPAIR_LOCATION_LABELS,
    REPAIR_SCHEMA_VERSION,
    REPAIR_STATUS_LABELS,
    REPAIR_TYPE_LABELS,
    SHIPMENT_DIRECTION_LABELS,
    append_history_event,
    load_repair_file,
    make_history_event,
    migrate_repair_case,
    save_repair_file,
)


IMPORT_ACTOR = "Импорт старого Excel"


class LegacyRepairImportError(RuntimeError):
    pass


def _text(value):
    return str(value or "").strip()


def _canonical(value):
    return re.sub(r"\s+", " ", _text(value).casefold().replace("ё", "е"))


def _tracks(case):
    return {
        _text(shipment.get("track_number"))
        for shipment in case.get("shipments", [])
        if isinstance(shipment, dict) and _text(shipment.get("track_number"))
    }


def _source_key(case):
    metadata = case.get("legacy_import")
    if not isinstance(metadata, dict):
        return ""
    return _text(metadata.get("source_key"))


def load_import_dataset(path):
    path = Path(path)
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LegacyRepairImportError(
            f"Не удалось прочитать набор импорта: {error}"
        ) from error
    validate_import_dataset(dataset)
    return dataset


def validate_import_dataset(dataset):
    if not isinstance(dataset, dict):
        raise LegacyRepairImportError("Набор импорта должен быть объектом")
    batch_key = _text(dataset.get("batch_key"))
    source_url = _text(dataset.get("source_url"))
    records = dataset.get("records")
    if not batch_key or not source_url or not isinstance(records, list):
        raise LegacyRepairImportError(
            "В наборе импорта обязательны batch_key, source_url и records"
        )

    source_keys = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise LegacyRepairImportError(
                f"Запись импорта {index} должна быть объектом"
            )
        source_key = _text(record.get("source_key"))
        fields = record.get("fields")
        shipments = record.get("shipments")
        if not source_key or source_key in source_keys:
            raise LegacyRepairImportError(
                f"Пустой или повторяющийся source_key: {source_key or index}"
            )
        source_keys.add(source_key)
        if not isinstance(fields, dict) or not isinstance(shipments, list):
            raise LegacyRepairImportError(
                f"У записи {source_key} обязательны fields и shipments"
            )
        choices = (
            ("status", set(REPAIR_STATUS_LABELS) | set(LEGACY_STATUS_MAP)),
            (
                "request_type",
                set(REPAIR_TYPE_LABELS)
                | {"repeat_diagnostics", "repair_completed", "surcharge_accessories"},
            ),
            ("location", set(REPAIR_LOCATION_LABELS) | {"with_customer_returned"}),
            ("communication_channel", REPAIR_CHANNEL_LABELS),
        )
        for field, allowed in choices:
            if _text(fields.get(field)) not in allowed:
                raise LegacyRepairImportError(
                    f"Некорректное поле {field} у записи {source_key}"
                )
        if not isinstance(fields.get("order_number", ""), str):
            raise LegacyRepairImportError(
                f"Номер заказа у записи {source_key} должен быть строкой"
            )
        for shipment in shipments:
            if not isinstance(shipment, dict):
                raise LegacyRepairImportError(
                    f"Некорректная накладная у записи {source_key}"
                )
            if _text(shipment.get("direction")) not in SHIPMENT_DIRECTION_LABELS:
                raise LegacyRepairImportError(
                    f"Некорректное направление накладной у {source_key}"
                )
            if not isinstance(shipment.get("track_number", ""), str):
                raise LegacyRepairImportError(
                    f"Накладная у записи {source_key} должна быть строкой"
                )


def _append_comment(current, addition):
    current = _text(current)
    addition = _text(addition)
    if not addition or addition in current:
        return current
    return f"{current}\n\n{addition}".strip()


def _apply_order_lookup(record, fields, review_notes, order_resolver):
    order_number = _text(fields.get("order_number"))
    if (
        not order_number
        or fields.get("order_source") != "our"
        or order_resolver is None
    ):
        return {}

    try:
        snapshot = order_resolver(order_number)
    except Exception as error:
        snapshot = {"ok": False, "error": str(error)}
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        message = _text(
            snapshot.get("error") if isinstance(snapshot, dict) else ""
        ) or "заказ не найден"
        review_notes.append(
            f"Не удалось проверить заказ №{order_number}: {message}."
        )
        return snapshot if isinstance(snapshot, dict) else {}

    client = snapshot.get("client")
    if isinstance(client, dict):
        for field in ("client_name", "client_phone", "client_email"):
            if not _text(fields.get(field)):
                fields[field] = _text(client.get(field))
    if (
        fields.get("communication_channel") == "email"
        and not _text(fields.get("contact"))
    ):
        fields["contact"] = _text(fields.get("client_email"))

    products = [
        _text(product.get("name"))
        for product in snapshot.get("products", [])
        if isinstance(product, dict) and _text(product.get("name"))
    ]
    lookup_settings = record.get("order_lookup")
    if (
        isinstance(lookup_settings, dict)
        and lookup_settings.get("fill_product_when_single")
        and not _text(fields.get("product_name"))
        and len(products) == 1
    ):
        fields["product_name"] = products[0]

    checked_facts = [
        f"Заказ №{order_number} проверен в режиме только чтения."
    ]
    if _text(snapshot.get("order_date")):
        checked_facts.append(
            f"Дата заказа: {_text(snapshot.get('order_date'))}."
        )
    if products:
        checked_facts.append(
            "Товары в заказе: " + "; ".join(products) + "."
        )
    fields["internal_comment"] = _append_comment(
        fields.get("internal_comment"),
        "\n".join(checked_facts),
    )
    return snapshot


def _stable_uuid(*parts):
    payload = ":".join(_text(part) for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def _build_import_case(
    dataset,
    record,
    imported_at,
    repair_number,
    order_resolver=None,
):
    fields = copy.deepcopy(record["fields"])
    review_notes = [
        _text(note)
        for note in record.get("review_notes", [])
        if _text(note)
    ]
    order_snapshot = _apply_order_lookup(
        record,
        fields,
        review_notes,
        order_resolver,
    )
    source_key = _text(record["source_key"])
    case_id = _stable_uuid(dataset["batch_key"], source_key)
    shipments = []
    for index, source in enumerate(record["shipments"], start=1):
        shipment = copy.deepcopy(source)
        shipment["track_number"] = _text(shipment.get("track_number"))
        shipment["id"] = _stable_uuid(
            dataset["batch_key"],
            source_key,
            "shipment",
            index,
            shipment["track_number"],
        )
        shipments.append(shipment)

    import_metadata = {
        "batch_key": _text(dataset["batch_key"]),
        "source_key": source_key,
        "source_row": record.get("source_row"),
        "source_url": _text(dataset["source_url"]),
        "label": _text(record.get("label")),
        "requires_review": bool(review_notes),
        "review_notes": list(dict.fromkeys(review_notes)),
        "source_record": copy.deepcopy(record),
        "order_check": copy.deepcopy(order_snapshot),
    }
    case = {
        "id": case_id,
        "schema_version": REPAIR_SCHEMA_VERSION,
        "repair_number": repair_number,
        "created_at": imported_at,
        "updated_at": imported_at,
        "archived_at": "",
        "request_at": "",
        "attachments": [],
        "shipments": shipments,
        "history": [
            make_history_event(
                "Импортирована запись из старого Excel",
                actor=IMPORT_ACTOR,
                comment=(
                    f"{_text(record.get('label'))}; "
                    f"строка {record.get('source_row')}"
                ),
                timestamp=imported_at,
            )
        ],
        "legacy_import": import_metadata,
        "migration": {
            "review_notes": list(dict.fromkeys(review_notes)),
        },
        "legacy_snapshot": {
            "source_url": _text(dataset["source_url"]),
            "source_record": copy.deepcopy(record),
        },
    }
    case.update(fields)
    case["repair_type"] = case["request_type"]
    return migrate_repair_case(case, migrated_at=imported_at)


def _next_repair_numbers(cases, count, year):
    used = {
        _text(case.get("repair_number"))
        for case in cases
        if _text(case.get("repair_number"))
    }
    sequence = 1
    for repair_number in used:
        match = re.fullmatch(rf"R-{re.escape(str(year))}-(\d+)", repair_number)
        if match:
            sequence = max(sequence, int(match.group(1)) + 1)
    result = []
    while len(result) < count:
        candidate = f"R-{year}-{sequence:04d}"
        sequence += 1
        if candidate in used:
            continue
        used.add(candidate)
        result.append(candidate)
    return result


def _match_existing(source, cases):
    source_tracks = _tracks(source)
    if source_tracks:
        matches = [
            index
            for index, case in enumerate(cases)
            if source_tracks & _tracks(case)
        ]
        if len(matches) == 1:
            return matches[0], "совпала накладная"
        if len(matches) > 1:
            return None, "одна накладная найдена в нескольких карточках"

    messenger = _canonical(
        source.get("client_messenger") or source.get("contact")
    )
    if messenger.startswith("@"):
        matches = [
            index
            for index, case in enumerate(cases)
            if messenger
            in {
                _canonical(case.get("client_messenger")),
                _canonical(case.get("contact")),
            }
        ]
        if len(matches) == 1:
            return matches[0], "совпал Telegram"
        if len(matches) > 1:
            return None, "Telegram найден в нескольких карточках"

    order_number = _canonical(source.get("order_number"))
    if order_number:
        matches = []
        for index, case in enumerate(cases):
            if _canonical(case.get("order_number")) != order_number:
                continue
            secondary_match = any(
                _canonical(source.get(field))
                and _canonical(source.get(field))
                == _canonical(case.get(field))
                for field in ("product_name", "model", "problem")
            )
            if secondary_match:
                matches.append(index)
        if len(matches) == 1:
            return matches[0], "совпали заказ и товар/неисправность"
        if len(matches) > 1:
            return None, "заказ и признаки найдены в нескольких карточках"

    client_name = _canonical(source.get("client_name"))
    if client_name:
        matches = []
        for index, case in enumerate(cases):
            if _canonical(case.get("client_name")) != client_name:
                continue
            secondary_match = any(
                _canonical(source.get(field))
                and _canonical(source.get(field))
                == _canonical(case.get(field))
                for field in ("product_name", "model", "problem")
            )
            if secondary_match:
                matches.append(index)
        if len(matches) == 1:
            return matches[0], "совпали клиент и товар/неисправность"
        if len(matches) > 1:
            return None, "клиент и признаки найдены в нескольких карточках"

    return None, ""


def _merge_import_case(existing, source, imported_at):
    merged = copy.deepcopy(existing)
    changed = []
    protected_fields = {
        "id",
        "repair_number",
        "created_at",
        "updated_at",
        "archived_at",
        "history",
        "shipments",
        "attachments",
        "legacy_snapshot",
        "migration",
        "legacy_import",
    }
    for field, value in source.items():
        if field in protected_fields or not _text(value):
            continue
        if not _text(merged.get(field)):
            merged[field] = copy.deepcopy(value)
            changed.append(field)

    known_tracks = _tracks(merged)
    for shipment in source.get("shipments", []):
        track_number = _text(shipment.get("track_number"))
        if track_number and track_number not in known_tracks:
            merged.setdefault("shipments", []).append(copy.deepcopy(shipment))
            known_tracks.add(track_number)
            changed.append(f"накладная {track_number}")

    source_comment = _text(source.get("internal_comment"))
    merged_comment = _append_comment(
        merged.get("internal_comment"),
        "Импорт из старого Excel:\n" + source_comment
        if source_comment
        else "Импорт из старого Excel.",
    )
    if merged_comment != _text(merged.get("internal_comment")):
        merged["internal_comment"] = merged_comment
        changed.append("примечание")

    merged["legacy_import"] = copy.deepcopy(source["legacy_import"])
    review_notes = list(
        merged.get("migration", {}).get("review_notes", [])
        if isinstance(merged.get("migration"), dict)
        else []
    )
    review_notes.extend(source["legacy_import"].get("review_notes", []))
    merged["migration"] = {
        "review_notes": list(dict.fromkeys(_text(note) for note in review_notes)),
    }
    merged["updated_at"] = imported_at
    append_history_event(
        merged,
        "Дополнена данными из старого Excel",
        actor=IMPORT_ACTOR,
        comment=", ".join(changed) or "Добавлена отметка об источнике",
        timestamp=imported_at,
    )
    return migrate_repair_case(merged, migrated_at=imported_at)


def plan_legacy_repair_import(
    existing_cases,
    dataset,
    imported_at=None,
    order_resolver=None,
):
    validate_import_dataset(dataset)
    imported_at = imported_at or datetime.now().astimezone().strftime(
        "%Y-%m-%d %H:%M"
    )
    existing_cases = [
        migrate_repair_case(case, migrated_at=imported_at)
        for case in existing_cases
    ]
    original_cases = copy.deepcopy(existing_cases)
    source_key_indexes = {}
    for index, case in enumerate(original_cases):
        source_key = _source_key(case)
        if source_key:
            source_key_indexes.setdefault(source_key, []).append(index)

    numbers = iter(
        _next_repair_numbers(
            existing_cases,
            len(dataset["records"]),
            imported_at[:4],
        )
    )
    result_cases = copy.deepcopy(existing_cases)
    operations = []
    for record in dataset["records"]:
        source_key = _text(record["source_key"])
        existing_source_matches = source_key_indexes.get(source_key, [])
        if len(existing_source_matches) == 1:
            existing = original_cases[existing_source_matches[0]]
            metadata = existing.get("legacy_import", {})
            operations.append({
                "source_key": source_key,
                "label": _text(record.get("label")),
                "action": "skip",
                "repair_number": _text(existing.get("repair_number")),
                "reason": "запись этого источника уже импортирована",
                "requires_review": bool(
                    isinstance(metadata, dict)
                    and metadata.get("requires_review")
                ),
                "review_notes": list(
                    metadata.get("review_notes", [])
                    if isinstance(metadata, dict)
                    else []
                ),
            })
            continue
        if len(existing_source_matches) > 1:
            operations.append({
                "source_key": source_key,
                "label": _text(record.get("label")),
                "action": "manual",
                "repair_number": "",
                "reason": "source_key уже указан у нескольких карточек",
                "requires_review": True,
                "review_notes": [
                    "Найдено несколько ранее импортированных карточек."
                ],
            })
            continue

        source = _build_import_case(
            dataset,
            record,
            imported_at,
            next(numbers),
            order_resolver=order_resolver,
        )
        match_index, reason = _match_existing(source, original_cases)
        if reason and match_index is None:
            operations.append({
                "source_key": source_key,
                "label": _text(record.get("label")),
                "action": "manual",
                "repair_number": "",
                "reason": reason,
                "requires_review": True,
                "review_notes": [reason],
            })
            continue
        if match_index is not None:
            existing = original_cases[match_index]
            merged = _merge_import_case(existing, source, imported_at)
            result_index = next(
                index
                for index, case in enumerate(result_cases)
                if case.get("id") == existing.get("id")
            )
            result_cases[result_index] = merged
            action = "update"
            repair_number = _text(merged.get("repair_number"))
        else:
            result_cases.append(source)
            action = "create"
            repair_number = _text(source.get("repair_number"))

        metadata = source.get("legacy_import", {})
        operations.append({
            "source_key": source_key,
            "label": _text(record.get("label")),
            "action": action,
            "repair_number": repair_number,
            "reason": reason or "совпадений не найдено",
            "requires_review": bool(metadata.get("requires_review")),
            "review_notes": list(metadata.get("review_notes", [])),
        })

    counts = {
        action: sum(
            1 for operation in operations if operation["action"] == action
        )
        for action in ("create", "update", "skip", "manual")
    }
    counts["requires_review"] = sum(
        1 for operation in operations if operation["requires_review"]
    )
    return {
        "batch_key": _text(dataset["batch_key"]),
        "source_url": _text(dataset["source_url"]),
        "imported_at": imported_at,
        "counts": counts,
        "operations": operations,
        "result_cases": result_cases,
    }


def _write_backup(path, backup_dir):
    path = Path(path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"repair_cases-before-import-{timestamp}.json"
    if path.exists():
        shutil.copy2(path, backup_path)
    else:
        backup_path.write_text("[]\n", encoding="utf-8")
    digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    return backup_path, digest


def import_legacy_repair_file(
    path,
    dataset,
    apply=False,
    backup_dir=None,
    imported_at=None,
    order_resolver=None,
):
    path = Path(path)
    if not apply:
        plan = plan_legacy_repair_import(
            load_repair_file(path),
            dataset,
            imported_at=imported_at,
            order_resolver=order_resolver,
        )
        plan["applied"] = False
        plan["backup_path"] = ""
        plan["backup_sha256"] = ""
        plan.pop("result_cases", None)
        return plan

    if backup_dir is None:
        raise LegacyRepairImportError(
            "Для применения импорта обязателен --backup-dir"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        existing_cases = load_repair_file(path)
        plan = plan_legacy_repair_import(
            existing_cases,
            dataset,
            imported_at=imported_at,
            order_resolver=order_resolver,
        )
        if plan["counts"]["manual"]:
            raise LegacyRepairImportError(
                "Импорт остановлен: найдены неоднозначные дубликаты"
            )
        backup_path, digest = _write_backup(path, backup_dir)
        save_repair_file(path, plan["result_cases"])

    plan["applied"] = True
    plan["backup_path"] = str(backup_path)
    plan["backup_sha256"] = digest
    plan.pop("result_cases", None)
    return plan
