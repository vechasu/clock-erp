"""SQLite-backed queryable snapshot for the bounded Bitrix orders window."""

import json
import math
import os
import re
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.domain_schema_migrations import validate_orders_database
from app.services.customer_identity import link_order_safely


PAGE_SIZES = (20, 50, 100, 200)
DATE_QUERY = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2}|\d{4})$")
AMOUNT_QUERY = re.compile(r"^[\d\s.,₽]+$")
PHONE_QUERY = re.compile(r"^[+\d\s()\-]+$")
EXACT_ORDER_NUMBER_QUERY = re.compile(
    r"^\s*(?:(?:№|#|Nº)\s*)?(\d+)(?:\s*№)?\s*$",
    re.IGNORECASE,
)
LOGGER = logging.getLogger(__name__)

BITRIX_OWNED_FIELDS = (
    "id", "ID", "external_id", "external_order_id", "number",
    "ACCOUNT_NUMBER", "created_at", "date", "updated_at", "status",
    "status_name", "cancelled", "order_total", "price", "currency",
    "external_customer_id", "customer", "phone", "email", "country",
    "region", "city", "payment", "payment_system", "paid", "delivery",
    "delivery_type", "delivery_price", "delivery_price_source", "address",
    "delivery_address", "comment", "bitrix_comment", "items",
    "products", "products_count", "tracking", "track_number", "paid_name",
    "source", "source_name", "external_source", "source_kind", "status_known",
    "location_id", "products_total", "discount", "total",
    "calculation_complete", "calculation_consistent", "sync_state",
    "sync_missing",
)


def _text(value):
    return str(value or "").strip()


def normalize_exact_order_number_query(value):
    """Return an exact display number without ever coercing it to an integer."""
    match = EXACT_ORDER_NUMBER_QUERY.fullmatch(str(value or ""))
    return match.group(1) if match else None


def pagination_items(page, page_count):
    """Compact deterministic page window: 1 2 3 4 5 … last."""
    page = max(1, int(page))
    page_count = max(1, int(page_count))
    candidates = {1, page_count}
    candidates.update(range(max(1, page - 2), min(page_count, page + 2) + 1))
    if page <= 4:
        candidates.update(range(1, min(page_count, 5) + 1))
    if page >= page_count - 3:
        candidates.update(range(max(1, page_count - 4), page_count + 1))
    result = []
    previous = None
    for number in sorted(candidates):
        if previous is not None and number - previous > 1:
            result.append(None)
        result.append(number)
        previous = number
    return result


def _phone_digits(value):
    return "".join(character for character in _text(value) if character.isdigit())


def _amount_search(value):
    if value in (None, ""):
        return ""
    try:
        amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    fixed = format(amount.quantize(Decimal("0.01")), "f")
    whole = fixed[:-3] if fixed.endswith(".00") else fixed
    return "{} {} {}".format(fixed, fixed.replace(".", ","), whole)


