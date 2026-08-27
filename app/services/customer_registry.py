"""Canonical customer registry backed by a deploy-time migrated SQLite database."""

from __future__ import print_function

import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.customer_registry_migrations import SCHEMA_VERSION, migrate_database


PAGE_SIZES = (20, 50, 100, 200)
PHONE_FORMATTING = re.compile(r"^[+\d\s().\-\u00a0]+$")
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,63}$")
MASKED_EMAIL_MARKERS = ("relay", "masked", "privaterelay", "marketplace")


def text(value):
    return str(value or "").strip()


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_phone(value):
    raw = text(value)
    if (not raw or not PHONE_FORMATTING.fullmatch(raw) or raw.count("+") > 1
            or ("+" in raw and not raw.startswith("+"))):
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
    normalized = text(value).casefold()
    return normalized if EMAIL_PATTERN.fullmatch(normalized) else ""


def masked_email(value):
    normalized = normalize_email(value)
    return bool(normalized and any(marker in normalized for marker in MASKED_EMAIL_MARKERS))


def validate_database(path):
    path = Path(path)
    if not path.exists():
        raise sqlite3.OperationalError("customer registry migration required")
    connection = sqlite3.connect("file:{}?mode=ro".format(path.resolve()), uri=True)
    try:
        expected = {"registry_meta", "customers", "customer_external_ids",
                    "customer_contacts", "customer_operations", "customer_identity_conflicts"}
        actual = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if actual != expected:
            raise sqlite3.DatabaseError("customer registry schema differs")
        version = connection.execute(
            "SELECT value FROM registry_meta WHERE key='schema_version'"
        ).fetchone()
        if not version or version[0] != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("customer registry version differs")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("customer registry quick_check failed")
    finally:
        connection.close()


def _hint(value):
    value = text(value)
    if not value:
        return ""
    if "@" in value:
        local, domain = value.split("@", 1)
        return (local[:1] + "***@" + domain[:2] + "***")
    digits = "".join(character for character in value if character.isdigit())
    return "***" + digits[-2:] if digits else "***"


