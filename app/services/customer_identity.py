"""Persistent ERP customers and conservative order identity matching."""

import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone


LOGGER = logging.getLogger(__name__)
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,63}$")
PHONE_FORMATTING = re.compile(r"^[+\d\s().\-\u00a0]+$")
CUSTOMER_PAGE_SIZES = (20, 50, 100, 200)


def _text(value):
    return str(value or "").strip()


def normalize_phone(value):
    """Return an unambiguous E.164-like identity or an empty string."""
    raw = _text(value)
    if (
        not raw or not PHONE_FORMATTING.fullmatch(raw)
        or raw.count("+") > 1 or ("+" in raw and not raw.startswith("+"))
    ):
        return ""
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        return "+7" + digits[1:]
    if len(digits) == 10 and digits.startswith("9") and not raw.startswith("+"):
        return "+7" + digits
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    return ""


def normalize_email(value):
    normalized = _text(value).casefold()
    return normalized if normalized and EMAIL_PATTERN.fullmatch(normalized) else ""


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def order_identity(order):
    return {
        "name": _text(order.get("customer")),
        "phone": _text(order.get("phone")),
        "normalized_phone": normalize_phone(order.get("phone")),
        "email": _text(order.get("email")),
        "normalized_email": normalize_email(order.get("email")),
        "country": _text(order.get("country")),
        "region": _text(order.get("region")),
        "city": _text(order.get("city")),
    }


def _customer_rows(connection, field, value):
    if not value:
        return []
    return connection.execute(
        "SELECT * FROM customers WHERE {} = ? ORDER BY id".format(field),
        (value,),
    ).fetchall()


def match_or_create_customer(connection, order, create=True, logger=None):
    """Match one order without ever merging ambiguous customer records."""
    logger = logger or LOGGER
    identity = order_identity(order)
    phone_rows = _customer_rows(
        connection, "normalized_phone", identity["normalized_phone"]
    )
    email_rows = _customer_rows(
        connection, "normalized_email", identity["normalized_email"]
    )
    phone_ids = {int(row["id"]) for row in phone_rows}
    email_ids = {int(row["id"]) for row in email_rows}

    conflict = ""
    if len(phone_ids) > 1:
        conflict = "duplicate_phone_candidates"
    elif len(email_ids) > 1:
        conflict = "duplicate_email_candidates"
    elif phone_ids and email_ids and phone_ids != email_ids:
        conflict = "phone_email_cross_conflict"
    elif len(phone_rows) == 1 and identity["normalized_email"]:
        stored_email = _text(phone_rows[0]["normalized_email"])
        if stored_email and stored_email != identity["normalized_email"]:
            conflict = "phone_email_value_conflict"
    elif len(email_rows) == 1 and identity["normalized_phone"]:
        stored_phone = _text(email_rows[0]["normalized_phone"])
        if stored_phone and stored_phone != identity["normalized_phone"]:
            conflict = "email_phone_value_conflict"
    if conflict:
        logger.warning(
            "Customer identity conflict reason=%s order_id=%s source=%s",
            conflict,
            _text(order.get("id") or order.get("ID")),
            _text(order.get("source") or "tictactoy"),
        )
        return {"customer_id": None, "action": "conflict", "reason": conflict}

    matched = next(iter(phone_ids or email_ids), None)
    if matched is not None:
        matched_row = next(
            row for row in phone_rows + email_rows if int(row["id"]) == matched
        )
        updates = {}
        for field in ("name", "phone", "email", "country", "region", "city"):
            if not _text(matched_row[field]) and identity[field]:
                updates[field] = identity[field]
        if not _text(matched_row["normalized_phone"]) and identity["normalized_phone"]:
            updates["normalized_phone"] = identity["normalized_phone"]
        if not _text(matched_row["normalized_email"]) and identity["normalized_email"]:
            updates["normalized_email"] = identity["normalized_email"]
        if "name" in updates:
            updates["name_fold"] = updates["name"].casefold()
        if updates:
            updates["updated_at"] = utc_now()
            fields = sorted(updates)
            connection.execute(
                "UPDATE customers SET {} WHERE id = ?".format(
                    ", ".join("{} = ?".format(field) for field in fields)
                ),
                [updates[field] for field in fields] + [matched],
            )
        return {"customer_id": matched, "action": "matched", "reason": ""}
    if not create or not (
        identity["normalized_phone"] or identity["normalized_email"]
    ):
        return {"customer_id": None, "action": "unlinked", "reason": "no_identity"}

    now = utc_now()
    cursor = connection.execute(
        "INSERT INTO customers (name, name_fold, phone, normalized_phone, email, "
        "normalized_email, country, region, city, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            identity["name"], identity["name"].casefold(), identity["phone"], identity["normalized_phone"],
            identity["email"], identity["normalized_email"], identity["country"],
            identity["region"], identity["city"], now, now,
        ),
    )
    return {"customer_id": int(cursor.lastrowid), "action": "created", "reason": ""}


