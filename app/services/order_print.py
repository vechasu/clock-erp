import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


ADDRESS_MARKER = re.compile(r"#S\s*([^#\s,;]+)", re.IGNORECASE)


def _first(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _text(value):
    if value in (None, "") or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _decimal(value, default=None):
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return default


def format_money(value):
    amount = _decimal(value)
    if amount is None:
        return ""
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount == amount.to_integral():
        rendered = format(amount, ",.0f")
    else:
        rendered = format(amount, ",.2f").replace(".", ",")
    return rendered.replace(",", " ") if amount == amount.to_integral() else rendered.replace(",", " ", 1)


def format_quantity(value):
    quantity = _decimal(value, Decimal("1"))
    if quantity == quantity.to_integral():
        return str(int(quantity))
    return format(quantity.normalize(), "f").replace(".", ",")


def format_order_datetime(value):
    raw = _text(value)
    if not raw:
        return ""
    prepared = raw.replace("T", " ")
    prepared = re.sub(r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$", "", prepared)
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(prepared, pattern).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            continue
    return raw


def _properties(order):
    result = {}
    values = _first(order, "properties", "PROPERTIES") or []
    if isinstance(values, dict):
        values = [{"code": key, "value": value} for key, value in values.items()]
    for item in values:
        if not isinstance(item, dict):
            continue
        code = _text(_first(item, "code", "CODE")).upper()
        value = _first(item, "value", "VALUE")
        if code and value not in (None, "", [], {}):
            result[code] = value
    return result


def _property_or_order(order, properties, property_codes, order_keys=()):
    value = _first(order, *order_keys)
    if value not in (None, "", [], {}):
        return value
    for code in property_codes:
        value = properties.get(code)
        if value not in (None, "", [], {}):
            return value
    return None


def _clean_address(address, geography):
    raw = ADDRESS_MARKER.sub("", _text(address)).strip(" ,;#")
    if not raw:
        return ""
    geographic_values = {
        re.sub(r"[^a-zа-яё0-9]+", "", _text(value).casefold())
        for value in geography.values()
        if _text(value)
    }
    parts = []
    for part in (item.strip() for item in raw.split(",")):
        normalized = re.sub(r"[^a-zа-яё0-9]+", "", part.casefold())
        normalized_without_prefix = re.sub(
            r"^(г|город|обл|область|край|республика|рн|район)", "", normalized
        )
        if (
            normalized
            and normalized not in geographic_values
            and normalized_without_prefix not in geographic_values
        ):
            parts.append(part)
    return ", ".join(parts) or raw


def _delivery_point(order, properties, address):
    explicit = _property_or_order(
        order,
        properties,
        ("PVZ", "PICKUP_POINT", "DELIVERY_POINT", "IPOLSDEK_PVZ"),
        ("pickup_point", "delivery_point", "pvz"),
    )
    if explicit:
        return _text(explicit)
    marker = ADDRESS_MARKER.search(_text(address))
    return marker.group(1).strip() if marker else ""


def _line_context(item, index):
    raw = item.get("raw") if isinstance(item, dict) else None
    source = dict(raw) if isinstance(raw, dict) else {}
    if isinstance(item, dict):
        source.update(item)
    quantity = _decimal(_first(source, "quantity", "QUANTITY"), Decimal("1"))
    unit_price = _decimal(_first(
        source, "sale_unit_price", "price", "PRICE"
    ))
    line_total = _decimal(_first(source, "line_total", "sum", "SUM", "total", "TOTAL"))
    if line_total is None and unit_price is not None:
        line_total = unit_price * quantity
    return {
        "index": index,
        "name": _text(_first(source, "name", "NAME")) or "Товар без названия",
        "sku": _text(_first(source, "sku", "SKU", "article", "ARTICLE", "code", "CODE")),
        "quantity": format_quantity(quantity),
        "unit_price": format_money(unit_price),
        "line_total": format_money(line_total),
        "line_total_value": line_total,
    }


def build_order_print_context(order):
    properties = _properties(order)
    geography = {
        "country": _text(_property_or_order(
            order, properties, ("COUNTRY", "COUNTRY_NAME"), ("country", "country_name")
        )),
        "region": _text(_property_or_order(
            order, properties, ("REGION", "REGION_NAME", "LOCATION"), ("region", "region_name")
        )),
        "city": _text(_property_or_order(
            order, properties, ("CITY", "LOCATION_CITY"), ("city", "city_name")
        )),
        "postal_code": _text(_property_or_order(
            order, properties, ("ZIP", "POSTAL_CODE"), ("postal_code", "zip")
        )),
    }
    address_source = _property_or_order(
        order, properties, ("ADDRESS",), ("address", "delivery_address")
    )
    delivery_point = _delivery_point(order, properties, address_source)
    delivery_name = _text(_property_or_order(
        order, properties, ("DELIVERY_NAME",), ("delivery", "delivery_name")
    ))
    point_label = ""
    if delivery_point:
        point_label = "Постамат" if "постамат" in delivery_name.casefold() else "ПВЗ"

    raw_items = _first(order, "items", "products", "PRODUCTS", "basket", "BASKET") or []
    lines = [
        _line_context(item, index)
        for index, item in enumerate(raw_items, start=1)
        if isinstance(item, dict)
    ]
    computed_products_total = sum(
        (line["line_total_value"] for line in lines), Decimal("0")
    ) if lines and all(line["line_total_value"] is not None for line in lines) else None
    products_total = _decimal(_first(order, "products_total"), computed_products_total)
    discount = _decimal(_first(order, "discount", "DISCOUNT", "DISCOUNT_PRICE"), Decimal("0"))
    delivery_price = _decimal(_first(
        order, "delivery_price", "DELIVERY_PRICE", "price_delivery", "PRICE_DELIVERY"
    ))
    total = _decimal(_first(order, "order_total", "total", "price", "PRICE", "sum", "SUM"))
    paid_code = _text(_first(order, "paid", "PAYED")).upper()
    paid_amount = _decimal(_property_or_order(
        order, properties, ("SUM_PAID", "PAID_AMOUNT"), ("sum_paid", "paid_amount")
    ))
    if paid_code == "Y" and paid_amount is None:
        paid_amount = total
    amount_due = _decimal(_property_or_order(
        order, properties, ("TO_PAY", "COD_AMOUNT", "PAYMENT_ON_DELIVERY"),
        ("amount_due", "to_pay", "cod_amount"),
    ))
    payment_name = _text(_first(order, "payment", "pay_system", "PAY_SYSTEM_NAME"))
    if amount_due is None and paid_code != "Y" and any(
        marker in payment_name.casefold()
        for marker in (
            "при получении", "налож", "курьер", "пункт выдачи", "пункте выдачи"
        )
    ):
        amount_due = total

    has_sku = any(line["sku"] for line in lines)
    status = _text(_first(order, "status_name", "STATUS_NAME"))
    if not status:
        status = _text(_first(order, "status", "STATUS_ID")) or "Неизвестный статус"
    return {
        "number": _text(_first(order, "number", "ACCOUNT_NUMBER", "id", "ID", "external_id")),
        "created_at": format_order_datetime(_first(order, "created_at", "date", "DATE_INSERT")),
        "status": status,
        "customer": _text(_first(order, "customer", "client", "name", "USER_NAME")) or _text(properties.get("FIO")),
        "phone": _text(_property_or_order(order, properties, ("PHONE",), ("phone",))),
        "email": _text(_property_or_order(order, properties, ("EMAIL",), ("email",))),
        "delivery": delivery_name,
        "geography": geography,
        "address": _clean_address(address_source, geography),
        "delivery_point": delivery_point,
        "delivery_point_label": point_label,
        "tracking": _text(_property_or_order(
            order, properties, ("TRACKING_NUMBER",), ("tracking", "track_number", "tracking_number")
        )),
        "comment": _text(_first(order, "comment", "USER_DESCRIPTION", "COMMENTS")),
        "payment": payment_name,
        "payment_status": "Оплачен" if paid_code == "Y" else "Не оплачен" if paid_code == "N" else "",
        "paid_amount": format_money(paid_amount),
        "amount_due": format_money(amount_due),
        "lines": lines,
        "has_sku": has_sku,
        "products_total": format_money(products_total),
        "discount": format_money(discount) if discount and discount > 0 else "",
        "delivery_price": format_money(delivery_price),
        "total": format_money(total),
    }