class CustomerRegistry:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("CUSTOMERS_DATABASE_PATH") or "instance/customers.db")

    def validate(self):
        validate_database(self.path)

    @contextmanager
    def connection(self):
        self.validate()
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _candidate_ids(connection, kind, normalized, source):
        if not normalized:
            return set()
        rows = connection.execute(
            "SELECT DISTINCT customer_id FROM customer_contacts "
            "WHERE kind=? AND normalized_value=? AND (masked=0 OR source=?)",
            (kind, normalized, source),
        ).fetchall()
        return {int(row[0]) for row in rows}

    def upsert_operation(self, connection, operation):
        operation_type = text(operation.get("operation_type"))
        source = text(operation.get("source")).casefold()
        external_id = text(operation.get("external_id"))
        if operation_type not in {"order", "sale", "repair"} or not source or not external_id:
            return {"action": "rejected", "reason": "invalid_operation_identity"}
        existing = connection.execute(
            "SELECT customer_id FROM customer_operations WHERE operation_type=? AND source=? AND external_id=?",
            (operation_type, source, external_id),
        ).fetchone()
        now = utc_now()
        phone = normalize_phone(operation.get("phone"))
        email = normalize_email(operation.get("email"))
        email_masked = int(masked_email(email))
        external_customer_id = text(operation.get("external_customer_id"))
        reason = ""
        if existing:
            customer_id = int(existing[0])
            action = "updated"
            matched_by = "operation_identity"
        else:
            external_ids = set()
            if external_customer_id:
                external_ids = {int(row[0]) for row in connection.execute(
                    "SELECT customer_id FROM customer_external_ids WHERE source=? AND external_customer_id=?",
                    (source, external_customer_id),
                )}
            related_ids = set()
            related_source = text(operation.get("related_order_source")).casefold()
            related_id = text(operation.get("related_order_id"))
            if related_source and related_id:
                related_ids = {int(row[0]) for row in connection.execute(
                    "SELECT customer_id FROM customer_operations WHERE operation_type='order' AND source=? AND external_id=?",
                    (related_source, related_id),
                )}
            phone_ids = self._candidate_ids(connection, "phone", phone, source)
            email_ids = self._candidate_ids(connection, "email", email, source)
            if related_ids:
                candidates = related_ids
                matched_by = "related_order"
            elif external_ids:
                candidates = external_ids
                matched_by = "external_id"
            elif phone_ids and email_ids and phone_ids != email_ids:
                candidates = set()
                reason = "phone_email_cross_conflict"
                matched_by = "conflict"
            elif len(phone_ids) > 1 or len(email_ids) > 1:
                candidates = set()
                reason = "ambiguous_contact"
                matched_by = "conflict"
            else:
                candidates = phone_ids or email_ids
                matched_by = "phone" if phone_ids else "email" if email_ids else "operation_identity"
            if len(candidates) == 1:
                customer_id = next(iter(candidates))
                action = "matched"
            else:
                cursor = connection.execute(
                    "INSERT INTO customers(name,name_fold,city,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (text(operation.get("name")), text(operation.get("name")).casefold(),
                     text(operation.get("city")), now, now),
                )
                customer_id = int(cursor.lastrowid)
                action = "created"
                if reason:
                    connection.execute(
                        "INSERT OR IGNORE INTO customer_identity_conflicts "
                        "(operation_type,source,external_id,reason,phone_hint,email_hint,created_at) VALUES(?,?,?,?,?,?,?)",
                        (operation_type, source, external_id, reason,
                         _hint(operation.get("phone")), _hint(operation.get("email")), now),
                    )

        current = connection.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        updates = {}
        for field in ("name", "city"):
            value = text(operation.get(field))
            if value and not text(current[field]):
                updates[field] = value
        if "name" in updates:
            updates["name_fold"] = updates["name"].casefold()
        if updates:
            updates["updated_at"] = now
            fields = sorted(updates)
            connection.execute(
                "UPDATE customers SET {} WHERE id=?".format(
                    ",".join("{}=?".format(field) for field in fields)
                ), [updates[field] for field in fields] + [customer_id]
            )
        if external_customer_id:
            connection.execute(
                "INSERT OR IGNORE INTO customer_external_ids(customer_id,source,external_customer_id,created_at) VALUES(?,?,?,?)",
                (customer_id, source, external_customer_id, now),
            )
        for kind, normalized, display, masked in (
            ("phone", phone, text(operation.get("phone")), 0),
            ("email", email, text(operation.get("email")), email_masked),
        ):
            if normalized:
                connection.execute(
                    "INSERT OR IGNORE INTO customer_contacts(customer_id,kind,normalized_value,display_value,source,masked,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (customer_id, kind, normalized, display, source, masked, now, now),
                )
        payload = json.dumps(operation.get("payload") or {}, ensure_ascii=False, separators=(",", ":"))
        values = (
            customer_id, external_customer_id or None, text(operation.get("local_ref")) or None,
            text(operation.get("related_order_source")).casefold() or None,
            text(operation.get("related_order_id")) or None, text(operation.get("status")),
            text(operation.get("occurred_at")), operation.get("amount"),
            int(bool(operation.get("completed"))), int(bool(operation.get("cancelled"))),
            int(operation.get("active", 1) is not False), payload, now, now,
        )
        if existing:
            connection.execute(
                "UPDATE customer_operations SET customer_id=?,external_customer_id=COALESCE(?,external_customer_id),"
                "local_ref=COALESCE(?,local_ref),related_order_source=COALESCE(?,related_order_source),"
                "related_order_id=COALESCE(?,related_order_id),status=?,occurred_at=?,amount=?,completed=?,"
                "cancelled=?,active=?,payload_json=?,updated_at=? WHERE operation_type=? AND source=? AND external_id=?",
                values[:-1] + (operation_type, source, external_id),
            )
        else:
            connection.execute(
                "INSERT INTO customer_operations(customer_id,external_customer_id,local_ref,related_order_source,"
                "related_order_id,status,occurred_at,amount,completed,cancelled,active,payload_json,created_at,updated_at,"
                "operation_type,source,external_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values + (operation_type, source, external_id),
            )
        return {"action": action, "customer_id": customer_id, "reason": reason,
                "matched_by": matched_by}

    @staticmethod
    def recompute(connection):
        connection.execute(
            "UPDATE customers SET first_operation_at=(SELECT MIN(occurred_at) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1),"
            "last_operation_at=(SELECT MAX(occurred_at) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1),"
            "operations_count=(SELECT COUNT(*) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1),"
            "completed_orders_count=(SELECT COUNT(*) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1 AND o.operation_type='order' AND o.completed=1 AND o.cancelled=0),"
            "cancelled_orders_count=(SELECT COUNT(*) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1 AND o.operation_type='order' AND o.cancelled=1),"
            "total_completed_amount=COALESCE((SELECT SUM(CASE WHEN o.operation_type='order' AND o.completed=1 AND o.cancelled=0 THEN COALESCE(o.amount,0) WHEN o.operation_type='sale' AND o.completed=1 AND o.cancelled=0 AND NOT EXISTS (SELECT 1 FROM customer_operations q WHERE q.operation_type='order' AND q.source=o.related_order_source AND q.external_id=o.related_order_id) THEN COALESCE(o.amount,0) ELSE 0 END) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1),0)"
        )

    def list(self, query="", page=1, per_page=50):
        query = text(query)
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = int(per_page)
        except (TypeError, ValueError):
            per_page = 50
        if per_page not in PAGE_SIZES:
            per_page = 50
        where, params = "", []
        if query:
            folded = "%{}%".format(query.casefold())
            phone = normalize_phone(query)
            where = (" WHERE c.name_fold LIKE ? OR lower(c.city) LIKE ? OR EXISTS (SELECT 1 FROM customer_contacts cc WHERE cc.customer_id=c.id AND (cc.normalized_value LIKE ? OR lower(cc.display_value) LIKE ?)) OR EXISTS (SELECT 1 FROM customer_operations co WHERE co.customer_id=c.id AND (lower(co.source) LIKE ? OR lower(co.external_id) LIKE ? OR lower(COALESCE(co.local_ref,'')) LIKE ?)) OR EXISTS (SELECT 1 FROM customer_external_ids ce WHERE ce.customer_id=c.id AND (lower(ce.source) LIKE ? OR lower(ce.external_customer_id) LIKE ?))")
            params = [folded] * 9
            if phone:
                where += " OR EXISTS (SELECT 1 FROM customer_contacts cp WHERE cp.customer_id=c.id AND cp.kind='phone' AND cp.normalized_value=?)"
                params.append(phone)
        with self.connection() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM customers c" + where, params).fetchone()[0])
            pages = max(1, int(math.ceil(float(total) / per_page)))
            page = min(page, pages)
            rows = connection.execute(
                "SELECT c.*, (SELECT display_value FROM customer_contacts WHERE customer_id=c.id AND kind='phone' ORDER BY id LIMIT 1) phone,"
                "(SELECT display_value FROM customer_contacts WHERE customer_id=c.id AND kind='email' ORDER BY id LIMIT 1) email,"
                "(SELECT group_concat(DISTINCT source) FROM customer_operations WHERE customer_id=c.id) sources "
                "FROM customers c" + where + " ORDER BY COALESCE(c.last_operation_at,'') DESC,c.id DESC LIMIT ? OFFSET ?",
                params + [per_page, (page - 1) * per_page],
            ).fetchall()
        return {"rows": [dict(row) for row in rows], "total": total, "page": page,
                "per_page": per_page, "pages": pages}

    def get(self, customer_id):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT c.*, (SELECT display_value FROM customer_contacts WHERE customer_id=c.id AND kind='phone' ORDER BY id LIMIT 1) phone,"
                "(SELECT display_value FROM customer_contacts WHERE customer_id=c.id AND kind='email' ORDER BY id LIMIT 1) email,"
                "(SELECT group_concat(DISTINCT source) FROM customer_operations WHERE customer_id=c.id) sources FROM customers c WHERE c.id=?",
                (customer_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_minimal(self, name, phone="", email=""):
        """Create a real customer card, reusing an exact normalized contact."""
        name = text(name)
        phone_display, email_display = text(phone), text(email)
        phone_normalized, email_normalized = normalize_phone(phone), normalize_email(email)
        if not name:
            raise ValueError("Имя клиента обязательно.")
        if not phone_normalized and not email_normalized:
            raise ValueError("Укажите корректный телефон или email.")
        now = utc_now()
        created = False
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            phone_ids = self._candidate_ids(connection, "phone", phone_normalized, "purchases")
            email_ids = self._candidate_ids(connection, "email", email_normalized, "purchases")
            if phone_ids and email_ids and phone_ids != email_ids:
                raise ValueError("Телефон и email принадлежат разным клиентам.")
            candidates = phone_ids or email_ids
            if len(candidates) > 1:
                raise ValueError("Найдено несколько клиентов с таким контактом.")
            if candidates:
                customer_id = next(iter(candidates))
            else:
                cursor = connection.execute(
                    "INSERT INTO customers(name,name_fold,city,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (name, name.casefold(), "", now, now),
                )
                customer_id = int(cursor.lastrowid)
                created = True
                for kind, normalized, display in (
                    ("phone", phone_normalized, phone_display),
                    ("email", email_normalized, email_display),
                ):
                    if normalized:
                        connection.execute(
                            "INSERT INTO customer_contacts(customer_id,kind,normalized_value,display_value,source,masked,created_at,updated_at) VALUES(?,?,?,?, 'purchases',0,?,?)",
                            (customer_id, kind, normalized, display, now, now),
                        )
        return self.get(customer_id), created

    def customer_for_operation(self, operation_type, source, external_id):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT customer_id FROM customer_operations WHERE operation_type=? AND source=? AND external_id=?",
                (text(operation_type), text(source).casefold(), text(external_id)),
            ).fetchone()
        return int(row[0]) if row else None

    def operations(self, customer_id, operation_type=None, page=1, per_page=20):
        try:
            page, per_page = max(1, int(page)), int(per_page)
        except (TypeError, ValueError):
            page, per_page = 1, 20
        if per_page not in PAGE_SIZES:
            per_page = 20
        where, params = "customer_id=?", [customer_id]
        if operation_type in {"order", "sale", "repair"}:
            where += " AND operation_type=?"
            params.append(operation_type)
        with self.connection() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM customer_operations WHERE " + where, params).fetchone()[0])
            pages = max(1, int(math.ceil(float(total) / per_page)))
            page = min(page, pages)
            rows = connection.execute(
                "SELECT * FROM customer_operations WHERE " + where + " ORDER BY occurred_at DESC,id DESC LIMIT ? OFFSET ?",
                params + [per_page, (page - 1) * per_page],
            ).fetchall()
        return {"rows": [dict(row) for row in rows], "total": total, "page": page,
                "per_page": per_page, "pages": pages}
