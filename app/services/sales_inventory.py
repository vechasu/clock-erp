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


class CancellationConflictError(SalesInventoryError):
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


def optional_nonnegative_number(value, label):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SalesInventoryError("{} должна быть числом.".format(label))
    if not math.isfinite(number) or number < 0:
        raise SalesInventoryError(
            "{} должна быть неотрицательной.".format(label)
        )
    return number


class SalesInventory:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

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
        unit_price = optional_nonnegative_number(unit_price, "Цена продажи")

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
                "OR (? IS NOT NULL AND source = ? AND external_order_id = ? "
                "AND cancelled_at IS NULL AND deleted_at IS NULL) "
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
        unit_price = optional_nonnegative_number(unit_price, "Цена продажи")
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
            metadata = dict(payload)
            old_quantity = float(item["quantity"])
            old_returned = float(item["returned_quantity"] or 0)
            requested_product_id = str(
                metadata.get("product_id", item["product_id"])
            ).strip()
            try:
                product_id = int(requested_product_id)
            except (TypeError, ValueError):
                raise SalesInventoryError("Товар не найден.")

            old_product_id = int(item["product_id"])
            requested_status = str(
                metadata.get("order_status") or sale["status"] or "completed"
            )
            if requested_status in {"returned", "cancelled"}:
                new_returned = quantity
            elif old_returned >= old_quantity - 0.000001:
                new_returned = 0.0
            else:
                new_returned = min(old_returned, quantity)
            old_effect = old_quantity - old_returned
            new_effect = quantity - new_returned
            source = str(metadata.get("source") or sale["source"])
            sale_number = metadata.get("order_number") or sale_id

            def adjust_stock(target_product_id, delta, key, comment):
                if abs(delta) <= 0.000001:
                    return None
                product_row = connection.execute(
                    "SELECT id, stock, brand_id, category_id "
                    "FROM catalog_excel_products WHERE id = ? AND active = 1",
                    (target_product_id,),
                ).fetchone()
                if product_row is None:
                    raise SalesInventoryError("Товар не найден.")
                if delta < 0:
                    cursor = connection.execute(
                        "UPDATE catalog_excel_products SET stock = stock + ?, "
                        "stock_source = 'sale', updated_at = ? "
                        "WHERE id = ? AND active = 1 AND stock >= ?",
                        (delta, updated_at, target_product_id, abs(delta)),
                    )
                    if cursor.rowcount != 1:
                        raise InsufficientStockError(product_row["stock"])
                else:
                    cursor = connection.execute(
                        "UPDATE catalog_excel_products SET stock = stock + ?, "
                        "stock_source = 'sale', updated_at = ? "
                        "WHERE id = ? AND active = 1",
                        (delta, updated_at, target_product_id),
                    )
                    if cursor.rowcount != 1:
                        raise SalesInventoryError("Товар не найден.")
                stock_after = float(product_row["stock"]) + delta
                connection.execute(
                    "INSERT INTO catalog_stock_movements ("
                    "id, product_id, movement_type, quantity_delta, stock_after, "
                    "sale_id, sale_item_id, idempotency_key, source, user_name, "
                    "comment, created_at) VALUES (?, ?, 'manual_adjustment', "
                    "?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), target_product_id, delta, stock_after,
                        sale_id, item["id"], key, source,
                        str(user_name or "") or None, comment, updated_at,
                    ),
                )
                return product_row

            if product_id != old_product_id:
                adjust_stock(
                    old_product_id,
                    old_effect,
                    idempotency_key,
                    "Корректировка продажи №{}: возврат старого товара".format(
                        sale_number
                    ),
                )
                product = adjust_stock(
                    product_id,
                    -new_effect,
                    None,
                    "Корректировка продажи №{}: списание нового товара".format(
                        sale_number
                    ),
                )
            else:
                product = adjust_stock(
                    product_id,
                    old_effect - new_effect,
                    idempotency_key,
                    "Корректировка продажи №{}".format(sale_number),
                )
            if product is None:
                product = connection.execute(
                    "SELECT id, stock, brand_id, category_id "
                    "FROM catalog_excel_products WHERE id = ? AND active = 1",
                    (product_id,),
                ).fetchone()
            if product is None:
                raise SalesInventoryError("Товар не найден.")

            metadata.update({
                "id": sale_id,
                "product_id": str(product_id),
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
                    "WHERE source = ? AND external_order_id = ? AND id <> ? "
                    "AND cancelled_at IS NULL AND deleted_at IS NULL",
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
            item_status = (
                "returned"
                if new_returned >= quantity - 0.000001
                else "partially_returned"
                if new_returned > 0
                else "completed"
            )
            returned_at = (
                updated_at
                if new_returned > 0
                else None
            )
            connection.execute(
                "UPDATE erp_sale_items SET product_id = ?, brand_id = ?, "
                "category_id = ?, quantity = ?, unit_price = ?, "
                "returned_quantity = ?, status = ?, returned_at = ? WHERE id = ?",
                (
                    product_id, product["brand_id"], product["category_id"],
                    quantity, unit_price, new_returned, item_status,
                    returned_at, item["id"],
                ),
            )
            connection.execute(
                "UPDATE erp_sales SET status = ?, returned_at = ?, "
                "updated_at = ? WHERE id = ?",
                (item_status, returned_at, updated_at, sale_id),
            )
            if failure_hook:
                failure_hook(connection)
        return self.get_sale(sale_id)

    def cancel_sale(
        self,
        sale_id,
        reason="",
        comment="",
        user_name="",
        idempotency_key="",
        failure_hook=None,
    ):
        sale_id = str(sale_id or "").strip()
        reason = str(reason or "").strip()
        comment = str(comment or "").strip()
        user_name = str(user_name or "").strip()
        cancelled_at = now_iso()
        base_key = str(idempotency_key or "").strip() or (
            "sale-cancel:{}".format(sale_id)
        )

        self.initialize()
        with self.database.transaction() as connection:
            sale = connection.execute(
                "SELECT * FROM erp_sales WHERE id = ?", (sale_id,)
            ).fetchone()
            items = connection.execute(
                "SELECT * FROM erp_sale_items WHERE sale_id = ? ORDER BY id",
                (sale_id,),
            ).fetchall()
            if sale is None or not items:
                raise CancellationConflictError("Продажа не найдена.")
            if sale["deleted_at"]:
                raise CancellationConflictError("Продажа уже удалена.")
            if sale["cancelled_at"]:
                return self._sale_from_connection(connection, sale_id)
            if str(sale["status"] or "") == "returned":
                raise CancellationConflictError(
                    "Возвращённую продажу нельзя отменить."
                )

            plan = self._movement_plan_from_connection(connection, sale_id)
            if not plan["safe"]:
                raise CancellationConflictError(
                    "Не удалось безопасно определить складское движение "
                    "этой продажи. Остаток не изменён, продажа не отменена."
                )

            item_by_product = {
                int(item["product_id"]): item for item in items
            }
            for index, reversal in enumerate(plan["reversals"]):
                product_id = reversal["product_id"]
                quantity = reversal["quantity"]
                product = connection.execute(
                    "SELECT stock FROM catalog_excel_products WHERE id = ?",
                    (product_id,),
                ).fetchone()
                if product is None:
                    raise CancellationConflictError(
                        "Не удалось безопасно восстановить остаток товара. "
                        "Продажа не отменена."
                    )
                stock_before = float(product["stock"] or 0)
                stock_after = stock_before + quantity
                connection.execute(
                    "UPDATE catalog_excel_products SET stock = ?, "
                    "stock_source = 'sale_cancel', updated_at = ? WHERE id = ?",
                    (stock_after, cancelled_at, product_id),
                )
                item = item_by_product.get(product_id) or items[0]
                movement_key = "{}:{}".format(base_key, index)
                connection.execute(
                    "INSERT INTO catalog_stock_movements ("
                    "id, product_id, movement_type, quantity_delta, "
                    "stock_before, stock_after, sale_id, sale_item_id, "
                    "idempotency_key, source, user_name, comment, created_at"
                    ") VALUES (?, ?, 'cancellation', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), product_id, quantity, stock_before,
                        stock_after, sale_id, item["id"], movement_key,
                        sale["source"], user_name or None,
                        "Отмена продажи №{}: {}{}".format(
                            self._sale_number(sale),
                            reason,
                            ": {}".format(comment) if comment else "",
                        ),
                        cancelled_at,
                    ),
                )

            metadata = self._metadata(sale)
            metadata.update({
                "order_status": "cancelled",
                "cancelled_at": cancelled_at,
                "cancellation_reason": reason,
                "cancellation_comment": comment,
                "cancelled_by": user_name,
            })
            connection.execute(
                "UPDATE erp_sale_items SET returned_quantity = quantity, "
                "status = 'returned', returned_at = ?, return_reason = ? "
                "WHERE sale_id = ?",
                (cancelled_at, reason or "Отмена продажи", sale_id),
            )
            cursor = connection.execute(
                "UPDATE erp_sales SET status = 'returned', returned_at = ?, "
                "return_reason = ?, cancelled_at = ?, cancellation_reason = ?, "
                "cancellation_comment = ?, cancelled_by = ?, metadata_json = ?, "
                "updated_at = ? WHERE id = ? AND cancelled_at IS NULL "
                "AND deleted_at IS NULL",
                (
                    cancelled_at, reason or "Отмена продажи", cancelled_at,
                    reason or None, comment or None, user_name or None,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    cancelled_at, sale_id,
                ),
            )
            if cursor.rowcount != 1:
                raise CancellationConflictError(
                    "Продажа уже была изменена другим запросом."
                )
            if failure_hook:
                failure_hook(connection)

        return self.get_sale(sale_id)

    def delete_sale(
        self,
        sale_id,
        reason="",
        user_name="",
        idempotency_key="",
    ):
        sale_id = str(sale_id or "").strip()
        deleted_at = now_iso()
        self.initialize()
        with self.database.transaction() as connection:
            sale = connection.execute(
                "SELECT * FROM erp_sales WHERE id = ?", (sale_id,)
            ).fetchone()
            item = connection.execute(
                "SELECT * FROM erp_sale_items WHERE sale_id = ? "
                "ORDER BY id LIMIT 1", (sale_id,)
            ).fetchone()
            if sale is None or item is None:
                raise ReturnConflictError("Продажа не найдена.")
            metadata = self._metadata(sale)
            if sale["deleted_at"] or metadata.get("deleted_at"):
                return self._sale_from_connection(connection, sale_id)
            if not sale["cancelled_at"]:
                raise CancellationConflictError(
                    "Сначала отмените продажу, чтобы восстановить остаток."
                )
            metadata["deleted_at"] = deleted_at
            metadata["deleted_by"] = str(user_name or "")
            connection.execute(
                "UPDATE erp_sales SET deleted_at = ?, deleted_by = ?, "
                "metadata_json = ?, updated_at = ? WHERE id = ?",
                (
                    deleted_at, str(user_name or "") or None,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    deleted_at, sale_id,
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
                (optional_nonnegative_number(unit_price, "Цена продажи"), sale_id),
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
        if row is None:
            return None
        plan = cls._movement_plan_from_connection(connection, sale_id)
        return cls._sale_payload(row, plan)

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
        query += " WHERE s.deleted_at IS NULL"
        if sale_id is not None:
            query += " AND s.id = ?"
            parameters = (str(sale_id),)
        query += " ORDER BY s.inserted_at DESC, i.id"
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
            plans = self._movement_plans_from_connection(
                connection,
                [row["id"] for row in rows],
            )
        return [
            payload for payload in (
                self._sale_payload(row, plans.get(row["id"])) for row in rows
            )
            if not payload.get("deleted_at")
        ]

    @classmethod
    def _movement_plans_from_connection(cls, connection, sale_ids):
        sale_ids = list(dict.fromkeys(str(value) for value in sale_ids if value))
        if not sale_ids:
            return {}
        placeholders = ",".join("?" for _value in sale_ids)
        rows = connection.execute(
            "SELECT sale_id, product_id, SUM(quantity_delta) AS net_delta, "
            "COUNT(*) AS movement_count FROM catalog_stock_movements "
            "WHERE sale_id IN ({}) GROUP BY sale_id, product_id".format(
                placeholders
            ),
            sale_ids,
        ).fetchall()
        grouped = {sale_id: [] for sale_id in sale_ids}
        for row in rows:
            grouped.setdefault(str(row["sale_id"]), []).append(row)
        return {
            sale_id: cls._movement_plan_from_rows(grouped.get(sale_id, []))
            for sale_id in sale_ids
        }

    @classmethod
    def _movement_plan_from_connection(cls, connection, sale_id):
        return cls._movement_plans_from_connection(
            connection, [sale_id]
        ).get(str(sale_id), cls._movement_plan_from_rows([]))

    @staticmethod
    def _movement_plan_from_rows(rows):
        reversals = []
        safe = True
        for row in rows:
            net_delta = float(row["net_delta"] or 0)
            if net_delta > 0.000001:
                safe = False
            elif net_delta < -0.000001:
                reversals.append({
                    "product_id": int(row["product_id"]),
                    "quantity": -net_delta,
                })
        return {
            "safe": safe,
            "reversals": reversals,
            "quantity": sum(item["quantity"] for item in reversals),
            "movement_count": sum(int(row["movement_count"] or 0) for row in rows),
        }

    @staticmethod
    def _metadata(sale):
        try:
            return json.loads(sale["metadata_json"] or "{}")
        except (TypeError, ValueError):
            return {}

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
    def _sale_payload(row, movement_plan=None):
        payload = SalesInventory._metadata(row)
        quantity = float(row["quantity"])
        returned_quantity = float(row["returned_quantity"] or 0)
        stored_order_status = str(payload.get("order_status") or "completed")
        inventory_status = str(row["status"] or "completed")
        cancelled_at = row["cancelled_at"] or payload.get("cancelled_at") or ""
        deleted_at = row["deleted_at"] or payload.get("deleted_at") or ""
        movement_plan = movement_plan or {
            "safe": True, "quantity": 0, "movement_count": 0,
        }
        payload.update({
            "id": row["id"],
            "source": row["source"],
            "created_at": row["created_at"],
            "product_id": str(row["product_id"]),
            "quantity": quantity,
            "unit_price": (
                float(row["unit_price"])
                if row["unit_price"] is not None
                else None
            ),
            "status": inventory_status,
            "order_status": (
                "cancelled"
                if cancelled_at
                else inventory_status
                if inventory_status in {"partially_returned", "returned"}
                and stored_order_status != "cancelled"
                else stored_order_status
            ),
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
            "cancelled_at": cancelled_at,
            "cancellation_reason": (
                row["cancellation_reason"]
                or payload.get("cancellation_reason")
                or ""
            ),
            "cancellation_comment": (
                row["cancellation_comment"]
                or payload.get("cancellation_comment")
                or ""
            ),
            "cancelled_by": (
                row["cancelled_by"] or payload.get("cancelled_by") or ""
            ),
            "deleted_at": deleted_at,
            "deleted_by": row["deleted_by"] or payload.get("deleted_by") or "",
            "cancellation_quantity": movement_plan["quantity"],
            "cancellation_safe": movement_plan["safe"],
            "cancellation_has_movements": bool(movement_plan["movement_count"]),
            "inventory_managed": True,
            "automatic_stock_applied": True,
        })
        return payload