def _created_sort(value):
    raw = _text(value)
    if not raw:
        return ""
    normalized = raw.replace("Z", "+00:00")
    for candidate in (normalized, normalized.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate).strftime("%Y-%m-%dT%H:%M:%S")
        except (AttributeError, ValueError):
            pass
    for candidate, format_string in (
        (raw[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"),
        (raw[:16].replace("T", " "), "%Y-%m-%d %H:%M"),
        (raw[:16], "%d.%m.%Y %H:%M"),
        (raw[:10], "%d.%m.%Y"),
        (raw[:10], "%Y-%m-%d"),
    ):
        try:
            return datetime.strptime(candidate, format_string).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        except ValueError:
            continue
    return raw


def _date_search(value):
    sortable = _created_sort(value)
    try:
        parsed = datetime.strptime(sortable[:10], "%Y-%m-%d")
    except ValueError:
        return _text(value).casefold()
    return "{} {} {}".format(
        parsed.strftime("%d.%m.%Y"),
        parsed.strftime("%d.%m.%y"),
        parsed.strftime("%Y-%m-%d"),
    )


def order_item_units(order):
    products = order.get("products") or order.get("items") or []
    if not products:
        existing = order.get("item_units")
        return existing if existing not in (None, "") else None
    total = Decimal("0")
    for product in products:
        raw = product.get("quantity", product.get("QUANTITY", 1))
        try:
            total += Decimal(str(raw if raw not in (None, "") else 1))
        except (InvalidOperation, TypeError, ValueError):
            total += Decimal("1")
    return int(total) if total == total.to_integral_value() else float(total)


def _extra_search(order):
    """Flatten list-safe order details once so list search stays one SQL query."""
    values = [
        order.get("source"), order.get("source_name"), order.get("email"),
        order.get("payment"), order.get("payment_system"), order.get("delivery"),
        order.get("country"), order.get("region"), order.get("city"),
        order.get("address"), order.get("delivery_address"), order.get("comment"),
    ]
    for product in order.get("items") or order.get("products") or []:
        values.extend(product.get(key) for key in (
            "name", "model", "article", "sku", "barcode", "xml_id",
            "brand", "category", "PRODUCT_NAME", "NAME", "ARTICLE", "SKU",
        ))
    return " ".join(_text(value) for value in values if _text(value)).casefold()


class OrdersSnapshotStore:
    def __init__(self, path=None):
        configured = path or os.getenv("ORDERS_DATABASE_PATH")
        self.path = Path(configured) if configured else Path("instance/orders.db")
        self._schema_validated = False

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self):
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        if not self._schema_validated:
            validate_orders_database(self.path)
            self._schema_validated = True
        return self

    def loaded_at(self):
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM orders_snapshot_meta WHERE key='loaded_at'"
            ).fetchone()
        try:
            return float(row["value"]) if row is not None else 0
        except (TypeError, ValueError):
            return 0

    def count(self):
        self.initialize()
        with self.connection() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM orders_snapshot"
            ).fetchone()[0])

    def source_ids(self, source):
        """Return durable identities for one source before/after a sync."""
        self.initialize()
        with self.connection() as connection:
            return {
                str(row["order_id"])
                for row in connection.execute(
                    "SELECT order_id FROM orders_snapshot WHERE source = ?",
                    (str(source or "").strip(),),
                ).fetchall()
            }

    @staticmethod
    def _merge_bitrix_payload(
        previous, incoming, preserve_existing_local=False
    ):
        """Update only fields owned by Bitrix and retain every ERP-only key."""
        merged = dict(previous or {})
        incoming = dict(incoming or {})
        for field in BITRIX_OWNED_FIELDS:
            if field not in incoming:
                continue
            if (
                preserve_existing_local
                and previous
                and field in {"status", "status_name", "tracking", "track_number"}
            ):
                continue
            value = incoming[field]
            if field in {"items", "products"} and not value and merged.get(field):
                continue
            merged[field] = value
        return merged

    def _upsert_bitrix_in_connection(
        self, connection, orders, loaded_at, preserve_existing_local=False
    ):
        result = {"added": 0, "updated": 0, "skipped": 0}
        for position, incoming in enumerate(orders):
            order_id = _text(
                incoming.get("external_id") or incoming.get("id")
                or incoming.get("ID")
            )
            if not order_id:
                result["skipped"] += 1
                continue
            existing = connection.execute(
                "SELECT order_id, item_units, detail_loaded, payload_json, customer_id "
                "FROM orders_snapshot WHERE source = 'tictactoy' "
                "AND external_order_id = ?",
                (order_id,),
            ).fetchone()
            previous = json.loads(existing["payload_json"]) if existing else {}
            order = self._merge_bitrix_payload(
                previous, incoming,
                preserve_existing_local=preserve_existing_local,
            )
            order["id"] = order_id
            order["external_id"] = order_id
            order["source"] = "tictactoy"
            order["source_name"] = order.get("source_name") or "Tictactoy"
            incoming_has_items = bool(incoming.get("items") or incoming.get("products"))
            item_units = order_item_units(order)
            if existing and not incoming_has_items:
                item_units = existing["item_units"]
            detail_loaded = int(bool(
                incoming_has_items
                or (existing is not None and existing["detail_loaded"])
            ))
            customer_id = existing["customer_id"] if existing else None
            if customer_id is None:
                customer_id = link_order_safely(
                    connection, order, logger=LOGGER
                )["customer_id"]
            created = order.get("created_at") or order.get("date")
            total = (
                order.get("order_total")
                if order.get("order_total") is not None
                else order.get("price")
            )
            try:
                source_position = -int(order_id)
            except ValueError:
                source_position = position
            payload_json = json.dumps(
                order, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            values = (
                source_position,
                _text(order.get("number") or order_id).casefold(),
                _text(order.get("customer")).casefold(),
                _extra_search(order),
                _phone_digits(order.get("phone")),
                _amount_search(total),
                _date_search(created),
                _created_sort(created),
                _text(order.get("status")).upper(),
                item_units,
                detail_loaded,
                payload_json,
                loaded_at,
                customer_id,
            )
            if existing:
                if (
                    existing["payload_json"] == payload_json
                    and existing["item_units"] == item_units
                    and int(existing["detail_loaded"]) == detail_loaded
                ):
                    result["skipped"] += 1
                    continue
                connection.execute(
                    "UPDATE orders_snapshot SET source_position=?, number_fold=?, "
                    "customer_fold=?, extra_fold=?, phone_digits=?, amount_search=?, "
                    "date_search=?, created_sort=?, status=?, item_units=?, "
                    "detail_loaded=?, payload_json=?, loaded_at=?, "
                    "customer_id=COALESCE(customer_id, ?) WHERE source='tictactoy' "
                    "AND external_order_id=?",
                    values + (order_id,),
                )
                result["updated"] += 1
            else:
                connection.execute(
                    "INSERT INTO orders_snapshot (order_id, source, external_order_id, "
                    "source_position, number_fold, customer_fold, extra_fold, phone_digits, "
                    "amount_search, date_search, created_sort, status, item_units, "
                    "detail_loaded, payload_json, loaded_at, customer_id) "
                    "VALUES (?, 'tictactoy', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (order_id, order_id) + values,
                )
                result["added"] += 1
        return result

    def upsert_bitrix(
        self, orders, loaded_at, checkpoint_cursor=None, checkpoint_complete=False,
        preserve_existing_local=False,
    ):
        """Commit one idempotent history batch and its resume cursor together."""
        self.initialize()
        loaded_at = float(loaded_at or 0)
        with self.connection() as connection:
            result = self._upsert_bitrix_in_connection(
                connection, orders, loaded_at,
                preserve_existing_local=preserve_existing_local,
            )
            connection.execute(
                "INSERT OR REPLACE INTO orders_snapshot_meta (key, value) "
                "VALUES ('loaded_at', ?)", (str(loaded_at),)
            )
            if checkpoint_cursor is not None or checkpoint_complete:
                checkpoint = {
                    "cursor": int(checkpoint_cursor or 0),
                    "complete": bool(checkpoint_complete),
                    "updated_at": loaded_at,
                }
                connection.execute(
                    "INSERT OR REPLACE INTO orders_snapshot_meta (key, value) "
                    "VALUES ('bitrix_history_checkpoint', ?)",
                    (json.dumps(checkpoint, sort_keys=True),),
                )
        return result

    def history_checkpoint(self):
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM orders_snapshot_meta "
                "WHERE key='bitrix_history_checkpoint'"
            ).fetchone()
        if row is None:
            return {"cursor": 0, "complete": False}
        try:
            state = json.loads(row["value"])
            return {
                "cursor": int(state.get("cursor") or 0),
                "complete": bool(state.get("complete")),
            }
        except (TypeError, ValueError):
            return {"cursor": 0, "complete": False}

    def replace(self, orders, loaded_at):
        """Compatibility entry point: recent refreshes are additive, never destructive."""
        return self.upsert_bitrix(orders, loaded_at)

    def upsert_wildberries(self, orders):
        """Idempotently store each WB assembly order as its own record."""
        self.initialize()
        added = 0
        updated = 0
        with self.connection() as connection:
            for order in orders:
                wb_order_id = _text(order.get("wb_order_id"))
                if not wb_order_id:
                    continue
                order_id = "wb:" + wb_order_id
                existing = connection.execute(
                    "SELECT order_id, customer_id FROM orders_snapshot "
                    "WHERE source = 'wildberries' AND external_order_id = ?",
                    (wb_order_id,),
                ).fetchone()
                created = order.get("created_at") or order.get("date")
                total = order.get("order_total")
                if total is None:
                    total = order.get("price")
                try:
                    source_position = -int(wb_order_id)
                except ValueError:
                    source_position = -int(datetime.now().timestamp())
                values = (
                    source_position,
                    _text(order.get("number") or wb_order_id).casefold(),
                    _text(order.get("customer")).casefold(),
                    " ".join(_text(value) for value in (
                        order.get("source_name"), order.get("wb_order_id"),
                        order.get("order_uid"), order.get("rid"),
                        order.get("article"), " ".join(order.get("skus") or []),
                        order.get("nm_id"), order.get("chrt_id"),
                    )).casefold(),
                    _phone_digits(order.get("phone")),
                    _amount_search(total),
                    _date_search(created),
                    _created_sort(created),
                    _text(order.get("status")),
                    order_item_units(order),
                    json.dumps(order, ensure_ascii=False, separators=(",", ":")),
                    float(datetime.now().timestamp()),
                )
                customer_id = existing["customer_id"] if existing else None
                if customer_id is None:
                    customer_id = link_order_safely(
                        connection, order, logger=LOGGER
                    )["customer_id"]
                if existing:
                    connection.execute(
                        "UPDATE orders_snapshot SET source_position = ?, number_fold = ?, "
                        "customer_fold = ?, extra_fold = ?, phone_digits = ?, amount_search = ?, "
                        "date_search = ?, created_sort = ?, status = ?, item_units = ?, "
                        "detail_loaded = 1, payload_json = ?, loaded_at = ?, "
                        "customer_id = COALESCE(customer_id, ?) "
                        "WHERE source = 'wildberries' AND external_order_id = ?",
                        values + (customer_id, wb_order_id),
                    )
                    updated += 1
                else:
                    connection.execute(
                        "INSERT INTO orders_snapshot (order_id, source, external_order_id, "
                        "source_position, number_fold, customer_fold, extra_fold, phone_digits, "
                        "amount_search, date_search, created_sort, status, item_units, "
                        "detail_loaded, payload_json, loaded_at, customer_id) "
                        "VALUES (?, 'wildberries', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                        (order_id, wb_order_id) + values + (customer_id,),
                    )
                    added += 1
        return {"added": added, "updated": updated}

    def ensure(self, orders, loaded_at):
        current = self.loaded_at()
        if self.count() == 0 or float(loaded_at or 0) > current:
            self.replace(orders, loaded_at)

    def get(self, order_id):
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json, item_units, customer_id FROM orders_snapshot "
                "WHERE order_id = ?",
                (_text(order_id),),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["item_units"] = row["item_units"]
        payload["customer_id"] = row["customer_id"]
        return payload

    def get_by_identity(self, source, external_order_id):
        """Resolve the canonical local record by source identity."""
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json, item_units, customer_id FROM orders_snapshot "
                "WHERE source = ? AND external_order_id = ?",
                (_text(source).casefold(), _text(external_order_id)),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["item_units"] = row["item_units"]
        payload["customer_id"] = row["customer_id"]
        return payload

    def set_item_units(self, order_id, item_units):
        self.initialize()
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE orders_snapshot SET item_units = ? WHERE order_id = ?",
                (item_units, _text(order_id)),
            )
        return cursor.rowcount > 0

    def enrich_from_detail(self, order_id, detail):
        """Persist list-safe authoritative fields from one read-only detail fetch."""
        self.initialize()
        order_id = _text(order_id)
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM orders_snapshot WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if row is None:
                return False
            payload = json.loads(row["payload_json"])
            for field in (
                "number", "customer", "phone", "email", "country", "region", "city", "order_total", "price",
                "created_at", "date", "updated_at", "status", "status_name",
                "source", "source_name", "payment", "payment_system", "paid",
                "delivery", "address", "delivery_address", "comment", "items",
                "products", "products_count",
            ):
                value = detail.get(field)
                if value not in (None, ""):
                    payload[field] = value
            units = order_item_units(detail)
            created = payload.get("created_at") or payload.get("date")
            total = (
                payload.get("order_total")
                if payload.get("order_total") is not None
                else payload.get("price")
            )
            cursor = connection.execute(
                "UPDATE orders_snapshot SET number_fold = ?, customer_fold = ?, "
                "extra_fold = ?, phone_digits = ?, amount_search = ?, date_search = ?, "
                "created_sort = ?, status = ?, item_units = ?, detail_loaded = 1, "
                "payload_json = ? WHERE order_id = ?",
                (
                    _text(payload.get("number") or order_id).casefold(),
                    _text(payload.get("customer")).casefold(),
                    _extra_search(payload),
                    _phone_digits(payload.get("phone")),
                    _amount_search(total),
                    _date_search(created),
                    _created_sort(created),
                    _text(payload.get("status")).upper(),
                    units,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    order_id,
                ),
            )
            customer_id = link_order_safely(
                connection, payload, logger=LOGGER
            )["customer_id"]
            if customer_id is not None:
                connection.execute(
                    "UPDATE orders_snapshot SET customer_id = COALESCE(customer_id, ?) "
                    "WHERE order_id = ?", (customer_id, order_id)
                )
        return cursor.rowcount > 0

    def missing_detail_ids(self, limit=200):
        self.initialize()
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT order_id FROM orders_snapshot WHERE source = 'tictactoy' "
                "AND detail_loaded = 0 "
                "ORDER BY source_position, order_id LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [row["order_id"] for row in rows]

    def query(self, args, now=None, allowed_order_ids=None):
        self.initialize()
        query = _text(args.get("q"))
        exact_candidate = normalize_exact_order_number_query(query)
        exact_number = None
        if exact_candidate is not None:
            with self.connection() as connection:
                exact_rows = connection.execute(
                    "SELECT order_id FROM orders_snapshot WHERE "
                    "number_fold = ? OR order_id = ? OR external_order_id = ?",
                    (exact_candidate.casefold(), exact_candidate, exact_candidate),
                ).fetchall()
            exact_ids = {row["order_id"] for row in exact_rows}
            if allowed_order_ids is not None:
                exact_ids.intersection_update(str(value) for value in allowed_order_ids)
            if exact_ids:
                exact_number = exact_candidate
        status = _text(args.get("status") or "all").upper()
        source = _text(args.get("source") or "all").casefold()
        period = _text(args.get("period") or "all")
        raw_page_size = _text(args.get("page_size") or 50).casefold()
        try:
            page_size = int(raw_page_size)
        except (TypeError, ValueError):
            page_size = 50
        if page_size not in PAGE_SIZES:
            page_size = 50
        try:
            page = max(1, int(args.get("page") or 1))
        except (TypeError, ValueError):
            page = 1

        clauses = []
        parameters = []
        if exact_number is not None:
            clauses.append(
                "(number_fold = ? OR order_id = ? OR external_order_id = ?)"
            )
            parameters.extend([exact_number.casefold(), exact_number, exact_number])
            page = 1
            page_size = "all"
        elif query:
            folded = "%{}%".format(query.casefold())
            search_clauses = [
                "number_fold LIKE ?",
                "customer_fold LIKE ?",
                "extra_fold LIKE ?",
            ]
            search_parameters = [folded, folded, folded]
            digits = _phone_digits(query)
            if digits and PHONE_QUERY.match(query):
                search_clauses.append("phone_digits LIKE ?")
                search_parameters.append("%{}%".format(digits))
            if AMOUNT_QUERY.match(query):
                amount = _amount_search(query.replace("₽", ""))
                if amount:
                    normalized_amount = amount.split()[0]
                    search_clauses.append("amount_search LIKE ?")
                    search_parameters.append("%{}%".format(normalized_amount))
            date_match = DATE_QUERY.match(query)
            if date_match:
                day, month, year = date_match.groups()
                date_value = "{}.{}.{}".format(day, month, year)
                search_clauses.append("date_search LIKE ?")
                search_parameters.append("%{}%".format(date_value))
            clauses.append("(" + " OR ".join(search_clauses) + ")")
            parameters.extend(search_parameters)
        if exact_number is None and status != "ALL":
            clauses.append("status = ?")
            parameters.append(status)
        if exact_number is None and source in {"tictactoy", "wildberries"}:
            clauses.append("source = ?")
            parameters.append(source)
        if exact_number is None and period in {"today", "7d", "30d"}:
            reference = now or datetime.now()
            days = 0 if period == "today" else 7 if period == "7d" else 30
            threshold = (reference - timedelta(days=days)).strftime("%Y-%m-%d")
            clauses.append("created_sort >= ?")
            parameters.append(threshold)
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""

        with self.connection() as connection:
            if allowed_order_ids is not None:
                connection.execute(
                    "CREATE TEMP TABLE IF NOT EXISTS allowed_order_ids "
                    "(order_id TEXT PRIMARY KEY)"
                )
                connection.execute("DELETE FROM allowed_order_ids")
                connection.executemany(
                    "INSERT OR IGNORE INTO allowed_order_ids(order_id) VALUES(?)",
                    [(str(value),) for value in allowed_order_ids],
                )
                clauses.append(
                    "order_id IN (SELECT order_id FROM temp.allowed_order_ids)"
                )
                where_sql = " WHERE " + " AND ".join(clauses)
            total = int(connection.execute(
                "SELECT COUNT(*) FROM orders_snapshot" + where_sql,
                parameters,
            ).fetchone()[0])
            effective_page_size = max(total, 1) if page_size == "all" else page_size
            page_count = max(1, int(math.ceil(float(total) / effective_page_size)))
            page = min(page, page_count)
            rows = connection.execute(
                "SELECT payload_json, item_units, customer_id FROM orders_snapshot"
                + where_sql
                + " ORDER BY created_sort DESC, order_id DESC LIMIT ? OFFSET ?",
                parameters + [effective_page_size, (page - 1) * effective_page_size],
            ).fetchall()
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM orders_snapshot "
                "GROUP BY status"
            ).fetchall()
            physical_total = int(connection.execute(
                "SELECT COUNT(*) FROM orders_snapshot"
            ).fetchone()[0])
        result_rows = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["item_units"] = row["item_units"]
            payload["customer_id"] = row["customer_id"]
            result_rows.append(payload)
        counts = {row["status"]: int(row["count"]) for row in status_rows}
        return {
            "rows": result_rows,
            "total": total,
            "physical_total": physical_total,
            "page": page,
            "page_size": page_size,
            "page_count": page_count,
            "page_items": pagination_items(page, page_count),
            "exact_number": (
                exact_candidate if exact_number is not None or total == 0 else None
            ),
            "kpis": {
                "total": physical_total,
                "unconfirmed": counts.get("N", 0),
                "confirmed": counts.get("A", 0),
                "assembled": counts.get("D", 0),
                "refused": counts.get("C", 0),
                "active": (
                    counts.get("N", 0)
                    + counts.get("A", 0)
                    + counts.get("D", 0)
                ),
            },
        }
