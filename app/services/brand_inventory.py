"""Transactional brand inventory documents built on the canonical stock ledger."""

import re
import uuid
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    _empty_enrichment,
    _json,
    article_quality,
    ensure_unique_article,
    normalize_text,
)


FINAL_STATUSES = {"confirmed", "adjusted", "added", "missing"}


class InventoryError(ValueError):
    status_code = 400


class InventoryConflict(InventoryError):
    status_code = 409


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def nonnegative_integer(value, label="Фактическое количество"):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value).strip()):
        raise InventoryError("{} должно быть целым неотрицательным числом.".format(label))
    return int(value)


class BrandInventory:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    def initialize(self):
        self.database.initialize()

    @staticmethod
    def _brand(connection, brand_id):
        try:
            brand_id = int(brand_id)
        except (TypeError, ValueError):
            raise InventoryError("Бренд не найден.")
        row = connection.execute(
            "SELECT id, name, active FROM erp_brands WHERE id = ?", (brand_id,)
        ).fetchone()
        if row is None or not row["active"]:
            raise InventoryError("Бренд не найден.")
        return row

    @staticmethod
    def _latest_movement_rowid(connection, product_id):
        return int(connection.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM catalog_stock_movements WHERE product_id = ?",
            (int(product_id),),
        ).fetchone()[0])

    @staticmethod
    def _session(connection, session_id, active=False):
        row = connection.execute(
            "SELECT s.*, b.name AS brand_name FROM erp_inventory_sessions s "
            "JOIN erp_brands b ON b.id = s.brand_id WHERE s.id = ?",
            (str(session_id),),
        ).fetchone()
        if row is None:
            raise InventoryError("Инвентаризация не найдена.")
        if active and row["status"] != "active":
            raise InventoryConflict("Инвентаризация уже завершена или отменена.")
        return row

    def start(self, brand_id, user_name=""):
        self.initialize()
        now = utc_now()
        with self.database.transaction() as connection:
            brand = self._brand(connection, brand_id)
            existing = connection.execute(
                "SELECT id FROM erp_inventory_sessions WHERE brand_id = ? "
                "AND status = 'active' LIMIT 1", (brand["id"],)
            ).fetchone()
            if existing:
                return self._detail(connection, existing["id"]), False
            fractional = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products WHERE active = 1 "
                "AND brand_id = ? AND ABS(stock - CAST(stock AS INTEGER)) > 0.000001",
                (brand["id"],),
            ).fetchone()[0]
            if fractional:
                raise InventoryError(
                    "У бренда есть дробные остатки; сначала исправьте данные склада."
                )
            session_id = uuid.uuid4().hex
            products = connection.execute(
                "SELECT p.id, CAST(p.stock AS INTEGER) AS stock, "
                "COALESCE((SELECT MAX(m.rowid) FROM catalog_stock_movements m "
                "WHERE m.product_id = p.id), 0) AS movement_rowid "
                "FROM catalog_excel_products p WHERE p.active = 1 "
                "AND p.brand_id = ? AND p.stock > 0 ORDER BY p.id",
                (brand["id"],),
            ).fetchall()
            connection.execute(
                "INSERT INTO erp_inventory_sessions (id, brand_id, status, started_by, "
                "started_at, start_positions, updated_at) VALUES (?, ?, 'active', ?, ?, ?, ?)",
                (session_id, brand["id"], user_name or None, now, len(products), now),
            )
            for product in products:
                connection.execute(
                    "INSERT INTO erp_inventory_items (id, session_id, product_id, "
                    "snapshot_stock, status, appearance, snapshot_at, snapshot_movement_rowid) "
                    "VALUES (?, ?, ?, ?, 'pending', 'snapshot', ?, ?)",
                    (uuid.uuid4().hex, session_id, product["id"], int(product["stock"]),
                     now, int(product["movement_rowid"])),
                )
            return self._detail(connection, session_id), True

    def get(self, session_id):
        self.initialize()
        with self.database.connect() as connection:
            return self._detail(connection, session_id)

    def list_items(self, session_id, query="", category_id=None, limit=250, offset=0):
        self.initialize()
        with self.database.connect() as connection:
            self._session(connection, session_id)
            where = ["i.session_id = ?", "i.status IN ('pending', 'conflict', 'error')"]
            params = [str(session_id)]
            query = str(query or "").strip()
            if query:
                where.append("(p.excel_name_raw LIKE ? OR COALESCE(p.excel_article, '') LIKE ?)")
                term = "%{}%".format(query)
                params.extend((term, term))
            if category_id not in (None, ""):
                try:
                    category_id = int(category_id)
                except (TypeError, ValueError):
                    raise InventoryError("Категория не найдена.")
                where.append("p.category_id = ?")
                params.append(category_id)
            limit = max(1, min(int(limit or 250), 500))
            offset = max(0, int(offset or 0))
            rows = connection.execute(
                "SELECT i.*, p.excel_name_raw AS name, p.excel_article AS article, "
                "p.stock AS current_stock, p.bitrix_thumbnail_url AS photo_url, "
                "p.category_id, c.name AS category_name FROM erp_inventory_items i "
                "JOIN catalog_excel_products p ON p.id = i.product_id "
                "LEFT JOIN erp_categories c ON c.id = p.category_id WHERE {} "
                "ORDER BY p.excel_name_raw COLLATE NOCASE, p.id LIMIT ? OFFSET ?".format(
                    " AND ".join(where)
                ), params + [limit, offset],
            ).fetchall()
            return [self._item_dict(row) for row in rows]

    def refresh_conflict(self, session_id, item_id):
        self.initialize()
        with self.database.transaction() as connection:
            self._session(connection, session_id, active=True)
            item, product = self._item_product(connection, session_id, item_id)
            if item["status"] != "conflict":
                raise InventoryConflict("Позиция не требует перепроверки.")
            now = utc_now()
            connection.execute(
                "UPDATE erp_inventory_items SET snapshot_stock = ?, actual_stock = NULL, "
                "final_stock = NULL, quantity_delta = NULL, status = 'pending', "
                "snapshot_at = ?, snapshot_movement_rowid = ?, error_message = NULL "
                "WHERE id = ?",
                (int(product["stock"]), now,
                 self._latest_movement_rowid(connection, product["id"]), item["id"]),
            )
            return {"item_id": item["id"], "system_stock": int(product["stock"])}

    def confirm(self, session_id, item_id, actual_stock, user_name="",
                idempotency_key="", confirm_zero=False, failure_hook=None):
        actual_stock = nonnegative_integer(actual_stock)
        if actual_stock == 0 and not confirm_zero:
            raise InventoryError("Подтвердите, что фактический остаток равен 0.")
        key = str(idempotency_key or "").strip()
        if not key:
            raise InventoryError("Не указан ключ операции.")
        self.initialize()
        with self.database.transaction() as connection:
            self._session(connection, session_id, active=True)
            repeated = connection.execute(
                "SELECT id, status, actual_stock, final_stock, quantity_delta, movement_id "
                "FROM erp_inventory_items WHERE session_id = ? AND idempotency_key = ?",
                (str(session_id), key),
            ).fetchone()
            if repeated:
                return self._confirmation_result(repeated, repeated=True)
            item, product = self._item_product(connection, session_id, item_id)
            if item["status"] in FINAL_STATUSES:
                return self._confirmation_result(item, repeated=True)
            if item["status"] == "conflict":
                raise InventoryConflict("Товар изменился после начала инвентаризации — перепроверьте")
            latest = self._latest_movement_rowid(connection, product["id"])
            current = int(product["stock"])
            if latest > int(item["snapshot_movement_rowid"]) or current != int(item["snapshot_stock"]):
                connection.execute(
                    "UPDATE erp_inventory_items SET status = 'conflict', error_message = ? WHERE id = ?",
                    ("Остаток изменился с {} на {}".format(item["snapshot_stock"], current), item["id"]),
                )
                return {
                    "ok": False, "conflict": True, "item_id": item["id"],
                    "snapshot_stock": int(item["snapshot_stock"]), "current_stock": current,
                    "message": "Товар изменился после начала инвентаризации — перепроверьте",
                }
            return self._apply_confirmation(
                connection, item, product, actual_stock, user_name, key,
                "confirmed" if actual_stock == current else "adjusted",
                failure_hook=failure_hook,
            )

    def search_products(self, session_id, query):
        query = str(query or "").strip()
        if len(query) < 2:
            return []
        self.initialize()
        with self.database.connect() as connection:
            session = self._session(connection, session_id, active=True)
            term = "%{}%".format(query)
            rows = connection.execute(
                "SELECT p.id, p.excel_name_raw AS name, p.excel_article AS article, "
                "p.stock, p.active, p.brand_id, b.name AS brand_name "
                "FROM catalog_excel_products p LEFT JOIN erp_brands b ON b.id = p.brand_id "
                "WHERE (p.excel_name_raw LIKE ? OR COALESCE(p.excel_article, '') LIKE ? "
                "OR p.normalized_name LIKE ?) ORDER BY (p.brand_id = ?) DESC, p.active DESC, "
                "p.excel_name_raw COLLATE NOCASE LIMIT 30",
                (term, term, "%{}%".format(normalize_text(query)), session["brand_id"]),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_existing(self, session_id, product_id, actual_stock, user_name="",
                     idempotency_key="", confirm_zero=False, failure_hook=None):
        actual_stock = nonnegative_integer(actual_stock)
        if actual_stock == 0 and not confirm_zero:
            raise InventoryError("Подтвердите, что фактический остаток равен 0.")
        key = str(idempotency_key or "").strip()
        if not key:
            raise InventoryError("Не указан ключ операции.")
        self.initialize()
        with self.database.transaction() as connection:
            session = self._session(connection, session_id, active=True)
            product = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ?", (int(product_id),)
            ).fetchone()
            if product is None:
                raise InventoryError("Товар не найден.")
            if int(product["brand_id"] or 0) != int(session["brand_id"]):
                raise InventoryError("Товар относится к другому бренду.")
            existing = connection.execute(
                "SELECT * FROM erp_inventory_items WHERE session_id = ? AND product_id = ?",
                (session["id"], product["id"]),
            ).fetchone()
            if existing:
                if existing["status"] in FINAL_STATUSES:
                    return self._confirmation_result(existing, repeated=True)
                raise InventoryConflict(
                    "Товар уже находится в очереди — подтвердите его в основной таблице."
                )
            now = utc_now()
            item_id = uuid.uuid4().hex
            connection.execute(
                "UPDATE catalog_excel_products SET active = 1, "
                "source_key = COALESCE(deleted_source_key, source_key), "
                "deleted_at = NULL, deleted_by = NULL, "
                "deleted_stock = NULL, delete_mode = NULL, deleted_source_key = NULL, updated_at = ? "
                "WHERE id = ?", (now, product["id"]),
            )
            connection.execute(
                "INSERT INTO erp_inventory_items (id, session_id, product_id, snapshot_stock, "
                "status, appearance, snapshot_at, snapshot_movement_rowid) "
                "VALUES (?, ?, ?, ?, 'pending', 'existing', ?, ?)",
                (item_id, session["id"], product["id"], int(product["stock"]), now,
                 self._latest_movement_rowid(connection, product["id"])),
            )
            item = connection.execute(
                "SELECT * FROM erp_inventory_items WHERE id = ?", (item_id,)
            ).fetchone()
            return self._apply_confirmation(
                connection, item, product, actual_stock, user_name, key, "added",
                failure_hook=failure_hook,
            )

    def add_new(self, session_id, name, article, actual_stock, user_name="",
                idempotency_key="", category_id=None, failure_hook=None):
        name = " ".join(str(name or "").split())
        article = " ".join(str(article or "").split())
        if not name:
            raise InventoryError("Название товара обязательно.")
        actual_stock = nonnegative_integer(actual_stock)
        if actual_stock <= 0:
            raise InventoryError("Для найденного товара количество должно быть больше 0.")
        key = str(idempotency_key or "").strip()
        if not key:
            raise InventoryError("Не указан ключ операции.")
        self.initialize()
        with self.database.transaction() as connection:
            session = self._session(connection, session_id, active=True)
            repeated = connection.execute(
                "SELECT * FROM erp_inventory_items WHERE session_id = ? AND idempotency_key = ?",
                (session["id"], key),
            ).fetchone()
            if repeated:
                return self._confirmation_result(repeated, repeated=True)
            duplicate = connection.execute(
                "SELECT id, excel_name_raw FROM catalog_excel_products WHERE brand_id = ? "
                "AND (normalized_name = ? OR (? <> '' AND lower(COALESCE(excel_article,'')) = lower(?))) "
                "ORDER BY active DESC, id LIMIT 1",
                (session["brand_id"], normalize_text(name), article, article),
            ).fetchone()
            if duplicate:
                raise InventoryConflict(
                    "Похожий товар уже существует: {} (ID {}).".format(
                        duplicate["excel_name_raw"], duplicate["id"]
                    )
                )
            ensure_unique_article(connection, article)
            batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE status = 'active' "
                "ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
            if batch is None:
                raise InventoryError("Сначала оформите приход из Excel.")
            brand = connection.execute(
                "SELECT id, name FROM erp_brands WHERE id = ?", (session["brand_id"],)
            ).fetchone()
            category = None
            if category_id not in (None, ""):
                category = connection.execute(
                    "SELECT id, name FROM erp_categories WHERE id = ? AND brand_id = ?",
                    (int(category_id), brand["id"]),
                ).fetchone()
                if category is None:
                    raise InventoryError("Категория не найдена.")
            now = utc_now()
            excel_row = connection.execute(
                "SELECT COALESCE(MAX(excel_row), 1) + 1 FROM catalog_excel_products"
            ).fetchone()[0]
            enrichment = _empty_enrichment()
            columns = (
                "source_key", "created_batch_id", "current_batch_id", "active", "raw_excel_json",
                "excel_row", "excel_name_raw", "normalized_name", "excel_article", "article_quality",
                "excel_brand", "excel_category", "brand_id", "category_id", "stock", "cell",
                "stock_source", "file_sha256", "match_status", "match_method", "match_confidence",
                "match_decision", "candidates_json", "bitrix_link_cardinality", "shared_bitrix_row_count",
            ) + tuple(enrichment) + ("moysklad_sync_status", "created_at", "updated_at")
            values = (
                "inventory:{}".format(uuid.uuid4().hex), batch["id"], batch["id"], 1,
                _json({"source": "inventory", "name": name, "article": article}), excel_row,
                name, normalize_text(name), article or None, article_quality(article), brand["name"],
                category["name"] if category else None, brand["id"],
                category["id"] if category else None, 0, None, "inventory", batch["file_sha256"],
                "not_found", "inventory_create", 0.0, "unmatched", "[]", "unlinked", 0,
            ) + tuple(enrichment.values()) + ("not_linked", now, now)
            connection.execute(
                "INSERT INTO catalog_excel_products ({}) VALUES ({})".format(
                    ", ".join(columns), ", ".join("?" for _ in columns)
                ), values,
            )
            product_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            item_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO erp_inventory_items (id, session_id, product_id, snapshot_stock, "
                "status, appearance, snapshot_at, snapshot_movement_rowid) "
                "VALUES (?, ?, ?, 0, 'pending', 'new', ?, 0)",
                (item_id, session["id"], product_id, now),
            )
            item = connection.execute(
                "SELECT * FROM erp_inventory_items WHERE id = ?", (item_id,)
            ).fetchone()
            product = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ?", (product_id,)
            ).fetchone()
            return self._apply_confirmation(
                connection, item, product, actual_stock, user_name, key, "added",
                failure_hook=failure_hook,
            )

    def completion_preview(self, session_id):
        self.initialize()
        with self.database.connect() as connection:
            session = self._session(connection, session_id, active=True)
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count, COALESCE(SUM(snapshot_stock), 0) AS stock "
                "FROM erp_inventory_items WHERE session_id = ? GROUP BY status",
                (session["id"],),
            ).fetchall()
            by_status = {row["status"]: dict(row) for row in rows}
            return {
                "checked": sum(int(by_status.get(s, {}).get("count", 0)) for s in FINAL_STATUSES),
                "adjusted": int(by_status.get("adjusted", {}).get("count", 0)),
                "added": int(by_status.get("added", {}).get("count", 0)),
                "pending": int(by_status.get("pending", {}).get("count", 0)),
                "conflicts": int(by_status.get("conflict", {}).get("count", 0)),
                "units_to_write_off": int(by_status.get("pending", {}).get("stock", 0)),
            }

    def complete(self, session_id, user_name="", confirmation=False, failure_hook=None):
        if not confirmation:
            raise InventoryError("Подтвердите завершение инвентаризации.")
        self.initialize()
        with self.database.transaction() as connection:
            session = self._session(connection, session_id, active=True)
            pending = connection.execute(
                "SELECT i.*, p.stock, p.brand_id AS current_brand_id, "
                "p.id AS inventory_product_id, "
                "COALESCE((SELECT MAX(m.rowid) FROM catalog_stock_movements m "
                "WHERE m.product_id = p.id), 0) AS current_movement_rowid "
                "FROM erp_inventory_items i JOIN catalog_excel_products p "
                "ON p.id = i.product_id WHERE i.session_id = ? AND i.status IN "
                "('pending', 'conflict', 'error') ORDER BY i.id", (session["id"],)
            ).fetchall()
            conflicts = [row for row in pending if row["status"] == "conflict"]
            for row in pending:
                if row["status"] != "pending":
                    continue
                latest = int(row["current_movement_rowid"])
                if (
                    int(row["current_brand_id"] or 0) != int(session["brand_id"])
                    or latest > int(row["snapshot_movement_rowid"])
                    or int(row["stock"]) != int(row["snapshot_stock"])
                ):
                    conflicts.append(row)
            if conflicts:
                for row in conflicts:
                    connection.execute(
                        "UPDATE erp_inventory_items SET status = 'conflict', error_message = ? WHERE id = ?",
                        ("Товар изменился после снимка", row["id"]),
                    )
                return {"ok": False, "conflict": True, "count": len(conflicts),
                        "message": "Конфликтные позиции необходимо перепроверить."}
            for row in pending:
                if row["status"] != "pending":
                    raise InventoryConflict("Исправьте ошибки позиций перед завершением.")
                product = {"id": row["inventory_product_id"], "stock": row["stock"]}
                key = "inventory:{}:missing:{}".format(session["id"], row["id"])
                self._apply_confirmation(
                    connection, row, product, 0, user_name, key, "missing",
                    refresh_totals=False,
                )
            if failure_hook:
                failure_hook(connection)
            now = utc_now()
            self._refresh_totals(connection, session["id"])
            connection.execute(
                "UPDATE erp_inventory_sessions SET status = 'completed', completed_by = ?, "
                "completed_at = ?, updated_at = ? WHERE id = ? AND status = 'active'",
                (user_name or None, now, now, session["id"]),
            )
            return {"ok": True, "session": self._detail(connection, session["id"])}

    def cancel(self, session_id, reason, user_name=""):
        reason = " ".join(str(reason or "").split())
        if not reason:
            raise InventoryError("Укажите причину отмены.")
        self.initialize()
        with self.database.transaction() as connection:
            session = self._session(connection, session_id, active=True)
            now = utc_now()
            connection.execute(
                "UPDATE erp_inventory_sessions SET status = 'cancelled', cancelled_by = ?, "
                "cancelled_at = ?, cancelled_reason = ?, updated_at = ? WHERE id = ?",
                (user_name or None, now, reason, now, session["id"]),
            )
            return self._detail(connection, session["id"])

    def _apply_confirmation(self, connection, item, product, actual, user_name,
                            key, status, failure_hook=None, refresh_totals=True):
        current = int(product["stock"])
        delta = actual - current
        movement_id = None
        now = utc_now()
        if delta:
            movement_id = str(uuid.uuid4())
            cursor = connection.execute(
                "UPDATE catalog_excel_products SET stock = ?, stock_source = 'inventory', "
                "updated_at = ? WHERE id = ? AND stock = ?",
                (actual, now, product["id"], product["stock"]),
            )
            if cursor.rowcount != 1:
                raise InventoryConflict("Остаток изменился во время проведения — перепроверьте")
            connection.execute(
                "INSERT INTO catalog_stock_movements (id, product_id, movement_type, "
                "quantity_delta, stock_before, stock_after, idempotency_key, tenant_id, "
                "source_type, source_id, source_line_id, operation_kind, source, user_name, "
                "comment, created_at) VALUES (?, ?, 'inventory_adjustment', ?, ?, ?, ?, "
                "'default', 'inventory', ?, ?, 'adjust', 'Vechasu ERP', ?, 'Инвентаризация', ?)",
                (movement_id, product["id"], delta, current, actual, key, item["session_id"],
                 item["id"], user_name or None, now),
            )
        if failure_hook:
            failure_hook(connection)
        connection.execute(
            "UPDATE erp_inventory_items SET actual_stock = ?, final_stock = ?, quantity_delta = ?, "
            "status = ?, confirmed_by = ?, confirmed_at = ?, movement_id = ?, "
            "idempotency_key = ?, error_message = NULL WHERE id = ?",
            (actual, actual, delta, status, user_name or None, now, movement_id, key, item["id"]),
        )
        if refresh_totals:
            self._refresh_totals(connection, item["session_id"])
        stored = connection.execute(
            "SELECT * FROM erp_inventory_items WHERE id = ?", (item["id"],)
        ).fetchone()
        return self._confirmation_result(stored)

    @staticmethod
    def _refresh_totals(connection, session_id):
        totals = connection.execute(
            "SELECT SUM(CASE WHEN status IN ('confirmed','adjusted','added','missing') THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'adjusted' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'added' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'missing' THEN 1 ELSE 0 END), "
            "COALESCE(SUM(CASE WHEN status IN ('adjusted','added','missing') THEN quantity_delta ELSE 0 END),0) "
            "FROM erp_inventory_items WHERE session_id = ?", (session_id,)
        ).fetchone()
        connection.execute(
            "UPDATE erp_inventory_sessions SET checked_positions = ?, adjusted_positions = ?, "
            "added_positions = ?, missing_positions = ?, total_delta = ?, updated_at = ? WHERE id = ?",
            tuple(int(value or 0) for value in totals) + (utc_now(), session_id),
        )

    @staticmethod
    def _item_product(connection, session_id, item_id):
        item = connection.execute(
            "SELECT * FROM erp_inventory_items WHERE id = ? AND session_id = ?",
            (str(item_id), str(session_id)),
        ).fetchone()
        if item is None:
            raise InventoryError("Позиция инвентаризации не найдена.")
        product = connection.execute(
            "SELECT * FROM catalog_excel_products WHERE id = ?", (item["product_id"],)
        ).fetchone()
        if product is None:
            raise InventoryError("Товар не найден.")
        session = connection.execute(
            "SELECT brand_id FROM erp_inventory_sessions WHERE id = ?", (str(session_id),)
        ).fetchone()
        if session is None or int(product["brand_id"] or 0) != int(session["brand_id"]):
            raise InventoryConflict("Бренд товара изменился после начала инвентаризации.")
        return item, product

    def _detail(self, connection, session_id):
        row = self._session(connection, session_id)
        data = dict(row)
        data["remaining"] = int(connection.execute(
            "SELECT COUNT(*) FROM erp_inventory_items WHERE session_id = ? "
            "AND status IN ('pending','conflict','error')", (row["id"],)
        ).fetchone()[0])
        data["categories"] = [dict(item) for item in connection.execute(
            "SELECT DISTINCT c.id, c.name FROM erp_inventory_items i "
            "JOIN catalog_excel_products p ON p.id = i.product_id "
            "JOIN erp_categories c ON c.id = p.category_id WHERE i.session_id = ? "
            "ORDER BY c.name COLLATE NOCASE", (row["id"],)
        ).fetchall()]
        return data

    @staticmethod
    def _item_dict(row):
        data = dict(row)
        for key in ("snapshot_stock", "actual_stock", "final_stock", "quantity_delta", "current_stock"):
            if data.get(key) is not None:
                data[key] = int(data[key])
        return data

    @staticmethod
    def _confirmation_result(item, repeated=False):
        before = int(item["final_stock"] or 0) - int(item["quantity_delta"] or 0)
        after = int(item["final_stock"] or 0)
        changed = before != after
        return {
            "ok": True, "repeated": repeated, "item_id": item["id"],
            "status": item["status"], "stock_before": before, "stock_after": after,
            "delta": int(item["quantity_delta"] or 0), "movement_id": item["movement_id"],
            "message": (
                "Остаток изменён с {} на {}".format(before, after)
                if changed else "Проведено успешно"
            ),
        }
