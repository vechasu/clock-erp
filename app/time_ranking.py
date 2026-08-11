import re
from datetime import date, datetime, timedelta, timezone


_ISO_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"(?:(?:T| )(\d{2}):(\d{2})"
    r"(?::(\d{2})(?:\.(\d{1,6}))?)?"
    r"(Z|z|[+-]\d{2}:?\d{2})?)?$"
)
_LEGACY_FORMATS = (
    ("%d.%m.%Y %H:%M:%S", "second"),
    ("%d.%m.%Y %H:%M", "minute"),
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

    match = _ISO_DATETIME.match(text)
    if match:
        year, month, day, hour, minute, second, microsecond, offset = (
            match.groups()
        )
        tzinfo = None
        if offset in {"Z", "z"}:
            tzinfo = timezone.utc
        elif offset:
            sign = 1 if offset[0] == "+" else -1
            compact_offset = offset[1:].replace(":", "")
            tzinfo = timezone(sign * timedelta(
                hours=int(compact_offset[:2]),
                minutes=int(compact_offset[2:]),
            ))
        try:
            parsed = datetime(
                int(year), int(month), int(day), int(hour or 0),
                int(minute or 0), int(second or 0),
                int((microsecond or "0").ljust(6, "0")), tzinfo=tzinfo,
            )
            return parsed, (
                "date" if hour is None
                else "second" if second is not None
                else "minute"
            )
        except (TypeError, ValueError):
            return None

    for pattern, precision in _LEGACY_FORMATS:
        try:
            return datetime.strptime(text, pattern), precision
        except ValueError:
            continue
    return None


def erp_timestamp(value):
    parsed = parse_erp_datetime(value)
    if parsed is None:
        return None
    return _parsed_timestamp(parsed[0])


def _parsed_timestamp(parsed):
    try:
        return parsed.timestamp()
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
            return _parsed_timestamp(combined)
        return _parsed_timestamp(receipt_date)

    return erp_timestamp(created_value)
