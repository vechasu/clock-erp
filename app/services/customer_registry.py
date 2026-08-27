"""Canonical customer registry backed by a deploy-time migrated SQLite database."""

from __future__ import print_function

import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
    raw = text(value)
    if "@" not in raw:
        return ""
    local, domain = raw.rsplit("@", 1)
    normalized = local.casefold() + "@" + domain.casefold()
    return normalized if EMAIL_PATTERN.fullmatch(normalized) else ""


def display_city(value):
    value = text(value)
    return "Не указан" if not value or value.isdigit() else value


def real_activity_date(value):
    value = text(value)
    if not value:
        return ""
    try:
        parsed = datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    # Dates beyond the current year are technical fallbacks from incomplete exports.
    if parsed.year < 2000 or parsed.year > datetime.now().year:
        return ""
    return value


def masked_email(value):
    normalized = normalize_email(value)
    return bool(normalized and any(marker in normalized.casefold() for marker in MASKED_EMAIL_MARKERS))


def validate_database(path):
    path = Path(path)
    if not path.exists():
        raise sqlite3.OperationalError("customer registry migration required")
    connection = sqlite3.connect("file:{}?mode=ro".format(path.resolve()), uri=True)
    try:
        expected = {"registry_meta", "customers", "customer_external_ids",
                    "customer_contacts", "customer_operations", "customer_identity_conflicts",
                    "customer_duplicate_candidates", "customer_merge_audit", "customer_notes",
                    "customer_events"}
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
        # A contact match must not silently grow a transitive identity graph.  Only
        # the identifier that proved the match is attached; stable source IDs and
        # related orders may safely contribute both contacts.
        allowed_kinds = {"phone", "email"}
        if not existing and matched_by == "phone":
            allowed_kinds = {"phone"}
        elif not existing and matched_by == "email":
            allowed_kinds = {"email"}
        for kind, normalized, display, masked in (
            ("phone", phone, text(operation.get("phone")), 0),
            ("email", email, text(operation.get("email")), email_masked),
        ):
            if normalized and kind in allowed_kinds:
                connection.execute(
                    "INSERT OR IGNORE INTO customer_contacts(customer_id,kind,normalized_value,display_value,source,masked,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (customer_id, kind, normalized, display, source, masked, now, now),
                )
        payload = json.dumps(operation.get("payload") or {}, ensure_ascii=False, separators=(",", ":"))
        values = (
            customer_id, external_customer_id or None, text(operation.get("local_ref")) or None,
            text(operation.get("related_order_source")).casefold() or None,
            text(operation.get("related_order_id")) or None, text(operation.get("status")),
            real_activity_date(operation.get("occurred_at")), operation.get("amount"),
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
            "orders_count=(SELECT COUNT(DISTINCT source || ':' || external_id) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1 AND o.operation_type='order'),"
            "sales_count=(SELECT COUNT(DISTINCT source || ':' || external_id) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1 AND o.operation_type='sale' AND o.completed=1 AND o.cancelled=0),"
            "repairs_count=(SELECT COUNT(DISTINCT source || ':' || external_id) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1 AND o.operation_type='repair'),"
            "sales_amount=COALESCE((SELECT SUM(COALESCE(o.amount,0)) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1 AND o.operation_type='sale' AND o.completed=1 AND o.cancelled=0),0),"
            "last_sale_at=(SELECT MAX(occurred_at) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1 AND o.operation_type='sale' AND o.completed=1 AND o.cancelled=0),"
            "total_completed_amount=COALESCE((SELECT SUM(COALESCE(o.amount,0)) FROM customer_operations o WHERE o.customer_id=customers.id AND o.active=1 AND o.operation_type='sale' AND o.completed=1 AND o.cancelled=0),0)"
        )

    @staticmethod
    def _segment_sql(segment):
        today = datetime.now().date()
        boundaries = {
            "new": (today - timedelta(days=30)).isoformat(),
            "inactive": (today - timedelta(days=180)).isoformat(),
        }
        return {
            "new": ("c.first_operation_at>=?", [boundaries["new"]]),
            "repeat": ("c.sales_count>=2", []),
            "vip": ("(c.sales_count>=3 OR c.sales_amount>=100000)", []),
            "inactive": ("(c.sales_count>0 AND (c.last_sale_at IS NULL OR c.last_sale_at<?))", [boundaries["inactive"]]),
            "repair": ("c.repairs_count>0", []),
            "duplicates": ("EXISTS (SELECT 1 FROM customer_duplicate_candidates d WHERE d.status='open' AND (d.left_customer_id=c.id OR d.right_customer_id=c.id))", []),
        }.get(segment, ("1=1", []))

    def list(self, query="", page=1, per_page=50, filters=None, sort="last_activity", direction="desc"):
        filters = filters or {}
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
        clauses, params = ["c.merged_into_id IS NULL"], []
        if query:
            folded = "%{}%".format(query.casefold())
            phone = normalize_phone(query)
            search = ("(c.name_fold LIKE ? OR lower(c.city) LIKE ? OR EXISTS (SELECT 1 FROM customer_contacts cc WHERE cc.customer_id=c.id AND (cc.normalized_value LIKE ? OR lower(cc.display_value) LIKE ?)) OR EXISTS (SELECT 1 FROM customer_operations co WHERE co.customer_id=c.id AND (lower(co.source) LIKE ? OR lower(co.external_id) LIKE ? OR lower(COALESCE(co.local_ref,'')) LIKE ?)) OR EXISTS (SELECT 1 FROM customer_external_ids ce WHERE ce.customer_id=c.id AND (lower(ce.source) LIKE ? OR lower(ce.external_customer_id) LIKE ?)))")
            params = [folded] * 9
            if phone:
                search = search[:-1] + " OR EXISTS (SELECT 1 FROM customer_contacts cp WHERE cp.customer_id=c.id AND cp.kind='phone' AND cp.normalized_value=?))"
                params.append(phone)
            clauses.append(search)
        segment = text(filters.get("segment"))
        if segment:
            clause, values = self._segment_sql(segment)
            clauses.append(clause); params.extend(values)
        source = text(filters.get("source")).casefold()
        if source:
            clauses.append("EXISTS (SELECT 1 FROM customer_operations fs WHERE fs.customer_id=c.id AND fs.source=?)"); params.append(source)
        city = text(filters.get("city"))
        if city:
            clauses.append("c.city=?"); params.append(city)
        for key, sql, cast in (
            ("first_from", "c.first_operation_at>=?", str), ("first_to", "c.first_operation_at<=?", str),
            ("last_from", "c.last_operation_at>=?", str), ("last_to", "c.last_operation_at<=?", str),
            ("sales_min", "c.sales_count>=?", int), ("amount_min", "c.sales_amount>=?", float),
        ):
            if filters.get(key) not in (None, ""):
                try:
                    params.append(cast(filters[key])); clauses.append(sql)
                except (TypeError, ValueError):
                    pass
        contacts = text(filters.get("contacts"))
        if contacts == "complete": clauses.append("EXISTS (SELECT 1 FROM customer_contacts p WHERE p.customer_id=c.id AND p.kind='phone') AND EXISTS (SELECT 1 FROM customer_contacts e WHERE e.customer_id=c.id AND e.kind='email')")
        elif contacts == "missing": clauses.append("NOT EXISTS (SELECT 1 FROM customer_contacts cc WHERE cc.customer_id=c.id)")
        customer_ids = filters.get("customer_ids")
        if customer_ids is not None:
            safe_ids = [int(value) for value in customer_ids if str(value).isdigit()]
            if safe_ids:
                clauses.append("c.id IN ({})".format(",".join("?" for _ in safe_ids))); params.extend(safe_ids)
            else:
                clauses.append("1=0")
        where = " WHERE " + " AND ".join(clauses)
        order_fields = {"name": "c.name_fold", "purchases": "c.sales_amount", "history": "c.orders_count+c.sales_count+c.repairs_count", "last_activity": "COALESCE(c.last_operation_at,'')"}
        order = order_fields.get(sort, order_fields["last_activity"])
        direction = "ASC" if direction == "asc" else "DESC"
        with self.connection() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM customers c" + where, params).fetchone()[0])
            pages = max(1, int(math.ceil(float(total) / per_page)))
            page = min(page, pages)
            rows = connection.execute(
                "SELECT c.*, (SELECT display_value FROM customer_contacts WHERE customer_id=c.id AND kind='phone' ORDER BY id LIMIT 1) phone,"
                "(SELECT display_value FROM customer_contacts WHERE customer_id=c.id AND kind='email' ORDER BY id LIMIT 1) email,"
                "(SELECT group_concat(DISTINCT source) FROM customer_operations WHERE customer_id=c.id) sources "
                "FROM customers c" + where + " ORDER BY " + order + " " + direction + ",c.id DESC LIMIT ? OFFSET ?",
                params + [per_page, (page - 1) * per_page],
            ).fetchall()
            segment_counts = {}
            for key in ("all", "new", "repeat", "vip", "inactive", "repair", "duplicates"):
                clause, values = self._segment_sql(key)
                segment_counts[key] = int(connection.execute(
                    "SELECT COUNT(*) FROM customers c WHERE c.merged_into_id IS NULL AND " + clause, values
                ).fetchone()[0])
        prepared = []
        for row in rows:
            item = dict(row); item["city_display"] = display_city(item.get("city")); item["segments"] = self.segments(item)
            prepared.append(item)
        return {"rows": prepared, "total": total, "page": page,
                "per_page": per_page, "pages": pages, "segment_counts": segment_counts}

    @staticmethod
    def segments(customer):
        today = datetime.now().date()
        result = []
        first = text(customer.get("first_operation_at"))[:10]
        last_sale = text(customer.get("last_sale_at"))[:10]
        if first and first >= (today - timedelta(days=30)).isoformat(): result.append("Новый")
        if int(customer.get("sales_count") or 0) >= 2: result.append("Повторный")
        if int(customer.get("sales_count") or 0) >= 3 or float(customer.get("sales_amount") or 0) >= 100000: result.append("VIP")
        if int(customer.get("sales_count") or 0) and (not last_sale or last_sale < (today - timedelta(days=180)).isoformat()): result.append("Давно не покупал")
        if int(customer.get("repairs_count") or 0): result.append("Есть ремонт")
        return result or ["Без сегмента"]

    def get(self, customer_id):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT c.*, (SELECT display_value FROM customer_contacts WHERE customer_id=c.id AND kind='phone' ORDER BY id LIMIT 1) phone,"
                "(SELECT display_value FROM customer_contacts WHERE customer_id=c.id AND kind='email' ORDER BY id LIMIT 1) email,"
                "(SELECT group_concat(DISTINCT source) FROM customer_operations WHERE customer_id=c.id) sources FROM customers c WHERE c.id=?",
                (customer_id,),
            ).fetchone()
        if not row:
            return None
        customer = dict(row); customer["city_display"] = display_city(customer.get("city")); customer["segments"] = self.segments(customer)
        return customer

    def contacts(self, customer_id):
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT kind,display_value,source,created_at FROM customer_contacts WHERE customer_id=? ORDER BY kind,id",
                (int(customer_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def refresh_duplicate_candidates(self, connection):
        """Create review candidates without ever merging on a name alone."""
        now = utc_now()
        rows = connection.execute(
            "SELECT a.customer_id,b.customer_id,a.kind,a.normalized_value "
            "FROM customer_contacts a JOIN customer_contacts b ON b.kind=a.kind AND b.normalized_value=a.normalized_value "
            "AND b.customer_id>a.customer_id JOIN customers ca ON ca.id=a.customer_id JOIN customers cb ON cb.id=b.customer_id "
            "WHERE ca.merged_into_id IS NULL AND cb.merged_into_id IS NULL AND a.masked=0 AND b.masked=0 "
            "GROUP BY a.customer_id,b.customer_id,a.kind,a.normalized_value"
        ).fetchall()
        for left_id, right_id, kind, _value in rows:
            connection.execute(
                "INSERT OR IGNORE INTO customer_duplicate_candidates(left_customer_id,right_customer_id,score,reasons,status,created_at,updated_at) VALUES(?,?,90,?,'open',?,?)",
                (left_id, right_id, "Точное совпадение: {}".format("телефон" if kind == "phone" else "email"), now, now),
            )

    def duplicate_candidates(self, customer_id=None, limit=100):
        where, params = "d.status='open'", []
        if customer_id:
            where += " AND (d.left_customer_id=? OR d.right_customer_id=?)"; params.extend([int(customer_id)] * 2)
        with self.connection() as connection:
            self.refresh_duplicate_candidates(connection)
            rows = connection.execute(
                "SELECT d.*,a.name left_name,a.city left_city,b.name right_name,b.city right_city "
                "FROM customer_duplicate_candidates d JOIN customers a ON a.id=d.left_customer_id "
                "JOIN customers b ON b.id=d.right_customer_id WHERE " + where + " ORDER BY d.score DESC,d.id LIMIT ?",
                params + [min(max(int(limit), 1), 200)],
            ).fetchall()
        return [dict(row) for row in rows]

    def analytics(self, date_from="", date_to="", source="", city="", segment=""):
        filters = {"source": source, "city": city, "segment": segment,
                   "last_from": date_from, "last_to": date_to}
        listing = self.list(page=1, per_page=20, filters=filters)
        clauses, params = ["c.merged_into_id IS NULL"], []
        if date_from: clauses.append("c.last_operation_at>=?"); params.append(date_from)
        if date_to: clauses.append("c.last_operation_at<=?"); params.append(date_to)
        if source: clauses.append("EXISTS (SELECT 1 FROM customer_operations o WHERE o.customer_id=c.id AND o.source=?)"); params.append(source.casefold())
        if city: clauses.append("c.city=?"); params.append(city)
        if segment:
            clause, values = self._segment_sql(segment); clauses.append(clause); params.extend(values)
        where = " WHERE " + " AND ".join(clauses)
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*),SUM(CASE WHEN sales_count>0 THEN 1 ELSE 0 END),SUM(CASE WHEN sales_count>=2 THEN 1 ELSE 0 END),COALESCE(SUM(sales_amount),0),COALESCE(SUM(sales_count),0),SUM(CASE WHEN repairs_count>0 THEN 1 ELSE 0 END) FROM customers c" + where,
                params,
            ).fetchone()
            top = [dict(item) for item in connection.execute(
                "SELECT id,name,city,sales_count,sales_amount FROM customers c" + where + " ORDER BY sales_amount DESC,id LIMIT 10", params
            ).fetchall()]
            cities = [dict(item) for item in connection.execute(
                "SELECT CASE WHEN city='' OR city GLOB '[0-9]*' THEN 'Не указан' ELSE city END label,COUNT(*) value FROM customers c" + where + " GROUP BY label ORDER BY value DESC LIMIT 10", params
            ).fetchall()]
            sources = [dict(item) for item in connection.execute(
                "SELECT o.source label,COUNT(DISTINCT o.customer_id) value FROM customer_operations o JOIN customers c ON c.id=o.customer_id" + where.replace(" WHERE ", " WHERE ") + " GROUP BY o.source ORDER BY value DESC LIMIT 10", params
            ).fetchall()]
        total, buyers, repeat, revenue, sale_count, repairs = [float(value or 0) for value in row]
        return {"total": int(total), "buyers": int(buyers), "repeat": int(repeat),
                "repeat_share": round((repeat / buyers * 100) if buyers else 0, 1),
                "revenue": revenue, "average_check": (revenue / sale_count if sale_count else 0),
                "average_customer": (revenue / buyers if buyers else 0), "repairs": int(repairs),
                "top": top, "cities": cities, "sources": sources,
                "segment_counts": listing["segment_counts"]}

    def timeline(self, customer_id, event_type="", limit=100):
        clauses, params = ["customer_id=?"], [int(customer_id)]
        if event_type:
            clauses.append("event_type=?"); params.append(text(event_type))
        with self.connection() as connection:
            operation_rows = connection.execute(
                "SELECT operation_type event_type,source,external_id,occurred_at,status,local_ref FROM customer_operations WHERE customer_id=? AND active=1 ORDER BY occurred_at DESC,id DESC LIMIT ?",
                (int(customer_id), int(limit)),
            ).fetchall()
            note_rows = connection.execute(
                "SELECT 'comment' event_type,'erp' source,CAST(id AS TEXT) external_id,created_at occurred_at,body status,'' local_ref FROM customer_notes WHERE customer_id=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (int(customer_id), int(limit)),
            ).fetchall()
        rows = [dict(row) for row in operation_rows] + [dict(row) for row in note_rows]
        if event_type:
            rows = [row for row in rows if row["event_type"] == event_type]
        return sorted(rows, key=lambda row: (row.get("occurred_at") or "", row.get("external_id") or ""), reverse=True)[:limit]

    def add_note(self, customer_id, body, actor_id):
        body = text(body)
        if not body or len(body) > 10000:
            raise ValueError("Комментарий должен содержать от 1 до 10 000 символов.")
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM customers WHERE id=? AND merged_into_id IS NULL", (int(customer_id),)).fetchone():
                raise ValueError("Клиент не найден.")
            connection.execute(
                "INSERT INTO customer_notes(customer_id,body,actor_id,created_at) VALUES(?,?,?,?)",
                (int(customer_id), body, text(actor_id), now),
            )

    def merge(self, target_id, source_id, actor_id, idempotency_key):
        target_id, source_id = int(target_id), int(source_id)
        if target_id == source_id:
            raise ValueError("Нельзя объединить клиента с самим собой.")
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT * FROM customer_merge_audit WHERE idempotency_key=?", (text(idempotency_key),)).fetchone()
            if previous:
                return dict(previous), False
            target = connection.execute("SELECT * FROM customers WHERE id=?", (target_id,)).fetchone()
            source = connection.execute("SELECT * FROM customers WHERE id=?", (source_id,)).fetchone()
            if not target or not source or target["merged_into_id"] is not None or source["merged_into_id"] is not None:
                raise ValueError("Один из клиентов уже объединён или не существует.")
            snapshot_data = {
                "source": dict(source),
                "operation_ids": [row[0] for row in connection.execute("SELECT id FROM customer_operations WHERE customer_id=?", (source_id,))],
                "contact_ids": [row[0] for row in connection.execute("SELECT id FROM customer_contacts WHERE customer_id=?", (source_id,))],
                "contacts": [dict(row) for row in connection.execute("SELECT * FROM customer_contacts WHERE customer_id=?", (source_id,))],
                "external_id_ids": [row[0] for row in connection.execute("SELECT id FROM customer_external_ids WHERE customer_id=?", (source_id,))],
                "note_ids": [row[0] for row in connection.execute("SELECT id FROM customer_notes WHERE customer_id=?", (source_id,))],
            }
            snapshot = json.dumps(snapshot_data, ensure_ascii=False, sort_keys=True)
            connection.execute("UPDATE customer_external_ids SET customer_id=? WHERE customer_id=?", (target_id, source_id))
            connection.execute("UPDATE OR IGNORE customer_contacts SET customer_id=? WHERE customer_id=?", (target_id, source_id))
            connection.execute("DELETE FROM customer_contacts WHERE customer_id=?", (source_id,))
            connection.execute("UPDATE customer_operations SET customer_id=? WHERE customer_id=?", (target_id, source_id))
            connection.execute("UPDATE customer_notes SET customer_id=? WHERE customer_id=?", (target_id, source_id))
            connection.execute("UPDATE customers SET merged_into_id=?,updated_at=? WHERE id=?", (target_id, now, source_id))
            cursor = connection.execute(
                "INSERT INTO customer_merge_audit(action,target_customer_id,source_customer_id,actor_id,snapshot_json,created_at,idempotency_key) VALUES('merge',?,?,?,?,?,?)",
                (target_id, source_id, text(actor_id), snapshot, now, text(idempotency_key)),
            )
            connection.execute("UPDATE customer_duplicate_candidates SET status='merged',updated_at=? WHERE left_customer_id=? OR right_customer_id=?", (now, source_id, source_id))
            self.recompute(connection)
            row = connection.execute("SELECT * FROM customer_merge_audit WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row), True

    def unmerge(self, audit_id, actor_id):
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            audit = connection.execute("SELECT * FROM customer_merge_audit WHERE id=? AND action='merge'", (int(audit_id),)).fetchone()
            if not audit:
                raise ValueError("Объединение не найдено.")
            source_id, target_id = int(audit["source_customer_id"]), int(audit["target_customer_id"])
            source = connection.execute("SELECT merged_into_id FROM customers WHERE id=?", (source_id,)).fetchone()
            if not source or source[0] != target_id:
                raise ValueError("Объединение уже отменено или изменено.")
            snapshot = json.loads(audit["snapshot_json"] or "{}")
            connection.execute("UPDATE customers SET merged_into_id=NULL,updated_at=? WHERE id=?", (now, source_id))
            for table, key in (("customer_operations", "operation_ids"), ("customer_contacts", "contact_ids"),
                               ("customer_external_ids", "external_id_ids"), ("customer_notes", "note_ids")):
                identifiers = [int(value) for value in snapshot.get(key, [])]
                if identifiers:
                    connection.execute(
                        "UPDATE {} SET customer_id=? WHERE id IN ({})".format(table, ",".join("?" for _ in identifiers)),
                        [source_id] + identifiers,
                    )
            for contact in snapshot.get("contacts", []):
                connection.execute(
                    "INSERT OR IGNORE INTO customer_contacts(customer_id,kind,normalized_value,display_value,source,masked,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (source_id, contact["kind"], contact["normalized_value"], contact["display_value"],
                     contact["source"], contact["masked"], contact["created_at"], contact["updated_at"]),
                )
            connection.execute(
                "INSERT INTO customer_merge_audit(action,target_customer_id,source_customer_id,actor_id,snapshot_json,created_at,idempotency_key) VALUES('unmerge',?,?,?,?,?,?)",
                (target_id, source_id, text(actor_id), json.dumps(snapshot, ensure_ascii=False), now, "unmerge:{}".format(audit_id)),
            )
            self.recompute(connection)

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
