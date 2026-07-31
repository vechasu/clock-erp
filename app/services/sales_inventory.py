"""Transactional sales, returns and product stock movements."""

import json
import math
import uuid
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase


class SalesInventoryError(ValueError):
    pass


class InsufficientStockError(SalesInventoryError):
    def __init__(self, available):
        self.available = float(available or 0)
        super().__init__(
            "Недостаточно товара на складе. Доступно: {}".format(
                format_number(self.available)
            )
        )


class ReturnConflictError(SalesInventoryError):
    pass


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def format_number(value):
    value = float(value or 0)
    return str(int(value)) if value.is_integer() else "{:g}".format(value)


def positive_number(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SalesInventoryError("{} должно быть числом.".format(label))
    if not math.isfinite(number) or number <= 0:
        raise SalesInventoryError("{} должно быть больше нуля.".format(label))
    return number


class SalesInventory:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    def initialize(self):
        self.database.initialize()

    def exists(self):
        if not self.database.exists():
            return False
        try:
            with self.database.connect() as connection:
                return connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'erp_sales'"
                ).fetchone() is not None
        except Exception:
            return False

    def create_sale(
        self,
        payload,
        product_id,
        quantity,
        unit_price,
        user_name="",
        idempotency_key="",
        enforce_external_unique=False,
        failure_hook=None,
    ):
        quantity = positive_number(quantity, "Количество")
        try:
            unit_price = float(unit_price)
        except (TypeError, ValueError):
            raise SalesInventoryError("Цена продажи должна быть числом.")
        if not math.isfinite(unit_price) or unit_price < 0:
            raise SalesInventoryError(
                "Цена продажи должна быть неотрицательной."
            )

        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            raise SalesInventoryError("Товар не найден.")

        sale_id = str(payload.get("id") or uuid.uuid4().hex)
        created_at = str(payload.get("created_at") or now_iso())
        source = str(payload.get("source") or "Tictactoy")
        external_order_id = (
            str(payload.get("order_number") or "").strip() or None
            if enforce_external_unique
            else None
        )
        idempotency_key = str(idempotency_key or "").strip() or None
        inserted_at = now_iso()
        stored_payload = dict(payload)
        stored_payload["id"] = sale_id
        stored_payload["product_id"] = str(product_id)
        stored_payload["quantity"] = quantity
        stored_payload["unit_price"] = unit_price
        stored_payload["inventory_managed"] = True
        stored_payload["automatic_stock_applied"] = True

        self.initialize()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM erp_sales WHERE id = ? "
                "OR (? IS NOT NULL AND idempotency_key = ?) "
                "OR (? IS NOT NULL AND source = ? AND external_order_id = ?) "
                "LIMIT 1",
                (
                    sale_id,
                    idempotency_key,
                    idempotency_key,
                    external_order_id,
                    source,
                    external_order_id,
                ),
            ).fetchone()
            if existing is not None:
                return self._sale_from_connection(
                    connection,
                    existing["id"],
                )
            product = connection.execute(
                "SELECT id, stock, brand_id, category_id "
                "FROM catalog_excel_products "
                "WHERE id = ? AND active = 1",
                (product_id,),
            ).fetchone()
            if product is None:
                raise SalesInventoryError("Товар не найден.")

            available = float(product["stock"] or 0)
            cursor = connection.execute(
                "UPDATE catalog_excel_products "
                "SET stock = stock - ?, stock_source = 'sale', updated_at = ? "
                "WHERE id = ? AND active = 1 AND stock >= ?",
                (quantity, inserted_at, product_id, quantity),
            )
            if cursor.rowcount != 1:
                latest = connection.execute(
                    "SELECT stock FROM catalog_excel_products WHERE id = ?",
                    (product_id,),
                ).fetchone()
                raise InsufficientStockError(
                    latest["stock"] if latest else available
                )

            connection.execute(
                "INSERT INTO erp_sales ("
                "id, source, external_order_id, idempotency_key, status, "
                "created_at, user_name, metadata_json, inserted_at, updated_at"
                ") VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?)",
                (
                    sale_id,
                    source,
                    external_order_id,
                    idempotency_key,
                    created_at,
                    str(user_name or "") or None,
                    json.dumps(
                        stored_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    inserted_at,
                    inserted_at,
                ),
            )
            connection.execute(
                "INSERT INTO erp_sale_items ("
                "sale_id, product_id, brand_id, category_id, quantity, "
                "unit_price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    sale_id,
                    product_id,
                    product["brand_id"],
                    product["category_id"],
                    quantity,
                    unit_price,
                    created_at,
                ),
            )
            item_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            stock_after = available - quantity
            connection.execute(
                "INSERT INTO catalog_stock_movements ("
                "id, product_id, movement_type, quantity_delta, stock_after, "
                "sale_id, sale_item_id, idempotency_key, source, user_name, "
                "comment, created_at"
                ") VALUES (?, ?, 'sale', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    product_id,
                    -quantity,
                    stock_after,
                    sale_id,
                    item_id,
                    idempotency_key,
                    source,
                    str(user_name or "") or None,
                    "Продажа №{}".format(
                        stored_payload.get("order_number") or sale_id
                    ),
                    inserted_at,
                ),
            )
            if failure_hook:
                failure_hook(connection)

        return self.get_sale(sale_id)

    def return_sale(
        self,
        sale_id,
        quantity,
        reason="",
        user_name="",
        idempotency_key="",
        movement_type="return",
    ):
        quantity = positive_number(quantity, "Количество возврата")
        returned_at = now_iso()
        sale_id = str(sale_id or "").strip()
        reason = str(reason or "").strip()
        idempotency_key = str(idempotency_key or "").strip() or None
        if movement_type not in {"return", "cancellation"}:
            raise ReturnConflictError("Неизвестный тип возврата.")

        self.initialize()
        with self.database.transaction() as connection:
            if idempotency_key:
                repeated = connection.execute(
                    "SELECT sale_id FROM catalog_stock_movements "
                    "WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if repeated is not None:
                    return self._sale_from_connection(
                        connection,
                        repeated["sale_id"],
                    )
            sale = connection.execute(
                "SELECT * FROM erp_sales WHERE id = ?",
                (sale_id,),
            ).fetchone()
            item = connection.execute(
                "SELECT * FROM erp_sale_items "
                "WHERE sale_id = ? ORDER BY id LIMIT 1",
                (sale_id,),
            ).fetchone()
            if sale is None or item is None:
                raise ReturnConflictError("Продажа не найдена.")

            remaining = (
                float(item["quantity"])
                - float(item["returned_quantity"] or 0)
            )
            if remaining <= 0:
                raise ReturnConflictError("Возврат уже оформлен.")
            if quantity > remaining:
                raise ReturnConflictError(
                    "Можно вернуть не больше {}.".format(
                        format_number(remaining)
                    )
                )

            new_returned = (
                float(item["returned_quantity"] or 0) + quantity
            )
            fully_reversed = (
                abs(new_returned - float(item["quantity"])) < 0.000001
            )
            item_status = (
                "returned" if fully_reversed else "partially_returned"
            )
            stock_cursor = connection.execute(
                "UPDATE catalog_excel_products "
                "SET stock = stock + ?, stock_source = ?, "
                "updated_at = ? WHERE id = ? AND active = 1",
                (
                    quantity,
                    (
                        "sale_cancel"
                        if movement_type == "cancellation"
                        else "return"
                    ),
                    returned_at,
                    item["product_id"],
                ),
            )
            if stock_cursor.rowcount != 1:
                raise ReturnConflictError("Товар не найден.")
            product = connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id = ?",
                (item["product_id"],),
            ).fetchone()
            if product is None:
                raise ReturnConflictError("Товар не найден.")

            item_cursor = connection.execute(
                "UPDATE erp_sale_items SET returned_quantity = ?, "
                "status = ?, returned_at = ?, return_reason = ? "
                "WHERE id = ? AND returned_quantity = ?",
                (
                    new_returned,
                    item_status,
                    returned_at,
                    reason or None,
                    item["id"],
                    item["returned_quantity"],
                ),
            )
            if item_cursor.rowcount != 1:
                raise ReturnConflictError(
                    "Возврат уже был изменён другим запросом."
                )
            connection.execute(
                "UPDATE erp_sales SET status = ?, returned_at = ?, "
                "return_reason = ?, updated_at = ? WHERE id = ?",
                (
                    item_status,
                    returned_at,
                    reason or None,
                    returned_at,
                    sale_id,
                ),
            )
            connection.execute(
                "INSERT INTO catalog_stock_movements ("
                "id, product_id, movement_type, quantity_delta, stock_after, "
                "sale_id, sale_item_id, idempotency_key, source, user_name, "
                "comment, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    item["product_id"],
                    movement_type,
                    quantity,
                    float(product["stock"]),
                    sale_id,
                    item["id"],
                    idempotency_key,
                    sale["source"],
                    str(user_name or "") or None,
                    (
                        "{} по продаже №{}{}"
                    ).format(
                        (
                            "Отмена"
                            if movement_type == "cancellation"
                            else "Возврат"
                        ),
                        self._sale_number(sale),
                        ": {}".format(reason) if reason else "",
                    ),
                    returned_at,
                ),
            )

        return self.get_sale(sale_id)

    def update_sale(
        self,
        sale_id,
        payload,
        quantity,
        unit_price,
        user_name="",
        idempotency_key="",
        failure_hook=None,
    ):
        sale_id = str(sale_id or "").strip()
        quantity = positive_number(quantity, "Количество")
        try:
            unit_price = float(unit_price)
        except (TypeError, ValueError):
            raise SalesInventoryError("Цена продажи должна быть числом.")
        if not math.isfinite(unit_price) or unit_price < 0:
            raise SalesInventoryError(
                "Цена продажи должна быть неотрицательной."
            )
        idempotency_key = str(idempotency_key or "").strip() or None
        updated_at = now_iso()
        self.initialize()
        with self.database.transaction() as connection:
            if idempotency_key:
                repeated = connection.execute(
                    "SELECT sale_id FROM catalog_stock_movements "
                    "WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if repeated is not None:
                    return self._sale_from_connection(
                        connection,
                        repeated["sale_id"],
                    )
            sale = connection.execute(
                "SELECT * FROM erp_sales WHERE id = ?",
                (sale_id,),
            ).fetchone()
            item = connection.execute(
                "SELECT * FROM erp_sale_items "
                "WHERE sale_id = ? ORDER BY id LIMIT 1",
                (sale_id,),
            ).fetchone()
            if sale is None or item is None:
                raise SalesInventoryError("Продажа не найдена.")
            if float(item["returned_quantity"] or 0) > quantity:
                raise SalesInventoryError(
                    "Количество продажи не может быть меньше уже возвращённого."
                )
            old_quantity = float(item["quantity"])
            stock_delta = old_quantity - quantity
            if stock_delta < 0:
                cursor = connection.execute(
                    "UPDATE catalog_excel_products "
                    "SET stock = stock + ?, stock_source = 'sale', updated_at = ? "
                    "WHERE id = ? AND active = 1 AND stock >= ?",
                    (
                        stock_delta,
                        updated_at,
                        item["product_id"],
                        abs(stock_delta),
                    ),
                )
                if cursor.rowcount != 1:
                    latest = connection.execute(
                        "SELECT stock FROM catalog_excel_products WHERE id = ?",
                        (item["product_id"],),
                    ).fetchone()
                    raise InsufficientStockError(
                        latest["stock"] if latest else 0
                    )
            elif stock_delta > 0:
                connection.execute(
                    "UPDATE catalog_excel_products "
                    "SET stock = stock + ?, stock_source = 'sale', updated_at = ? "
                    "WHERE id = ?",
                    (stock_delta, updated_at, item["product_id"]),
                )
            product = connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id = ?",
                (item["product_id"],),
            ).fetchone()
            metadata = dict(payload)
            metadata.update({
                "id": sale_id,
                "product_id": str(item["product_id"]),
                "quantity": quantity,
                "unit_price": unit_price,
                "inventory_managed": True,
                "automatic_stock_applied": True,
            })
            updated_source = str(
                metadata.get("source") or sale["source"]
            )
            updated_external_id = (
                str(metadata.get("order_number") or "").strip() or None
            )
            if updated_external_id:
                duplicate = connection.execute(
                    "SELECT id FROM erp_sales "
                    "WHERE source = ? AND external_order_id = ? AND id <> ?",
                    (updated_source, updated_external_id, sale_id),
                ).fetchone()
                if duplicate is not None:
                    raise SalesInventoryError(
                        "Продажа с таким номером уже существует "
                        "в выбранном источнике."
                    )
            connection.execute(
                "UPDATE erp_sales SET source = ?, external_order_id = ?, "
                "created_at = ?, metadata_json = ?, updated_at = ? WHERE id = ?",
                (
                    updated_source,
                    updated_external_id,
                    str(metadata.get("created_at") or sale["created_at"]),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    updated_at,
                    sale_id,
                ),
            )
            returned = float(item["returned_quantity"] or 0)
            item_status = (
                "returned"
                if abs(returned - quantity) < 0.000001 and returned > 0
                else "partially_returned"
                if returned > 0
                else "completed"
            )
            connection.execute(
                "UPDATE erp_sale_items SET quantity = ?, unit_price = ?, "
                "status = ? WHERE id = ?",
                (quantity, unit_price, item_status, item["id"]),
            )
            connection.execute(
                "UPDATE erp_sales SET status = ? WHERE id = ?",
                (item_status, sale_id),
            )
            if abs(stock_delta) > 0.000001:
                connection.execute(
                    "INSERT INTO catalog_stock_movements ("
                    "id, product_id, movement_type, quantity_delta, stock_after, "
                    "sale_id, sale_item_id, idempotency_key, source, user_name, "
                    "comment, created_at"
                    ") VALUES (?, ?, 'manual_adjustment', ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        item["product_id"],
                        stock_delta,
                        float(product["stock"]),
                        sale_id,
                        item["id"],
                        idempotency_key,
                        str(metadata.get("source") or sale["source"]),
                        str(user_name or "") or None,
                        "Корректировка продажи №{}".format(
                            metadata.get("order_number") or sale_id
                        ),
                        updated_at,
                    ),
                )
            if failure_hook:
                failure_hook(connection)
        return self.get_sale(sale_id)

    def cancel_sale(
        self,
        sale_id,
        reason="",
        user_name="",
        idempotency_key="",
    ):
        sale = self.get_sale(sale_id)
        if sale is None:
            raise ReturnConflictError("Продажа не найдена.")
        remaining = float(sale.get("return_available_quantity") or 0)
        if remaining <= 0:
            return sale
        return self.return_sale(
            sale_id,
            remaining,
            reason=reason or "Отмена продажи",
            user_name=user_name,
            idempotency_key=idempotency_key,
            movement_type="cancellation",
        )

    def update_metadata(self, sale_id, payload, unit_price):
        sale_id = str(sale_id or "").strip()
        self.initialize()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM erp_sales WHERE id = ?",
                (sale_id,),
            ).fetchone()
            if current is None:
                raise SalesInventoryError("Продажа не найдена.")
            metadata = dict(payload)
            metadata["id"] = sale_id
            metadata["inventory_managed"] = True
            metadata["automatic_stock_applied"] = True
            updated_at = now_iso()
            connection.execute(
                "UPDATE erp_sales SET source = ?, created_at = ?, "
                "metadata_json = ?, updated_at = ? WHERE id = ?",
                (
                    str(metadata.get("source") or current["source"]),
                    str(metadata.get("created_at") or current["created_at"]),
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    updated_at,
                    sale_id,
                ),
            )
            connection.execute(
                "UPDATE erp_sale_items SET unit_price = ? WHERE sale_id = ?",
                (float(unit_price), sale_id),
            )
        return self.get_sale(sale_id)

    def get_sale(self, sale_id):
        if not self.exists():
            return None
        with self.database.connect() as connection:
            return self._sale_from_connection(connection, sale_id)

    @classmethod
    def _sale_from_connection(cls, connection, sale_id):
        row = connection.execute(
            "SELECT s.*, i.id AS item_id, i.product_id, i.quantity, "
            "i.unit_price, i.returned_quantity, i.status AS item_status, "
            "i.returned_at AS item_returned_at, "
            "i.return_reason AS item_return_reason "
            "FROM erp_sales s JOIN erp_sale_items i ON i.sale_id = s.id "
            "WHERE s.id = ? ORDER BY i.id LIMIT 1",
            (str(sale_id),),
        ).fetchone()
        return cls._sale_payload(row) if row else None

    def list_sales(self, sale_id=None):
        if not self.exists():
            return []
        query = (
            "SELECT s.*, i.id AS item_id, i.product_id, i.quantity, "
            "i.unit_price, i.returned_quantity, i.status AS item_status, "
            "i.returned_at AS item_returned_at, "
            "i.return_reason AS item_return_reason "
            "FROM erp_sales s JOIN erp_sale_items i ON i.sale_id = s.id"
        )
        parameters = ()
        if sale_id is not None:
            query += " WHERE s.id = ?"
            parameters = (str(sale_id),)
        query += " ORDER BY s.inserted_at DESC, i.id"
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._sale_payload(row) for row in rows]

    def list_movements(self, product_id=None, limit=5000):
        if not self.exists():
            return []
        self.initialize()
        query = (
            "SELECT m.*, s.source AS sale_source "
            "FROM catalog_stock_movements m "
            "LEFT JOIN erp_sales s ON s.id = m.sale_id"
        )
        parameters = []
        if product_id is not None:
            query += " WHERE m.product_id = ?"
            parameters.append(int(product_id))
        query += " ORDER BY m.created_at DESC LIMIT ?"
        parameters.append(int(limit))
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        labels = {
            "initial_stock": "Начальный остаток",
            "receipt": "Приход",
            "sale": "Продажа",
            "return": "Возврат",
            "manual_adjustment": "Ручная корректировка",
            "cancellation": "Отмена",
        }
        result = []
        for row in rows:
            delta = float(row["quantity_delta"])
            result.append({
                "id": row["id"],
                "product_id": str(row["product_id"]),
                "created_at": row["created_at"],
                "type": row["movement_type"],
                "label": labels.get(
                    row["movement_type"],
                    row["movement_type"],
                ),
                "quantity": abs(delta),
                "diff": delta,
                "stock_after": float(row["stock_after"]),
                "stock_before": (
                    float(row["stock_before"])
                    if row["stock_before"] is not None
                    else float(row["stock_after"]) - delta
                ),
                "sale_id": row["sale_id"] or "",
                "receipt_id": row["receipt_id"] or "",
                "receipt_item_id": row["receipt_item_id"] or "",
                "receipt_number": row["source_number"] or "",
                "source": row["sale_source"] or row["source"] or "",
                "user_name": row["user_name"] or "",
                "reason": row["comment"] or "",
            })
        return result

    @staticmethod
    def _sale_number(sale):
        try:
            payload = json.loads(sale["metadata_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        return str(payload.get("order_number") or sale["id"])

    @staticmethod
    def _sale_payload(row):
        try:
            payload = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        quantity = float(row["quantity"])
        returned_quantity = float(row["returned_quantity"] or 0)
        payload.update({
            "id": row["id"],
            "source": row["source"],
            "created_at": row["created_at"],
            "product_id": str(row["product_id"]),
            "quantity": quantity,
            "unit_price": float(row["unit_price"]),
            "status": row["status"],
            "order_status": row["status"],
            "returned_quantity": returned_quantity,
            "return_available_quantity": max(
                quantity - returned_quantity,
                0,
            ),
            "returned_at": (
                row["item_returned_at"]
                or row["returned_at"]
                or ""
            ),
            "return_reason": (
                row["item_return_reason"]
                or row["return_reason"]
                or ""
            ),
            "inventory_managed": True,
            "automatic_stock_applied": True,
        })
        return payload
