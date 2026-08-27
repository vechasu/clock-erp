"""Transactional brand inventory documents built on the canonical stock ledger."""

import re
import uuid
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.excel_product_catalog import (
    _empty_enrichment,
    _json,
    article_quality,
    require_unique_article,
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
    def _category(connection, brand_id, category_id):
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            raise InventoryError("Категория не найдена.")
        if category_id == 0:
            exists = connection.execute(
                "SELECT 1 FROM catalog_excel_products "
                "WHERE active = 1 AND brand_id = ? AND category_id IS NULL LIMIT 1",
                (int(brand_id),),
            ).fetchone()
            if exists is None:
                raise InventoryError("У выбранного бренда нет товаров без категории.")
            return {"id": None, "requested_id": 0, "name": "Без категории", "normalized_name": ""}
        row = connection.execute(
            "SELECT id, name FROM erp_categories "
            "WHERE id = ? AND active = 1", (category_id,)
        ).fetchone()
        if row is None:
            raise InventoryError("Категория не найдена.")
        exists = connection.execute(
            "SELECT 1 FROM catalog_excel_products p WHERE p.active = 1 "
            "AND p.brand_id = ? AND p.category_id = ? LIMIT 1",
            (int(brand_id), category_id),
        ).fetchone()
        if exists is None:
            raise InventoryError("Категория не содержит товаров выбранного бренда.")
        return dict(row, requested_id=category_id)

    @staticmethod
    def _model(connection, brand_id, category, model_id):
        try:
            model_id = int(model_id)
        except (TypeError, ValueError):
            raise InventoryError("Модель не найдена.")
        row = connection.execute(
            "SELECT id, name, normalized_name FROM erp_models "
            "WHERE id = ? AND brand_id = ? AND active = 1",
            (model_id, int(brand_id)),
        ).fetchone()
        if row is None:
            raise InventoryError("Модель не найдена.")
        category_sql, category_parameters = BrandInventory._category_predicate(category)
        exists = connection.execute(
            "SELECT 1 FROM catalog_excel_products p WHERE p.active = 1 "
            "AND p.brand_id = ? AND " + category_sql + " AND "
            "p.model_id = ? "
            "LIMIT 1",
            [int(brand_id)] + category_parameters + [model_id],
        ).fetchone()
        if exists is None:
            raise InventoryError(
                "Модель не содержит товаров выбранных бренда и категории."
            )
        return row

    @staticmethod
    def _category_predicate(category):
        if category["requested_id"] == 0:
            return "p.category_id IS NULL", []
        return "p.category_id = ?", [int(category["id"])]

    @staticmethod
    def _scope_label(brand_name, category_name=None, model_name=None):
        if model_name:
            return "{} → {} → {}".format(brand_name, category_name, model_name)
        if category_name:
            return "{} → {}".format(brand_name, category_name)
        return "{} · весь бренд".format(brand_name)

    def _scope(self, connection, brand_id, category_id=None, model_id=None):
        brand = self._brand(connection, brand_id)
        if model_id not in (None, "") and category_id in (None, ""):
            raise InventoryError("Для выбора модели сначала выберите категорию.")
        category = (
            self._category(connection, brand["id"], category_id)
            if category_id not in (None, "") else None
        )
        model = (
            self._model(connection, brand["id"], category, model_id)
            if model_id not in (None, "") else None
        )
        scope_type = "model" if model else "category" if category else "brand"
        return {
            "type": scope_type,
            "brand": brand,
            "category": category,
            "model": model,
            "label": self._scope_label(
                brand["name"],
                category["name"] if category else None,
                model["name"] if model else None,
            ),
        }

    @staticmethod
    def _snapshot_products(connection, scope):
        where = ["p.active = 1", "p.brand_id = ?", "p.stock > 0"]
        parameters = [int(scope["brand"]["id"])]
        if scope["category"]:
            category_sql, category_parameters = BrandInventory._category_predicate(
                scope["category"]
            )
            where.append(category_sql)
            parameters.extend(category_parameters)
        if scope["model"]:
            where.append("p.model_id = ?")
            parameters.append(int(scope["model"]["id"]))
        return connection.execute(
            "SELECT p.id, CAST(p.stock AS INTEGER) AS stock, p.stock AS raw_stock, "
            "p.excel_name_raw, "
            "p.excel_article, p.brand_id, p.category_id, p.model_id, "
            "p.excel_brand, p.excel_category, p.model, p.bitrix_thumbnail_url, "
            "COALESCE((SELECT MAX(m.rowid) FROM catalog_stock_movements m "
            "WHERE m.product_id = p.id), 0) AS movement_rowid "
            "FROM catalog_excel_products p WHERE " + " AND ".join(where) +
            " ORDER BY p.id",
            parameters,
        ).fetchall()

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

    def start(self, brand_id, user_name="", category_id=None, model_id=None,
              idempotency_key=""):
        self.initialize()
        now = utc_now()
        idempotency_key = str(idempotency_key or "").strip() or None
        with self.database.transaction() as connection:
            if idempotency_key:
                repeated = connection.execute(
                    "SELECT id FROM erp_inventory_sessions WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if repeated:
                    return self._detail(connection, repeated["id"]), False
            scope = self._scope(connection, brand_id, category_id, model_id)
            brand = scope["brand"]
            products = self._snapshot_products(connection, scope)
            if not products:
                raise InventoryError("В выбранной области нет товаров для инвентаризации.")
            for row in products:
                try:
                    raw_stock = float(row["raw_stock"])
                except (TypeError, ValueError):
                    raise InventoryError(
                        "В выбранной области есть некорректные остатки; "
                        "сначала исправьте данные склада."
                    )
                if raw_stock < 0:
                    raise InventoryError(
                        "В выбранной области есть отрицательные остатки; "
                        "сначала исправьте данные склада."
                    )
                if abs(raw_stock - int(raw_stock)) > 0.000001:
                    raise InventoryError(
                        "В выбранной области есть дробные остатки; "
                        "сначала исправьте данные склада."
                    )
            product_ids = [int(row["id"]) for row in products]
            conflicts = connection.execute(
                "SELECT s.id, s.scope_type, s.brand_id, s.category_id, s.model_id, "
                "s.scope_brand_name, s.scope_category_name, s.scope_model_name, "
                "i.product_id "
                "FROM erp_inventory_items i JOIN erp_inventory_sessions s "
                "ON s.id = i.session_id WHERE s.status = 'active' "
                "ORDER BY s.started_at, s.id, i.product_id"
            ).fetchall()
            requested_ids = set(product_ids)
            overlapping_sessions = {}
            for conflict_row in conflicts:
                product_id = int(conflict_row["product_id"])
                if product_id not in requested_ids:
                    continue
                current = overlapping_sessions.setdefault(
                    conflict_row["id"],
                    {"session": conflict_row, "product_ids": set()},
                )
                current["product_ids"].add(product_id)
            if overlapping_sessions:
                overlap = next(iter(overlapping_sessions.values()))
                conflict = overlap["session"]
                same_scope = (
                    (conflict["scope_type"] or "brand") == scope["type"]
                    and int(conflict["brand_id"]) == int(brand["id"])
                    and (conflict["category_id"] or None) == (
                        scope["category"]["id"] if scope["category"] else None
                    )
                    and (conflict["model_id"] or None) == (
                        scope["model"]["id"] if scope["model"] else None
                    )
                )
                active_count = connection.execute(
                    "SELECT COUNT(*) FROM erp_inventory_items WHERE session_id = ?",
                    (conflict["id"],),
                ).fetchone()[0]
                if (
                    same_scope
                    and len(overlap["product_ids"]) == len(products)
                    and int(active_count) == len(products)
                ):
                    return self._detail(connection, conflict["id"]), False
                conflict_label = self._scope_label(
                    conflict["scope_brand_name"] or brand["name"],
                    conflict["scope_category_name"], conflict["scope_model_name"],
                )
                raise InventoryConflict(
                    "Нельзя начать инвентаризацию: часть товаров уже участвует "
                    "в активной инвентаризации №{} ({}).".format(
                        conflict["id"], conflict_label
                    )
                )
            session_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO erp_inventory_sessions (id, brand_id, active_brand_id, "
                "scope_type, category_id, model_id, idempotency_key, scope_brand_name, "
                "scope_category_name, scope_model_name, status, started_by, started_at, "
                "start_positions, updated_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, "
                "'active', ?, ?, ?, ?)",
                (session_id, brand["id"], scope["type"],
                 scope["category"]["id"] if scope["category"] else None,
                 scope["model"]["id"] if scope["model"] else None,
                 idempotency_key, brand["name"],
                 scope["category"]["name"] if scope["category"] else None,
                 scope["model"]["name"] if scope["model"] else None,
                 user_name or None, now, len(products), now),
            )
            for product in products:
                connection.execute(
                    "INSERT INTO erp_inventory_items (id, session_id, product_id, "
                    "snapshot_stock, status, appearance, snapshot_at, snapshot_movement_rowid, "
                    "snapshot_name, snapshot_article, snapshot_brand_id, snapshot_category_id, "
                    "snapshot_model_id, snapshot_brand_name, snapshot_category_name, "
                    "snapshot_model_name, snapshot_photo_url) VALUES (?, ?, ?, ?, 'pending', "
                    "'snapshot', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, session_id, product["id"], int(product["stock"]),
                     now, int(product["movement_rowid"]), product["excel_name_raw"],
                     product["excel_article"], product["brand_id"], product["category_id"],
                     product["model_id"], product["excel_brand"], product["excel_category"],
                     product["model"], product["bitrix_thumbnail_url"]),
                )
            AuditJournal(self.database).record(
                "inventory", session_id, "created",
                "Инвентаризация · {}".format(scope["label"]),
                object_secondary=session_id,
                metadata={
                    "number": session_id,
                    "brand": brand["name"],
                    "brand_id": brand["id"],
                    "scope_type": scope["type"],
                    "category_id": scope["category"]["id"] if scope["category"] else None,
                    "model_id": scope["model"]["id"] if scope["model"] else None,
                    "scope_label": scope["label"],
                    "positions": len(products),
                },
                actor_id=user_name, actor_name=user_name,
                actor_type="user" if user_name else "system",
                occurred_at=now, connection=connection,
            )
            return self._detail(connection, session_id), True

    def get(self, session_id):
        self.initialize()
        with self.database.connect() as connection:
            return self._detail(connection, session_id)

    def active_for_brand(self, brand_id):
        """Return canonical active progress for the warehouse brand banner."""
        if brand_id in (None, ""):
            return None
        try:
            brand_id = int(brand_id)
        except (TypeError, ValueError):
            return None
        self.initialize()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM erp_inventory_sessions "
                "WHERE brand_id = ? AND status = 'active' LIMIT 1",
                (brand_id,),
            ).fetchone()
            return self._detail(connection, row["id"]) if row else None

    def list_active(self):
        """Return every active inventory so none is hidden by brand filters."""
        self.initialize()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM erp_inventory_sessions "
                "WHERE status = 'active' ORDER BY started_at DESC, id"
            ).fetchall()
            return [self._detail(connection, row["id"]) for row in rows]

    @staticmethod
    def _history_rows(connection):
        rows = connection.execute(
            "SELECT s.*, b.name AS brand_name, c.name AS category_name, "
            "mo.name AS model_name, COUNT(i.id) AS total_positions, "
            "COALESCE(SUM(CASE WHEN i.status IN "
            "('confirmed','adjusted','added','missing') THEN 1 ELSE 0 END),0) "
            "AS checked_positions, "
            "COALESCE(SUM(CASE WHEN COALESCE(i.quantity_delta,0) <> 0 "
            "THEN 1 ELSE 0 END),0) AS discrepancy_positions, "
            "COALESCE(SUM(CASE WHEN i.quantity_delta > 0 "
            "THEN i.quantity_delta ELSE 0 END),0) AS surplus, "
            "COALESCE(SUM(CASE WHEN i.quantity_delta < 0 "
            "THEN -i.quantity_delta ELSE 0 END),0) AS shortage "
            "FROM erp_inventory_sessions s "
            "JOIN erp_brands b ON b.id = s.brand_id "
            "LEFT JOIN erp_categories c ON c.id = s.category_id "
            "LEFT JOIN erp_models mo ON mo.id = s.model_id "
            "LEFT JOIN erp_inventory_items i ON i.session_id = s.id "
            "GROUP BY s.id ORDER BY s.started_at DESC, s.id DESC"
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in (
                "total_positions", "checked_positions", "discrepancy_positions",
                "surplus", "shortage",
            ):
                item[key] = int(item.get(key) or 0)
            item["scope_type"] = item.get("scope_type") or "legacy"
            item["scope_label"] = BrandInventory._scope_label(
                item.get("scope_brand_name") or item.get("brand_name") or "—",
                item.get("scope_category_name") or item.get("category_name"),
                item.get("scope_model_name") or item.get("model_name"),
            )
            item["employee"] = (
                item.get("completed_by") or item.get("cancelled_by")
                or item.get("started_by") or ""
            )
            result.append(item)
        return result

    def list_history(self, filters=None):
        """Return saved inventory documents without recalculating legacy values."""
        self.initialize()
        filters = filters or {}
        with self.database.connect() as connection:
            rows = self._history_rows(connection)
        query = str(filters.get("q") or "").strip().casefold()
        date_from = str(filters.get("date_from") or "").strip()
        date_to = str(filters.get("date_to") or "").strip()
        brand_id = str(filters.get("brand_id") or "").strip()
        category_id = str(filters.get("category_id") or "").strip()
        model_id = str(filters.get("model_id") or "").strip()
        employee = str(filters.get("employee") or "").strip().casefold()
        status = str(filters.get("status") or "").strip()
        discrepancies_only = str(filters.get("discrepancies") or "") == "1"

        def matches(item):
            searchable = " ".join(str(item.get(key) or "") for key in (
                "id", "scope_label", "employee", "brand_name",
                "scope_category_name", "scope_model_name",
            )).casefold()
            started_date = str(item.get("started_at") or "")[:10]
            if query and query not in searchable:
                return False
            if date_from and started_date < date_from:
                return False
            if date_to and started_date > date_to:
                return False
            if brand_id and str(item.get("brand_id") or "") != brand_id:
                return False
            if category_id and str(item.get("category_id") or "") != category_id:
                return False
            if model_id and str(item.get("model_id") or "") != model_id:
                return False
            if employee and employee not in str(item.get("employee") or "").casefold():
                return False
            if status and item.get("status") != status:
                return False
            if discrepancies_only and not item.get("discrepancy_positions"):
                return False
            return True

        return [item for item in rows if matches(item)]

    def history_facets(self):
        self.initialize()
        with self.database.connect() as connection:
            rows = self._history_rows(connection)
            brands = [dict(row) for row in connection.execute(
                "SELECT id, name FROM erp_brands WHERE active = 1 "
                "ORDER BY name COLLATE NOCASE, id"
            ).fetchall()]
            categories = [dict(row) for row in connection.execute(
                "SELECT id, name FROM erp_categories WHERE active = 1 "
                "ORDER BY name COLLATE NOCASE, id"
            ).fetchall()]
            models = [dict(row) for row in connection.execute(
                "SELECT id, name FROM erp_models WHERE active = 1 "
                "ORDER BY name COLLATE NOCASE, id"
            ).fetchall()]
        employees = sorted(set(
            item["employee"] for item in rows if item.get("employee")
        ), key=lambda value: value.casefold())
        return {
            "brands": brands, "categories": categories, "models": models,
            "employees": employees,
        }

    def brand_summary(self):
        """Return every catalog brand and its latest completed saved document."""
        self.initialize()
        with self.database.connect() as connection:
            histories = self._history_rows(connection)
            brands = [dict(row) for row in connection.execute(
                "SELECT id, name FROM erp_brands WHERE active = 1 "
                "ORDER BY name COLLATE NOCASE, id"
            ).fetchall()]
        latest = {}
        active = set()
        for item in histories:
            brand_id = int(item["brand_id"])
            if item["status"] == "active":
                active.add(brand_id)
            if item["status"] == "completed":
                current = latest.get(brand_id)
                item_key = (str(item.get("completed_at") or ""), str(item["id"]))
                current_key = (
                    (str(current.get("completed_at") or ""), str(current["id"]))
                    if current else ("", "")
                )
                if current is None or item_key > current_key:
                    latest[brand_id] = item
        result = []
        today = datetime.now(timezone.utc).date()
        for brand in brands:
            item = latest.get(int(brand["id"]))
            days_since = None
            if item and item.get("completed_at"):
                raw_date = str(item["completed_at"])[:10]
                try:
                    days_since = (today - datetime.strptime(
                        raw_date, "%Y-%m-%d"
                    ).date()).days
                except (TypeError, ValueError):
                    days_since = None
            scope_type = item.get("scope_type") if item else None
            check_type = (
                "full" if scope_type == "brand"
                else "partial" if scope_type in ("category", "model", "legacy")
                else "never"
            )
            result.append({
                "id": brand["id"], "brand_name": brand["name"],
                "latest": item, "days_since": days_since,
                "check_type": check_type,
                "has_active": int(brand["id"]) in active,
            })
        return result

    def document_items(self, session_id):
        """Read the immutable saved item values for a document card."""
        self.initialize()
        with self.database.connect() as connection:
            self._session(connection, session_id)
            rows = connection.execute(
                "SELECT i.id, i.product_id, i.snapshot_name AS name, "
                "i.snapshot_article AS article, i.snapshot_brand_name AS brand_name, "
                "i.snapshot_category_name AS category_name, "
                "i.snapshot_model_name AS model_name, i.snapshot_stock, "
                "i.actual_stock, i.quantity_delta, i.final_stock, i.status, "
                "i.movement_id, i.confirmed_at, i.confirmed_by "
                "FROM erp_inventory_items i WHERE i.session_id = ? "
                "ORDER BY COALESCE(i.snapshot_name, '') COLLATE NOCASE, i.id",
                (str(session_id),),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                delta = item.get("quantity_delta")
                item["result"] = (
                    "surplus" if delta is not None and int(delta) > 0
                    else "shortage" if delta is not None and int(delta) < 0
                    else "match" if delta is not None and int(delta) == 0
                    else "other"
                )
                result.append(item)
            return result

    def list_items(self, session_id, query="", category_id=None, limit=250, offset=0):
        self.initialize()
        with self.database.connect() as connection:
            self._session(connection, session_id)
            where = ["i.session_id = ?", "i.status IN ('pending', 'conflict', 'error')"]
            params = [str(session_id)]
            query = str(query or "").strip()
            if query:
                where.append(
                    "(COALESCE(i.snapshot_name, p.excel_name_raw) LIKE ? OR "
                    "COALESCE(i.snapshot_article, p.excel_article, '') LIKE ?)"
                )
                term = "%{}%".format(query)
                params.extend((term, term))
            if category_id not in (None, ""):
                try:
                    category_id = int(category_id)
                except (TypeError, ValueError):
                    raise InventoryError("Категория не найдена.")
                where.append("COALESCE(i.snapshot_category_id, p.category_id) = ?")
                params.append(category_id)
            limit = max(1, min(int(limit or 250), 500))
            offset = max(0, int(offset or 0))
            rows = connection.execute(
                "SELECT i.*, COALESCE(i.snapshot_name, p.excel_name_raw) AS name, "
                "COALESCE(i.snapshot_article, p.excel_article) AS article, "
                "p.stock AS current_stock, "
                "COALESCE(i.snapshot_photo_url, p.bitrix_thumbnail_url) AS photo_url, "
                "COALESCE(i.snapshot_category_id, p.category_id) AS category_id, "
                "COALESCE(i.snapshot_category_name, p.excel_category, c.name) AS category_name, "
                "COALESCE(i.snapshot_model_name, p.model) AS model_name "
                "FROM erp_inventory_items i "
                "JOIN catalog_excel_products p ON p.id = i.product_id "
                "LEFT JOIN erp_categories c ON c.id = COALESCE(i.snapshot_category_id, p.category_id) "
                "WHERE {} ORDER BY COALESCE(i.snapshot_name, p.excel_name_raw) "
                "COLLATE NOCASE, p.id LIMIT ? OFFSET ?".format(
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
            return {
                "item_id": item["id"], "system_stock": int(product["stock"]),
                "status": "pending", "state": "unverified",
                "checked": False, "needs_recheck": False,
            }

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
                    "status": "conflict", "state": "needs_recheck",
                    "checked": False, "needs_recheck": True,
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
            if session["scope_type"]:
                raise InventoryError(
                    "Состав этой инвентаризации зафиксирован при создании."
                )
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
            if session["scope_type"]:
                raise InventoryError(
                    "Нельзя добавлять SKU вне зафиксированного snapshot."
                )
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
            reactivated = 0 if product["active"] else 1
            connection.execute(
                "UPDATE catalog_excel_products SET active = 1, "
                "source_key = COALESCE(deleted_source_key, source_key), "
                "deleted_at = NULL, deleted_by = NULL, "
                "deleted_stock = NULL, delete_mode = NULL, deleted_source_key = NULL, updated_at = ? "
                "WHERE id = ?", (now, product["id"]),
            )
            connection.execute(
                "INSERT INTO erp_inventory_items (id, session_id, product_id, snapshot_stock, "
                "status, appearance, snapshot_at, snapshot_movement_rowid, reactivated) "
                "VALUES (?, ?, ?, ?, 'pending', 'existing', ?, ?, ?)",
                (item_id, session["id"], product["id"], int(product["stock"]), now,
                 self._latest_movement_rowid(connection, product["id"]), reactivated),
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
            if session["scope_type"]:
                raise InventoryError(
                    "Нельзя добавлять SKU вне зафиксированного snapshot."
                )
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
            require_unique_article(connection, article)
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
                "units_to_write_off": 0,
                "blocked_pending_units": int(
                    by_status.get("pending", {}).get("stock", 0)
                ),
            }

    def complete(self, session_id, user_name="", confirmation=False, failure_hook=None):
        if not confirmation:
            raise InventoryError("Подтвердите завершение инвентаризации.")
        self.initialize()
        with self.database.transaction() as connection:
            session = self._session(connection, session_id)
            if session["status"] == "completed":
                return {
                    "ok": True, "repeated": True,
                    "session": self._detail(connection, session_id),
                }
            if session["status"] != "active":
                raise InventoryConflict("Инвентаризация уже завершена или отменена.")
            pending = connection.execute(
                "SELECT i.*, p.stock, "
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
                    latest > int(row["snapshot_movement_rowid"])
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
            if pending:
                return {
                    "ok": False,
                    "conflict": True,
                    "count": len(pending),
                    "message": (
                        "Подтвердите все позиции перед завершением. "
                        "Непроверенные товары не списываются автоматически."
                    ),
                }
            if failure_hook:
                failure_hook(connection)
            now = utc_now()
            self._refresh_totals(connection, session["id"])
            connection.execute(
                "UPDATE erp_inventory_sessions SET status = 'completed', active_brand_id = NULL, "
                "completed_by = ?, "
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
                "UPDATE erp_inventory_sessions SET status = 'cancelled', active_brand_id = NULL, "
                "cancelled_by = ?, "
                "cancelled_at = ?, cancelled_reason = ?, updated_at = ? WHERE id = ?",
                (user_name or None, now, reason, now, session["id"]),
            )
            AuditJournal(self.database).record(
                "inventory", session["id"], "cancelled",
                "Инвентаризация · {}".format(self._scope_label(
                    session["scope_brand_name"] or session["brand_name"],
                    session["scope_category_name"], session["scope_model_name"],
                )),
                object_secondary=session["id"],
                before={"status": "active"},
                after={"status": "cancelled"},
                metadata={
                    "number": session["id"],
                    "brand": session["brand_name"],
                    "brand_id": session["brand_id"],
                    "positions": session["start_positions"],
                    "reason": reason,
                    "text_snapshot": reason,
                },
                actor_id=user_name, actor_name=user_name,
                actor_type="user" if user_name else "system",
                occurred_at=now, status="cancelled",
                connection=connection,
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
        return item, product

    def _detail(self, connection, session_id):
        row = self._session(connection, session_id)
        data = dict(row)
        totals = connection.execute(
            "SELECT COUNT(*) AS total_positions, "
            "SUM(CASE WHEN status IN ('confirmed','adjusted','added','missing') "
            "THEN 1 ELSE 0 END) AS checked_positions, "
            "SUM(CASE WHEN status IN ('pending','conflict','error') "
            "THEN 1 ELSE 0 END) AS remaining, "
            "SUM(CASE WHEN status = 'added' THEN 1 ELSE 0 END) AS added_positions, "
            "SUM(CASE WHEN COALESCE(quantity_delta,0) <> 0 THEN 1 ELSE 0 END) "
            "AS discrepancy_positions, "
            "SUM(CASE WHEN quantity_delta > 0 THEN quantity_delta ELSE 0 END) "
            "AS surplus, "
            "SUM(CASE WHEN quantity_delta < 0 THEN -quantity_delta ELSE 0 END) "
            "AS shortage "
            "FROM erp_inventory_items WHERE session_id = ?", (row["id"],)
        ).fetchone()
        for key in (
            "total_positions", "checked_positions", "remaining", "added_positions",
            "discrepancy_positions", "surplus", "shortage",
        ):
            data[key] = int(totals[key] or 0)
        data["locked_positions"] = (
            data["total_positions"] if data["status"] == "active" else 0
        )
        data["progress_percent"] = (
            int(round(100.0 * data["checked_positions"] / data["total_positions"]))
            if data["total_positions"] else 100
        )
        data["scope_type"] = data.get("scope_type") or "brand"
        data["legacy_scope"] = row["scope_type"] is None
        data["scope_label"] = self._scope_label(
            data.get("scope_brand_name") or data["brand_name"],
            data.get("scope_category_name"), data.get("scope_model_name"),
        )
        data["categories"] = [dict(item) for item in connection.execute(
            "SELECT DISTINCT COALESCE(i.snapshot_category_id, p.category_id) AS id, "
            "COALESCE(i.snapshot_category_name, p.excel_category, c.name) AS name "
            "FROM erp_inventory_items i JOIN catalog_excel_products p ON p.id = i.product_id "
            "LEFT JOIN erp_categories c ON c.id = COALESCE(i.snapshot_category_id, p.category_id) "
            "WHERE i.session_id = ? AND COALESCE(i.snapshot_category_name, "
            "p.excel_category, c.name, '') <> '' ORDER BY name COLLATE NOCASE",
            (row["id"],)
        ).fetchall()]
        return data

    @staticmethod
    def _item_dict(row):
        data = dict(row)
        for key in ("snapshot_stock", "actual_stock", "final_stock", "quantity_delta", "current_stock"):
            if data.get(key) is not None:
                data[key] = int(data[key])
        data["checked"] = data.get("status") in FINAL_STATUSES
        data["needs_recheck"] = data.get("status") == "conflict"
        data["state"] = (
            "needs_recheck" if data["needs_recheck"]
            else "confirmed" if data["checked"]
            else "unverified"
        )
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
            "state": "confirmed", "checked": True, "needs_recheck": False,
            "result": "already_confirmed" if repeated else "confirmed",
            "action_type": "inventory_item_confirmed",
            "message": (
                "Остаток изменён с {} на {}".format(before, after)
                if changed else "Проведено успешно"
            ),
        }
