"""Transactional purchase-request, plan and supplier-order workflow."""

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app.purchases_migrations import verify_database


REQUEST_STATUSES = (
    "new", "review", "planned", "ordered", "arrived", "notified", "sold", "closed",
)
ORDER_STATUSES = ("draft", "ordered", "partially_received", "received", "cancelled")
CHANNELS = ("whatsapp", "telegram", "email", "call", "website", "personal", "other")
PAGE_SIZES = (20, 50, 100, 200)


class PurchaseValidationError(ValueError):
    def __init__(self, message, field=""):
        super().__init__(message)
        self.field = field


class PurchaseNotFoundError(LookupError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def moscow_now():
    return datetime.now(timezone(timedelta(hours=3))).replace(microsecond=0).isoformat()


def text(value, maximum=1000):
    return " ".join(str(value or "").split())[:maximum]


def folded(value):
    return text(value, 500).casefold()


def parse_date(value, field, allow_time=False):
    value = text(value, 40)
    if not value:
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed.isoformat(timespec="seconds") if allow_time else parsed.date().isoformat()
    except ValueError:
        raise PurchaseValidationError("Укажите корректную дату.", field)


def positive_int(value, field, default=None):
    if value in (None, "") and default is not None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise PurchaseValidationError("Укажите целое количество.", field)
    if result < 1 or result > 100000:
        raise PurchaseValidationError("Количество должно быть от 1 до 100000.", field)
    return result


def money(value, field, nullable=True):
    if value in (None, "") and nullable:
        return None
    try:
        result = round(float(value), 2)
    except (TypeError, ValueError):
        raise PurchaseValidationError("Укажите корректную сумму.", field)
    if result < 0 or result > 1000000000:
        raise PurchaseValidationError("Сумма вне допустимого диапазона.", field)
    return result


def safe_url(value, field):
    value = text(value, 1000)
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise PurchaseValidationError("Укажите безопасную ссылку http или https.", field)
    return value


class PurchaseStore:
    def __init__(self, path=None):
        configured = path or os.getenv("ERP_PURCHASES_DATABASE", "").strip()
        self.path = Path(configured) if configured else Path("instance/purchases.db")
        self._validated = False

    def initialize(self):
        if not self._validated:
            verify_database(self.path)
            self._validated = True
        return self

    def connect(self):
        self.initialize()
        connection = sqlite3.connect(str(self.path), timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    @staticmethod
    def _request_values(payload, partial=False):
        result = {}
        if not partial or "customer_id" in payload:
            result["customer_id"] = positive_int(payload.get("customer_id"), "customer_id")
        if not partial or "product_id" in payload:
            raw = payload.get("product_id")
            result["product_id"] = positive_int(raw, "product_id") if raw not in (None, "") else None
        for field, maximum in (
            ("product_name", 300), ("brand", 160), ("model", 200), ("article", 160),
            ("product_url", 1000), ("image_url", 1000), ("description", 3000),
            ("customer_comment", 3000), ("internal_note", 5000),
        ):
            if not partial or field in payload:
                result[field] = text(payload.get(field), maximum)
        for field in ("product_url", "image_url"):
            if field in result:
                result[field] = safe_url(result[field], field)
        if not partial or "quantity" in payload:
            result["quantity"] = positive_int(payload.get("quantity"), "quantity", 1)
        if not partial or "target_price" in payload:
            result["target_price"] = money(payload.get("target_price"), "target_price")
        if not partial or "channel" in payload:
            channel = folded(payload.get("channel") or "other")
            if channel not in CHANNELS:
                raise PurchaseValidationError("Неизвестный канал обращения.", "channel")
            result["channel"] = channel
        if not partial or "requested_at" in payload:
            result["requested_at"] = parse_date(payload.get("requested_at") or moscow_now(), "requested_at", True)
        if not partial or "valid_until" in payload:
            result["valid_until"] = parse_date(payload.get("valid_until"), "valid_until")
        if not partial or "status" in payload:
            status = folded(payload.get("status") or "new")
            if status not in REQUEST_STATUSES:
                raise PurchaseValidationError("Неизвестный статус запроса.", "status")
            result["status"] = status
        return result

    @staticmethod
    def _validate_product_presence(values):
        if not values.get("product_id") and not any(values.get(key) for key in ("product_name", "brand", "model", "description")):
            raise PurchaseValidationError("Укажите существующий товар или понятное описание часов.", "product_name")

    @staticmethod
    def _history(connection, request_id, action, actor_id, old_status=None, new_status=None,
                 comment="", details=None, now=None):
        connection.execute(
            "INSERT INTO purchase_request_history(request_id,action,old_status,new_status,comment,actor_id,created_at,details_json) VALUES(?,?,?,?,?,?,?,?)",
            (request_id, action, old_status, new_status, text(comment, 1000), int(actor_id),
             now or utc_now(), json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
        )

    def create_request(self, payload, actor_id, customer_exists, product_resolver):
        values = self._request_values(payload)
        if not customer_exists(values["customer_id"]):
            raise PurchaseValidationError("Клиент не найден.", "customer_id")
        if values["product_id"]:
            product = product_resolver(values["product_id"])
            if not product:
                raise PurchaseValidationError("Товар не найден.", "product_id")
            for key in ("product_name", "brand", "model", "article", "image_url"):
                values[key] = text(product.get(key) or values.get(key), 1000)
        self._validate_product_presence(values)
        now = utc_now()
        fields = ("customer_id","product_id","product_name","brand","model","article","product_url",
                  "image_url","description","quantity","target_price","channel","requested_at","valid_until",
                  "customer_comment","internal_note","status")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO purchase_requests({0},created_at,updated_at,created_by,updated_by) VALUES({1},?,?,?,?)".format(
                    ",".join(fields), ",".join("?" for _ in fields)),
                tuple(values[field] for field in fields) + (now, now, int(actor_id), int(actor_id)),
            )
            request_id = int(cursor.lastrowid)
            self._history(connection, request_id, "created", actor_id, new_status=values["status"], now=now)
            connection.commit()
        return self.get_request(request_id)

    def get_request(self, request_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM purchase_requests WHERE id=?", (int(request_id),)).fetchone()
            if not row:
                raise PurchaseNotFoundError("Запрос не найден.")
            history = connection.execute(
                "SELECT * FROM purchase_request_history WHERE request_id=? ORDER BY created_at DESC,id DESC",
                (int(request_id),),
            ).fetchall()
            plan = connection.execute(
                "SELECT p.* FROM purchase_plan_items p JOIN purchase_plan_requests l ON l.plan_item_id=p.id WHERE l.request_id=?",
                (int(request_id),),
            ).fetchone()
            order = connection.execute(
                "SELECT o.*,i.id order_item_id,i.quantity ordered_quantity,i.received_quantity FROM supplier_orders o JOIN supplier_order_items i ON i.order_id=o.id JOIN supplier_order_requests r ON r.order_item_id=i.id WHERE r.request_id=? ORDER BY o.id DESC LIMIT 1",
                (int(request_id),),
            ).fetchone()
        result = dict(row)
        result["history"] = [dict(item) for item in history]
        result["plan_item"] = dict(plan) if plan else None
        result["supplier_order"] = dict(order) if order else None
        return result

    def update_request(self, request_id, payload, actor_id, customer_exists, product_resolver):
        current = self.get_request(request_id)
        values = self._request_values(payload, partial=True)
        merged = dict(current)
        merged.update(values)
        if "customer_id" in values and not customer_exists(values["customer_id"]):
            raise PurchaseValidationError("Клиент не найден.", "customer_id")
        if "product_id" in values and values["product_id"]:
            product = product_resolver(values["product_id"])
            if not product:
                raise PurchaseValidationError("Товар не найден.", "product_id")
            for key in ("product_name", "brand", "model", "article", "image_url"):
                merged[key] = text(product.get(key) or merged.get(key), 1000)
                values[key] = merged[key]
        self._validate_product_presence(merged)
        if not values:
            return current
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            fields = sorted(values)
            connection.execute(
                "UPDATE purchase_requests SET {0},updated_at=?,updated_by=? WHERE id=?".format(
                    ",".join("{}=?".format(field) for field in fields)),
                [values[field] for field in fields] + [now, int(actor_id), int(request_id)],
            )
            action = "status_changed" if "status" in values and values["status"] != current["status"] else "edited"
            self._history(connection, request_id, action, actor_id, current["status"],
                          values.get("status", current["status"]), payload.get("status_comment"),
                          {"fields": fields}, now)
            connection.commit()
        return self.get_request(request_id)

    def archive_request(self, request_id, archived, actor_id, comment=""):
        current = self.get_request(request_id)
        new_status = "closed" if archived else "review"
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE purchase_requests SET archived=?,status=?,updated_at=?,updated_by=? WHERE id=?",
                (int(bool(archived)), new_status, now, int(actor_id), int(request_id)),
            )
            self._history(connection, request_id, "archived" if archived else "reopened", actor_id,
                          current["status"], new_status, comment, now=now)
            connection.commit()
        return self.get_request(request_id)

    @staticmethod
    def _grouping_key(request_row):
        if request_row["product_id"]:
            return "product:{}".format(request_row["product_id"])
        parts = [folded(request_row[key]) for key in ("brand", "model", "article")]
        if all(parts):
            return "unknown:" + "|".join(parts)
        return "request:{}".format(request_row["id"])

    def add_to_plan(self, request_id, actor_id):
        current = self.get_request(request_id)
        key, now = self._grouping_key(current), utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT id FROM purchase_plan_items WHERE grouping_key=?", (key,)).fetchone()
            if row:
                plan_id = int(row[0])
                connection.execute("UPDATE purchase_plan_items SET status='active',updated_at=?,updated_by=? WHERE id=?", (now, int(actor_id), plan_id))
            else:
                cursor = connection.execute(
                    "INSERT INTO purchase_plan_items(grouping_key,product_id,product_name,brand,model,article,status,created_at,updated_at,updated_by) VALUES(?,?,?,?,?,?,'active',?,?,?)",
                    (key, current["product_id"], current["product_name"], current["brand"], current["model"], current["article"], now, now, int(actor_id)),
                )
                plan_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT OR IGNORE INTO purchase_plan_requests(plan_item_id,request_id,created_at) VALUES(?,?,?)",
                (plan_id, int(request_id), now),
            )
            if current["status"] not in ("ordered", "arrived", "notified", "sold"):
                connection.execute("UPDATE purchase_requests SET status='planned',updated_at=?,updated_by=? WHERE id=?", (now, int(actor_id), int(request_id)))
                self._history(connection, request_id, "added_to_plan", actor_id, current["status"], "planned", now=now)
            connection.commit()
        return plan_id

    def set_plan_quantity(self, plan_id, quantity, actor_id):
        value = 0 if str(quantity) == "0" else positive_int(quantity, "actual_quantity")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT actual_quantity FROM purchase_plan_items WHERE id=? AND status='active'", (int(plan_id),)).fetchone()
            if not current:
                raise PurchaseNotFoundError("Позиция плана не найдена.")
            now = utc_now()
            cursor = connection.execute("UPDATE purchase_plan_items SET actual_quantity=?,updated_at=?,updated_by=? WHERE id=?", (value, now, int(actor_id), int(plan_id)))
            if cursor.rowcount != 1:
                raise PurchaseNotFoundError("Позиция плана не найдена.")
            for row in connection.execute("SELECT request_id FROM purchase_plan_requests WHERE plan_item_id=?", (int(plan_id),)):
                self._history(connection, row[0], "plan_quantity_changed", actor_id,
                              details={"old": current[0], "new": value}, now=now)
            connection.commit()

    def remove_plan_item(self, plan_id, actor_id):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = utc_now()
            cursor = connection.execute("UPDATE purchase_plan_items SET status='removed',updated_at=?,updated_by=? WHERE id=? AND status='active'", (now, int(actor_id), int(plan_id)))
            if cursor.rowcount != 1:
                raise PurchaseNotFoundError("Активная позиция плана не найдена.")
            for row in connection.execute("SELECT request_id FROM purchase_plan_requests WHERE plan_item_id=?", (int(plan_id),)):
                current = connection.execute("SELECT status FROM purchase_requests WHERE id=?", (row[0],)).fetchone()[0]
                new_status = "review" if current == "planned" else current
                connection.execute("UPDATE purchase_requests SET status=?,updated_at=?,updated_by=? WHERE id=?", (new_status, now, int(actor_id), row[0]))
                self._history(connection, row[0], "removed_from_plan", actor_id, current, new_status, now=now)
            connection.commit()

    def list_plan(self, stock_resolver):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT p.*,COUNT(l.request_id) request_count,COUNT(DISTINCT r.customer_id) customer_count,"
                "SUM(r.quantity) demand_quantity,MIN(r.requested_at) earliest_request,MAX(r.requested_at) latest_request "
                "FROM purchase_plan_items p JOIN purchase_plan_requests l ON l.plan_item_id=p.id "
                "JOIN purchase_requests r ON r.id=l.request_id WHERE p.status!='removed' AND r.archived=0 "
                "GROUP BY p.id ORDER BY earliest_request,p.id"
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                stock = int(stock_resolver(item["product_id"]) or 0) if item["product_id"] else 0
                item["stock"] = stock
                item["recommended_quantity"] = max(0, int(item["demand_quantity"] or 0) - stock)
                item["purchase_quantity"] = item["actual_quantity"] if item["actual_quantity"] is not None else item["recommended_quantity"]
                waiting = connection.execute(
                    "SELECT r.* FROM purchase_requests r JOIN purchase_plan_requests l ON l.request_id=r.id WHERE l.plan_item_id=? ORDER BY r.requested_at,r.id",
                    (item["id"],),
                ).fetchall()
                item["requests"] = [dict(request_row) for request_row in waiting]
                result.append(item)
        return result

    def create_supplier_order(self, payload, actor_id):
        supplier = text(payload.get("supplier_name"), 240)
        if not supplier:
            raise PurchaseValidationError("Укажите поставщика.", "supplier_name")
        plan_ids = payload.get("plan_item_ids") or []
        try:
            plan_ids = sorted({int(value) for value in plan_ids if int(value) > 0})
        except (TypeError, ValueError):
            raise PurchaseValidationError("Выберите позиции плана.", "plan_item_ids")
        if not plan_ids:
            raise PurchaseValidationError("Выберите позиции плана.", "plan_item_ids")
        now = utc_now()
        internal_number = text(payload.get("internal_number"), 80) or "PO-{}".format(datetime.now().strftime("%Y%m%d-%H%M%S"))
        currency = text(payload.get("currency") or "RUB", 8).upper()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO supplier_orders(internal_number,supplier_name,created_date,ordered_date,expected_date,currency,comment,status,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,'draft',?,?,?,?)",
                (internal_number, supplier, parse_date(payload.get("created_date") or now, "created_date"),
                 parse_date(payload.get("ordered_date"), "ordered_date"), parse_date(payload.get("expected_date"), "expected_date"),
                 currency, text(payload.get("comment"), 3000), now, now, int(actor_id), int(actor_id)),
            )
            order_id = int(cursor.lastrowid)
            for plan_id in plan_ids:
                plan = connection.execute("SELECT * FROM purchase_plan_items WHERE id=? AND status='active'", (plan_id,)).fetchone()
                if not plan:
                    raise PurchaseValidationError("Позиция плана недоступна.", "plan_item_ids")
                demand = connection.execute(
                    "SELECT COALESCE(SUM(r.quantity),0) FROM purchase_requests r JOIN purchase_plan_requests l ON l.request_id=r.id WHERE l.plan_item_id=? AND r.archived=0",
                    (plan_id,),
                ).fetchone()[0]
                quantity = plan["actual_quantity"] if plan["actual_quantity"] is not None else max(1, int(demand or 0))
                if int(quantity) < 1:
                    raise PurchaseValidationError("Количество к закупке должно быть больше нуля.", "actual_quantity")
                price = money((payload.get("prices") or {}).get(str(plan_id)), "purchase_price") or 0
                item_cursor = connection.execute(
                    "INSERT INTO supplier_order_items(order_id,plan_item_id,quantity,purchase_price,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (order_id, plan_id, quantity, price, now, now),
                )
                order_item_id = int(item_cursor.lastrowid)
                requests = connection.execute("SELECT request_id FROM purchase_plan_requests WHERE plan_item_id=?", (plan_id,)).fetchall()
                for request_row in requests:
                    request_id = int(request_row[0])
                    connection.execute("INSERT INTO supplier_order_requests(order_item_id,request_id,created_at) VALUES(?,?,?)", (order_item_id, request_id, now))
                    self._history(connection, request_id, "supplier_order_created", actor_id, details={"order_id": order_id}, now=now)
                connection.execute("UPDATE purchase_plan_items SET status='ordered',updated_at=?,updated_by=? WHERE id=?", (now, int(actor_id), plan_id))
            connection.commit()
        return self.get_supplier_order(order_id)

    def get_supplier_order(self, order_id):
        with self.connect() as connection:
            order = connection.execute("SELECT * FROM supplier_orders WHERE id=?", (int(order_id),)).fetchone()
            if not order:
                raise PurchaseNotFoundError("Заказ поставщику не найден.")
            items = connection.execute(
                "SELECT i.*,p.product_name,p.brand,p.model,p.article FROM supplier_order_items i JOIN purchase_plan_items p ON p.id=i.plan_item_id WHERE i.order_id=? ORDER BY i.id",
                (int(order_id),),
            ).fetchall()
        result = dict(order)
        result["items"] = [dict(item) for item in items]
        result["total"] = sum(item["quantity"] * item["purchase_price"] for item in result["items"])
        return result

    def list_supplier_orders(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT o.*,COUNT(i.id) item_count,COALESCE(SUM(i.quantity*i.purchase_price),0) total FROM supplier_orders o LEFT JOIN supplier_order_items i ON i.order_id=o.id GROUP BY o.id ORDER BY o.created_at DESC,o.id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_order_status(self, order_id, status, actor_id):
        status = folded(status)
        if status not in ORDER_STATUSES:
            raise PurchaseValidationError("Неизвестный статус заказа.", "status")
        order = self.get_supplier_order(order_id)
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE supplier_orders SET status=?,ordered_date=CASE WHEN ?='ordered' THEN COALESCE(ordered_date,substr(?,1,10)) ELSE ordered_date END,updated_at=?,updated_by=? WHERE id=?", (status, status, now, now, int(actor_id), int(order_id)))
            if status == "ordered":
                request_rows = connection.execute("SELECT DISTINCT r.request_id FROM supplier_order_items i JOIN supplier_order_requests r ON r.order_item_id=i.id WHERE i.order_id=?", (int(order_id),)).fetchall()
                for row in request_rows:
                    current = connection.execute("SELECT status FROM purchase_requests WHERE id=?", (row[0],)).fetchone()[0]
                    connection.execute("UPDATE purchase_requests SET status='ordered',updated_at=?,updated_by=? WHERE id=?", (now, int(actor_id), row[0]))
                    self._history(connection, row[0], "supplier_order_ordered", actor_id, current, "ordered", now=now)
            connection.commit()
        return self.get_supplier_order(order_id)

    def receive_item(self, item_id, received_quantity, actor_id):
        quantity = positive_int(received_quantity, "received_quantity")
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            item = connection.execute("SELECT * FROM supplier_order_items WHERE id=?", (int(item_id),)).fetchone()
            if not item:
                raise PurchaseNotFoundError("Позиция заказа не найдена.")
            if quantity > item["quantity"]:
                raise PurchaseValidationError("Получено больше заказанного.", "received_quantity")
            connection.execute("UPDATE supplier_order_items SET received_quantity=?,updated_at=? WHERE id=?", (quantity, now, int(item_id)))
            related = connection.execute("SELECT request_id FROM supplier_order_requests WHERE order_item_id=? ORDER BY request_id", (int(item_id),)).fetchall()
            remaining = quantity
            for row in related:
                request_row = connection.execute("SELECT status,quantity FROM purchase_requests WHERE id=?", (row[0],)).fetchone()
                if remaining >= int(request_row["quantity"]):
                    connection.execute("UPDATE purchase_requests SET status='arrived',updated_at=?,updated_by=? WHERE id=?", (now, int(actor_id), row[0]))
                    self._history(connection, row[0], "received", actor_id, request_row["status"], "arrived", now=now)
                    remaining -= int(request_row["quantity"])
            totals = connection.execute("SELECT SUM(quantity),SUM(received_quantity) FROM supplier_order_items WHERE order_id=?", (item["order_id"],)).fetchone()
            status = "received" if totals[0] == totals[1] else "partially_received" if totals[1] else "ordered"
            connection.execute("UPDATE supplier_orders SET status=?,updated_at=?,updated_by=? WHERE id=?", (status, now, int(actor_id), item["order_id"]))
            connection.commit()
        return self.get_supplier_order(item["order_id"])

    def list_requests(self, filters=None, customer_ids=None):
        filters = filters or {}
        clauses, params = [], []
        query = folded(filters.get("q"))
        if query:
            pattern = "%{}%".format(query)
            clauses.append("(lower(product_name) LIKE ? OR lower(brand) LIKE ? OR lower(model) LIKE ? OR lower(article) LIKE ? OR lower(description) LIKE ? OR lower(customer_comment) LIKE ? OR lower(internal_note) LIKE ?{} )".format(
                " OR customer_id IN ({})".format(",".join("?" for _ in customer_ids)) if customer_ids else ""))
            params.extend([pattern] * 7 + list(customer_ids or []))
        for field in ("status", "channel"):
            value = folded(filters.get(field))
            if value:
                clauses.append("{}=?".format(field)); params.append(value)
        if filters.get("customer_id") not in (None, ""):
            clauses.append("customer_id=?")
            params.append(positive_int(filters.get("customer_id"), "customer_id"))
        brand = folded(filters.get("brand"))
        if brand:
            clauses.append("lower(brand)=?"); params.append(brand)
        if filters.get("date_from"):
            clauses.append("requested_at>=?"); params.append(parse_date(filters["date_from"], "date_from"))
        if filters.get("date_to"):
            clauses.append("requested_at<?"); params.append(parse_date(filters["date_to"], "date_to") + "T23:59:59")
        if str(filters.get("active_only", "")).lower() in ("1", "true", "on"):
            clauses.append("archived=0 AND status NOT IN ('sold','closed') AND (valid_until IS NULL OR valid_until>=date('now'))")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            per_page = int(filters.get("per_page", 20)); page = max(1, int(filters.get("page", 1)))
        except (TypeError, ValueError):
            per_page, page = 20, 1
        if per_page not in PAGE_SIZES:
            per_page = 20
        sort = filters.get("sort")
        order = {"oldest": "requested_at ASC,id ASC", "valid_until": "COALESCE(valid_until,'9999') ASC,id DESC", "quantity": "quantity DESC,id DESC"}.get(sort, "requested_at DESC,id DESC")
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM purchase_requests" + where, params).fetchone()[0])
            pages = max(1, int(math.ceil(float(total) / per_page))); page = min(page, pages)
            rows = connection.execute("SELECT * FROM purchase_requests" + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", params + [per_page, (page - 1) * per_page]).fetchall()
        return {"rows": [dict(row) for row in rows], "total": total, "page": page, "per_page": per_page, "pages": pages}

    def summary(self):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT SUM(status='new' AND archived=0),COUNT(DISTINCT CASE WHEN status NOT IN ('notified','sold','closed') AND archived=0 THEN customer_id END),SUM(status='arrived' AND archived=0) FROM purchase_requests"
            ).fetchone()
            plan = connection.execute("SELECT COUNT(*),COALESCE(SUM(COALESCE(actual_quantity,0)),0) FROM purchase_plan_items WHERE status='active'").fetchone()
            ordered = connection.execute("SELECT COALESCE(SUM(i.quantity-i.received_quantity),0) FROM supplier_order_items i JOIN supplier_orders o ON o.id=i.order_id WHERE o.status IN ('ordered','partially_received')").fetchone()[0]
        return {"new_requests": int(row[0] or 0), "waiting_customers": int(row[1] or 0),
                "plan_positions": int(plan[0] or 0), "recommended_units": int(plan[1] or 0),
                "ordered_units": int(ordered or 0), "arrived_unnotified": int(row[2] or 0)}
