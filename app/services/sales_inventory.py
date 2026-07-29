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
            product = connection.execute(
                "SELECT id, stock FROM catalog_excel_products "
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
                "id, source, status, created_at, user_name, metadata_json, "
                "inserted_at, updated_at"
                ") VALUES (?, ?, 'completed', ?, ?, ?, ?, ?)",
                (
                    sale_id,
                    source,
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
                "sale_id, product_id, quantity, unit_price, created_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    sale_id,
                    product_id,
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
                "sale_id, sale_item_id, source, user_name, comment, created_at"
                ") VALUES (?, ?, 'sale', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    product_id,
                    -quantity,
                    stock_after,
                    sale_id,
                    item_id,
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
    ):
        quantity = positive_number(quantity, "Количество возврата")
        returned_at = now_iso()
        sale_id = str(sale_id or "").strip()
        reason = str(reason or "").strip()

        self.initialize()
        with self.database.transaction() as connection:
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
            item_status = (
                "returned"
                if abs(new_returned - float(item["quantity"])) < 0.000001
                else "partially_returned"
            )
            stock_cursor = connection.execute(
                "UPDATE catalog_excel_products "
                "SET stock = stock + ?, stock_source = 'return', "
                "updated_at = ? WHERE id = ? AND active = 1",
                (quantity, returned_at, item["product_id"]),
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
                "sale_id, sale_item_id, source, user_name, comment, created_at"
                ") VALUES (?, ?, 'return', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    item["product_id"],
                    quantity,
                    float(product["stock"]),
                    sale_id,
                    item["id"],
                    sale["source"],
                    str(user_name or "") or None,
                    (
                        "Возврат по продаже №{}{}"
                    ).format(
                        self._sale_number(sale),
                        ": {}".format(reason) if reason else "",
                    ),
                    returned_at,
                ),
            )

        return self.get_sale(sale_id)

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
        sales = self.list_sales(sale_id=sale_id)
        return sales[0] if sales else None

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
                "stock_before": float(row["stock_after"]) - delta,
                "sale_id": row["sale_id"] or "",
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
