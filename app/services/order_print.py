import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


ADDRESS_MARKER = re.compile(r"#S\s*([^#\s,;]+)", re.IGNORECASE)
DELIVERY_SEPARATOR = re.compile(r"\s*(?::|—|–|\|)\s*", re.UNICODE)

ONES = (
    "", "один", "два", "три", "четыре", "пять", "шесть", "семь",
    "восемь", "девять", "десять", "одиннадцать", "двенадцать",
    "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
    "семнадцать", "восемнадцать", "девятнадцать",
)
FEMALE_ONES = ("", "одна", "две")
TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")
ORDERS = (
    ("", "", "", False),
    ("тысяча", "тысячи", "тысяч", True),
    ("миллион", "миллиона", "миллионов", False),
    ("миллиард", "миллиарда", "миллиардов", False),
    ("триллион", "триллиона", "триллионов", False),
)


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
    rendered = format(amount, ",.2f").replace(",", " ").replace(".", ",")
    return rendered


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


def format_phone(value):
    raw = _text(value)
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7 ({}) {}-{}-{}".format(
            digits[1:4], digits[4:7], digits[7:9], digits[9:11]
        )
    return raw


def _plural(value, forms):
    last_two = value % 100
    if 11 <= last_two <= 14:
        return forms[2]
    last = value % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def _triplet_words(value, female=False):
    words = []
    hundreds, remainder = divmod(value, 100)
    tens, units = divmod(remainder, 10)
    if hundreds:
        words.append(HUNDREDS[hundreds])
    if remainder < 20:
        if remainder:
            if female and remainder in (1, 2):
                words.append(FEMALE_ONES[remainder])
            else:
                words.append(ONES[remainder])
    else:
        words.append(TENS[tens])
        if units:
            if female and units in (1, 2):
                words.append(FEMALE_ONES[units])
            else:
                words.append(ONES[units])
    return words


def amount_in_words(value):
    amount = _decimal(value)
    if amount is None or amount < 0:
        return ""
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rubles = int(amount)
    kopecks = int((amount - Decimal(rubles)) * 100)
    if rubles >= 10 ** 15:
        return ""
    words = []
    if rubles == 0:
        words.append("ноль")
    order = 0
    remaining = rubles
    groups = []
    while remaining:
        remaining, group = divmod(remaining, 1000)
        groups.append((order, group))
        order += 1
    for order, group in reversed(groups):
        if not group:
            continue
        forms = ORDERS[order]
        words.extend(_triplet_words(group, female=forms[3]))
        if order:
            words.append(_plural(group, forms[:3]))
    words.append(_plural(rubles, ("рубль", "рубля", "рублей")))
    words.append(f"{kopecks:02d}")
    words.append(_plural(kopecks, ("копейка", "копейки", "копеек")))
    return " ".join(words)


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


def _delivery_parts(value):
    delivery = _text(value)
    if not delivery:
        return "", ""
    parts = DELIVERY_SEPARATOR.split(delivery, maxsplit=1)
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    return delivery, ""


def _line_context(item, index):
    source = item if isinstance(item, dict) else {}
    quantity = _decimal(_first(source, "quantity", "QUANTITY"), Decimal("1"))
    unit_price = _decimal(_first(source, "sale_unit_price", "price", "PRICE"))
    line_total = _decimal(_first(source, "line_total", "sum", "SUM", "total", "TOTAL"))
    if line_total is None and unit_price is not None:
        line_total = unit_price * quantity
    return {
        "index": index,
        "name": _text(_first(source, "name", "NAME")) or "Товар без названия",
        "quantity": format_quantity(quantity),
        "unit_price": format_money(unit_price),
        "line_total": format_money(line_total),
        "line_total_value": line_total,
    }


def build_order_print_context(order):
    """Build the print view strictly from the normalized read-only order."""
    geography = {
        "postal_code": _text(_first(order, "postal_code", "zip")),
        "country": _text(_first(order, "country", "country_name")),
        "region": _text(_first(order, "region", "region_name")),
        "city": _text(_first(order, "city", "city_name")),
    }
    raw_items = _first(order, "items", "products") or []
    lines = [
        _line_context(item, index)
        for index, item in enumerate(raw_items, start=1)
        if isinstance(item, dict)
    ]
    computed_products_total = (
        sum((line["line_total_value"] for line in lines), Decimal("0"))
        if lines and all(line["line_total_value"] is not None for line in lines)
        else None
    )
    products_total = _decimal(_first(order, "products_total"), computed_products_total)
    discount = _decimal(_first(order, "discount"), Decimal("0"))
    delivery_price = _decimal(_first(order, "delivery_price"), Decimal("0"))
    total = _decimal(_first(order, "order_total", "total", "price"))
    delivery_service, delivery_method = _delivery_parts(_first(order, "delivery"))
    page_span = 1 if len(lines) <= 22 else 1 + ((len(lines) - 23) // 38 + 1)

    return {
        "number": _text(_first(order, "number", "external_id", "id")) or "—",
        "created_at": format_order_datetime(_first(order, "created_at", "date")) or "—",
        "customer": _text(_first(order, "customer")) or "—",
        "phone": format_phone(_first(order, "phone")) or "—",
        "email": _text(_first(order, "email")) or "—",
        "geography": geography,
        "address": _clean_address(_first(order, "address"), geography),
        "payment": _text(_first(order, "payment")) or "—",
        "delivery_service": delivery_service or "—",
        "delivery_method": delivery_method,
        "lines": lines,
        "products_total": format_money(products_total),
        "discount": format_money(discount),
        "delivery_price": format_money(delivery_price),
        "total": format_money(total),
        "total_words": amount_in_words(total),
        "print_min_height_mm": page_span * 190 - (page_span - 1) * 10,
    }