def link_order_safely(connection, order, logger=None):
    """CRM failures are isolated so they can never reject an order ingest."""
    try:
        return match_or_create_customer(connection, order, logger=logger)
    except Exception as error:  # defensive boundary required by the ingest contract
        (logger or LOGGER).exception(
            "Customer matching failed without blocking order order_id=%s reason=%s",
            _text(order.get("id") or order.get("ID")), type(error).__name__,
        )
        return {"customer_id": None, "action": "error", "reason": type(error).__name__}


def _stable_value(orders, field):
    values = [_text(order.get(field)) for order in orders if _text(order.get(field))]
    if not values:
        return ""
    counts = Counter(values)
    return sorted(counts, key=lambda value: (-counts[value], value.casefold(), value))[0]


def _canonical_order(orders):
    return {
        "customer": _stable_value(orders, "customer"),
        "phone": _stable_value(orders, "phone"),
        "email": _stable_value(orders, "email"),
        "country": _stable_value(orders, "country"),
        "region": _stable_value(orders, "region"),
        "city": _stable_value(orders, "city"),
    }


def analyze_orders(orders):
    """Build deterministic one-to-one contact groups before any writes."""
    prepared = []
    phones_to_emails = defaultdict(set)
    emails_to_phones = defaultdict(set)
    invalid_phones = invalid_emails = 0
    for order in orders:
        identity = order_identity(order)
        phone = identity["normalized_phone"]
        email = identity["normalized_email"]
        if _text(order.get("phone")) and not phone:
            invalid_phones += 1
        if _text(order.get("email")) and not email:
            invalid_emails += 1
        if phone and email:
            phones_to_emails[phone].add(email)
            emails_to_phones[email].add(phone)
        prepared.append((order, phone, email))

    phone_conflicts = {key for key, values in phones_to_emails.items() if len(values) > 1}
    email_conflicts = {key for key, values in emails_to_phones.items() if len(values) > 1}
    groups = defaultdict(list)
    ambiguous = []
    for order, phone, email in prepared:
        if phone in phone_conflicts or email in email_conflicts:
            ambiguous.append(order)
            continue
        if phone and email:
            key = ("pair", phone, email)
        elif phone:
            linked_emails = phones_to_emails.get(phone) or set()
            key = ("pair", phone, next(iter(linked_emails))) if linked_emails else ("phone", phone)
        elif email:
            linked_phones = emails_to_phones.get(email) or set()
            key = ("pair", next(iter(linked_phones)), email) if linked_phones else ("email", email)
        else:
            key = None
        if key:
            groups[key].append(order)

    with_phone = sum(bool(_text(order.get("phone"))) for order, _phone, _email in prepared)
    with_email = sum(bool(_text(order.get("email"))) for order, _phone, _email in prepared)
    empty_phones = len(orders) - with_phone
    empty_emails = len(orders) - with_email
    without_identity = sum(not phone and not email for _order, phone, email in prepared)
    linked_forecast = sum(len(rows) for rows in groups.values())
    return {
        "groups": groups,
        "ambiguous_orders": ambiguous,
        "report": {
            "orders_total": len(orders),
            "orders_with_phone": with_phone,
            "orders_with_email": with_email,
            "orders_without_identity": without_identity,
            "unique_normalized_phones": len({phone for _o, phone, _e in prepared if phone}),
            "unique_normalized_emails": len({email for _o, _p, email in prepared if email}),
            "safe_customer_groups": len(groups),
            "phone_conflicts": len(phone_conflicts),
            "email_conflicts": len(email_conflicts),
            "phone_email_cross_conflicts": len(phone_conflicts) + len(email_conflicts),
            "empty_phones": empty_phones,
            "empty_emails": empty_emails,
            "garbage_phones": invalid_phones,
            "invalid_emails": invalid_emails,
            "estimated_customers": len(groups),
            "estimated_linked_orders": linked_forecast,
            "ambiguity_cases": len(ambiguous),
        },
    }


