"""Human-readable presentation of structured ERP journal events."""


ENTITY_LABELS = {
    "product": "Товары",
    "brand": "Бренды",
    "category": "Категории",
    "sale": "Продажи",
    "receipt": "Приход",
}

ACTION_LABELS = {
    "created": "Создано",
    "system_created": "Создано системой",
    "updated": "Изменено",
    "status_changed": "Статус изменён",
    "photo_added": "Добавлена фотография",
    "photo_replaced": "Фотография заменена",
    "photo_removed": "Фотография удалена",
    "cancelled": "Отменено",
    "refused": "Отказ",
    "deleted": "Удалено",
    "comment_added": "Комментарий добавлен",
}

FIELD_LABELS = {
    "name": "Название", "article": "Артикул", "brand": "Бренд",
    "category": "Категория", "price": "Цена", "cell": "Ячейка",
    "stock": "Остаток", "status": "Статус", "payment": "Оплата",
    "tracking": "Трек-номер", "quantity": "Количество",
    "unit_price": "Цена", "source": "Источник", "comment": "Комментарий",
    "order_number": "Номер", "document": "Документ",
    "receipt_date": "Дата прихода", "purchase_price": "Закупочная цена",
}

STATUS_LABELS = {
    "processing": "В обработке",
    "shipped": "Отправлен",
    "sent": "Отправлен",
    "completed": "Завершён успешно",
    "refusal": "Отказ",
    "cancelled": "Отменён",
    "partially_returned": "Частичный возврат",
    "returned": "Возврат",
    "deleted": "Удалён",
    "draft": "Черновик",
    "posted": "Проведён",
}


def display_value(value, field=""):
    if value in (None, ""):
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value)
    if field == "status":
        return STATUS_LABELS.get(text.casefold(), text)
    return text


def _changed_fields_text(count):
    last_two = count % 100
    last = count % 10
    noun = (
        "полей" if 11 <= last_two <= 14
        else "поле" if last == 1
        else "поля" if 2 <= last <= 4
        else "полей"
    )
    return "Изменено {} {}".format(count, noun)


def _products_suffix(metadata):
    try:
        count = int(
            metadata.get("products_deleted")
            if metadata.get("products_deleted") is not None
            else metadata.get("deleted_products_count", 0)
        )
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return ""
    last_two = count % 100
    last = count % 10
    noun = (
        "товаров" if 11 <= last_two <= 14
        else "товар" if last == 1
        else "товара" if 2 <= last <= 4
        else "товаров"
    )
    return " · удалено {} {}".format(count, noun)


def _single_change(changes):
    if len(changes) != 1:
        return ""
    change = changes[0]
    return "{}: {} → {}".format(
        change["label"], change["before"], change["after"]
    )


def format_journal_event(event, changes=None):
    """Return compact feed copy derived only from immutable event data."""
    entity = str(event.get("entity_type") or "")
    action = str(event.get("action") or "")
    metadata = event.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    changes = changes if isinstance(changes, list) else []
    title = str(event.get("object_label_snapshot") or "Событие")
    brand_snapshot = str(
        metadata.get("brand_name_snapshot")
        or metadata.get("brand_name")
        or metadata.get("brand")
        or ""
    ).strip()

    if action in {"photo_added", "photo_replaced", "photo_removed"}:
        action_text = ACTION_LABELS[action]
    elif entity == "brand":
        if action in {"created", "system_created"}:
            action_text = "Создан новый бренд"
        elif action == "updated" and _single_change(changes):
            change = changes[0]
            action_text = "Бренд переименован: {} → {}".format(
                change["before"], change["after"]
            )
        elif action == "deleted":
            action_text = "Бренд удалён" + _products_suffix(metadata)
        else:
            action_text = ACTION_LABELS.get(action, action)
    elif entity == "category":
        if action == "created":
            relation = metadata.get("relation_action")
            global_created = metadata.get("global_category_created") is True
            if brand_snapshot and (global_created or relation == "created"):
                action_text = "Создана новая категория в бренде «{}»".format(
                    brand_snapshot
                )
            elif brand_snapshot and relation == "linked":
                action_text = "Добавлена в бренд «{}»".format(brand_snapshot)
            else:
                action_text = "Создана категория"
        elif action == "updated" and _single_change(changes):
            change = changes[0]
            action_text = (
                "Категория переименована во всей ERP: {} → {}"
                .format(change["before"], change["after"])
            )
        elif action == "deleted":
            action_text = (
                "Удалена из бренда «{}»".format(brand_snapshot)
                if brand_snapshot else "Категория удалена из бренда"
            ) + _products_suffix(metadata)
        else:
            action_text = ACTION_LABELS.get(action, action)
    elif entity == "product":
        if action in {"created", "system_created"}:
            action_text = (
                "Создан новый товар" if action == "created"
                else "Товар создан системой"
            )
        elif action == "deleted":
            action_text = "Товар удалён"
        elif len(changes) == 1:
            action_text = _single_change(changes)
        elif len(changes) > 1:
            action_text = _changed_fields_text(len(changes))
        else:
            action_text = ACTION_LABELS.get(action, action)
    elif entity == "sale":
        if action in {"created", "system_created"}:
            action_text = "Создана новая продажа"
        elif action == "cancelled":
            action_text = "Продажа отменена"
        elif action == "refused":
            action_text = "Отказ"
        elif action == "deleted":
            action_text = "Продажа удалена"
        elif len(changes) == 1:
            action_text = _single_change(changes)
        elif len(changes) > 1:
            action_text = _changed_fields_text(len(changes))
        else:
            action_text = ACTION_LABELS.get(action, action)
    elif entity == "receipt":
        if action in {"created", "system_created"}:
            action_text = "Создан новый приход"
        elif action == "cancelled":
            action_text = "Приход отменён"
        elif action == "deleted":
            action_text = "Приход удалён"
        elif len(changes) == 1:
            action_text = _single_change(changes)
        elif len(changes) > 1:
            action_text = _changed_fields_text(len(changes))
        else:
            action_text = ACTION_LABELS.get(action, action)
    else:
        action_text = _single_change(changes) or ACTION_LABELS.get(action, action)

    return {
        "title": title,
        "action_text": action_text,
        "secondary_context": brand_snapshot,
        "entity_label": ENTITY_LABELS.get(entity, ""),
    }
