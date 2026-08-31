"""Shared validation and extraction rules for ERP product brand names."""

import re


NUMERIC_BRAND_PATTERN = re.compile(r"^\d+$")
BRAND_PROPERTY_CODES = {
    "brand",
    "brand_model",
    "manufacturer",
    "filter_brand",
}
BRAND_PROPERTY_NAMES = {
    "бренд",
    "марка часов",
    "производитель",
}
BRAND_FLAG_NAMES = {
    "отображать в бренде",
}
CANONICAL_BRAND_ALIASES = {
    "луч": "Луч",
    "luch": "Луч",
}


def _text(value):
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    return " ".join(str("" if value is None else value).split())


def normalize_brand(value):
    """Return a user-facing brand or an empty value for numeric garbage."""
    value = _text(value)
    if not value or NUMERIC_BRAND_PATTERN.fullmatch(value):
        return ""
    return CANONICAL_BRAND_ALIASES.get(value.casefold(), value)


def is_numeric_brand(value):
    value = _text(value)
    return bool(value and NUMERIC_BRAND_PATTERN.fullmatch(value))


def _normalized_label(value):
    return _text(value).casefold().replace("-", "_").replace(" ", "_")


def brand_from_properties(properties):
    """Extract a real brand property while ignoring Bitrix display flags."""
    numeric_value_seen = False
    for prop in properties or []:
        name = _text(prop.get("name")).casefold()
        if name in BRAND_FLAG_NAMES:
            continue
        code = _normalized_label(prop.get("code"))
        if (
            code not in BRAND_PROPERTY_CODES
            and name not in BRAND_PROPERTY_NAMES
        ):
            continue
        value = prop.get("display_value")
        if value in (None, "", []):
            value = prop.get("value")
        if isinstance(value, (list, tuple)):
            values = value
        else:
            values = [value]
        for item in values:
            if is_numeric_brand(item):
                numeric_value_seen = True
                continue
            brand = normalize_brand(item)
            if brand:
                return brand, None
    return "", "numeric_brand_rejected" if numeric_value_seen else None