def backfill_customers(connection, logger=None):
    import json

    rows = connection.execute(
        "SELECT order_id, source, customer_id, payload_json FROM orders_snapshot "
        "ORDER BY order_id"
    ).fetchall()
    orders = []
    existing_links = {}
    for row in rows:
        order = json.loads(row["payload_json"])
        order.setdefault("id", row["order_id"])
        order.setdefault("source", row["source"])
        orders.append(order)
        if row["customer_id"] is not None:
            existing_links[row["order_id"]] = int(row["customer_id"])
    analysis = analyze_orders(orders)
    linked = conflicts = 0
    for _key, group_orders in sorted(analysis["groups"].items(), key=lambda item: item[0]):
        linked_ids = {
            existing_links[_text(order.get("id") or order.get("ID"))]
            for order in group_orders
            if _text(order.get("id") or order.get("ID")) in existing_links
        }
        if len(linked_ids) > 1:
            conflicts += len(group_orders)
            continue
        if linked_ids:
            customer_id = next(iter(linked_ids))
        else:
            result = match_or_create_customer(
                connection, _canonical_order(group_orders), logger=logger
            )
            customer_id = result["customer_id"]
            if customer_id is None:
                conflicts += len(group_orders)
                continue
        order_ids = [_text(order.get("id") or order.get("ID")) for order in group_orders]
        placeholders = ",".join("?" for _value in order_ids)
        cursor = connection.execute(
            "UPDATE orders_snapshot SET customer_id = ? WHERE customer_id IS NULL "
            "AND order_id IN ({})".format(placeholders),
            [customer_id] + order_ids,
        )
        linked += cursor.rowcount
    totals = connection.execute(
        "SELECT COUNT(*) AS orders_total, "
        "SUM(CASE WHEN customer_id IS NOT NULL THEN 1 ELSE 0 END) AS linked_orders "
        "FROM orders_snapshot"
    ).fetchone()
    customers = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    return {
        **analysis["report"],
        "actual_customers": int(customers),
        "linked_orders": int(totals["linked_orders"] or 0),
        "orders_without_customer": int(totals["orders_total"] - (totals["linked_orders"] or 0)),
        "new_links": linked,
        "runtime_conflicts": conflicts,
    }


class CustomerStore:
    def __init__(self, orders_store):
        self.orders_store = orders_store

    def list(self, query="", page=1, per_page=20):
        self.orders_store.initialize()
        query = _text(query)
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = int(per_page)
        except (TypeError, ValueError):
            per_page = 20
        if per_page not in CUSTOMER_PAGE_SIZES:
            per_page = 20
        clauses, parameters = [], []
        if query:
            folded = "%{}%".format(query.casefold())
            clauses.append(
                "(c.name_fold LIKE ? OR lower(c.email) LIKE ? OR c.normalized_email LIKE ? "
                "OR EXISTS (SELECT 1 FROM orders_snapshot oq WHERE oq.customer_id = c.id "
                "AND oq.number_fold LIKE ?))"
            )
            parameters.extend([folded, folded, folded, folded])
            phone = normalize_phone(query)
            if phone:
                clauses[-1] = clauses[-1][:-1] + " OR c.normalized_phone = ?)"
                parameters.append(phone)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        aggregate = (
            "SELECT customer_id, COUNT(*) AS orders_count, MIN(created_sort) AS first_order_at, "
            "MAX(created_sort) AS last_order_at FROM orders_snapshot "
            "WHERE customer_id IS NOT NULL GROUP BY customer_id"
        )
        with self.orders_store.connection() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM customers c" + where, parameters
            ).fetchone()[0])
            pages = max(1, int(math.ceil(float(total) / per_page)))
            page = min(page, pages)
            rows = connection.execute(
                "SELECT c.*, COALESCE(a.orders_count, 0) AS orders_count, "
                "a.first_order_at, a.last_order_at FROM customers c LEFT JOIN (" + aggregate +
                ") a ON a.customer_id = c.id" + where +
                " ORDER BY COALESCE(a.last_order_at, '' ) DESC, c.id DESC LIMIT ? OFFSET ?",
                parameters + [per_page, (page - 1) * per_page],
            ).fetchall()
        return {"rows": [dict(row) for row in rows], "total": total, "page": page,
                "per_page": per_page, "pages": pages}

    def get(self, customer_id):
        self.orders_store.initialize()
        with self.orders_store.connection() as connection:
            row = connection.execute(
                "SELECT c.*, COUNT(o.order_id) AS orders_count, MIN(o.created_sort) AS first_order_at, "
                "MAX(o.created_sort) AS last_order_at FROM customers c "
                "LEFT JOIN orders_snapshot o ON o.customer_id = c.id WHERE c.id = ? GROUP BY c.id",
                (customer_id,),
            ).fetchone()
        return dict(row) if row else None

    def orders(self, customer_id, page=1, per_page=20):
        import json

        self.orders_store.initialize()
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = int(per_page)
        except (TypeError, ValueError):
            per_page = 20
        if per_page not in CUSTOMER_PAGE_SIZES:
            per_page = 20
        with self.orders_store.connection() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM orders_snapshot WHERE customer_id = ?", (customer_id,)
            ).fetchone()[0])
            pages = max(1, int(math.ceil(float(total) / per_page)))
            page = min(page, pages)
            rows = connection.execute(
                "SELECT order_id, source, payload_json FROM orders_snapshot WHERE customer_id = ? "
                "ORDER BY created_sort DESC, order_id DESC LIMIT ? OFFSET ?",
                (customer_id, per_page, (page - 1) * per_page),
            ).fetchall()
        orders = []
        for row in rows:
            order = json.loads(row["payload_json"])
            order["customer_id"] = customer_id
            orders.append(order)
        return {"rows": orders, "total": total, "page": page, "per_page": per_page, "pages": pages}
