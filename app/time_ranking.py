import re
from datetime import date, datetime


_AWARE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M%z",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M%z",
)
_NAIVE_FORMATS = (
    ("%Y-%m-%dT%H:%M:%S.%f", "second"),
    ("%Y-%m-%dT%H:%M:%S", "second"),
    ("%Y-%m-%dT%H:%M", "minute"),
    ("%Y-%m-%d %H:%M:%S.%f", "second"),
    ("%Y-%m-%d %H:%M:%S", "second"),
    ("%Y-%m-%d %H:%M", "minute"),
    ("%d.%m.%Y %H:%M:%S", "second"),
    ("%d.%m.%Y %H:%M", "minute"),
    ("%Y-%m-%d", "date"),
    ("%d.%m.%Y", "date"),
)


def parse_erp_datetime(value):
    """Return (datetime, precision) without changing the stored value."""
    if isinstance(value, datetime):
        return value, "second"
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day), "date"
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)), "second"
        except (OverflowError, OSError, TypeError, ValueError):
            return None

    text = str(value or "").strip()
    if not text:
        return None

    aware_text = text[:-1] + "+0000" if text.endswith(("Z", "z")) else text
    aware_text = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", aware_text)
    for pattern in _AWARE_FORMATS:
        try:
            parsed = datetime.strptime(aware_text, pattern)
            precision = "second" if "%S" in pattern else "minute"
            return parsed, precision
        except ValueError:
            continue

    for pattern, precision in _NAIVE_FORMATS:
        try:
            return datetime.strptime(text, pattern), precision
        except ValueError:
            continue
    return None


def erp_timestamp(value):
    parsed = parse_erp_datetime(value)
    if parsed is None:
        return None
    try:
        return parsed[0].timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def receipt_business_timestamp(receipt):
    """Use the operation date and its persisted creation time-of-day."""
    receipt_value = receipt.get("receipt_date")
    parsed_receipt = parse_erp_datetime(receipt_value)
    if parsed_receipt is not None and parsed_receipt[1] != "date":
        return erp_timestamp(receipt_value)

    created_value = receipt.get("created_at")
    parsed_created = parse_erp_datetime(created_value)
    if parsed_receipt is not None:
        receipt_date = parsed_receipt[0]
        if parsed_created is not None and parsed_created[1] != "date":
            created_time = parsed_created[0]
            combined = datetime(
                receipt_date.year,
                receipt_date.month,
                receipt_date.day,
                created_time.hour,
                created_time.minute,
                created_time.second,
                created_time.microsecond,
                tzinfo=created_time.tzinfo,
            )
            return erp_timestamp(combined)
        return erp_timestamp(receipt_date)

    return erp_timestamp(created_value)
