"""Idempotently restore product stock from a completed inventory snapshot."""

import uuid

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.brand_inventory import utc_now


class InventoryRestorationError(ValueError):
    pass


class InventorySnapshotRestoration:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    @staticmethod
    def _brand(connection, brand_name):
        rows = connection.execute(
            "SELECT * FROM erp_brands WHERE name = ? COLLATE BINARY",
            (str(brand_name or "").strip(),),
        ).fetchall()
        if len(rows) != 1:
            raise InventoryRestorationError(
                "Бренд с точным названием {!r} не найден.".format(brand_name)
            )
        return rows[0]

    @staticmethod
    def _session(connection, brand_id, session_id=None):
        parameters = [int(brand_id)]
        where = "brand_id = ? AND status = 'completed'"
        if session_id:
            where += " AND id = ?"
            parameters.append(str(session_id))
        row = connection.execute(
            "SELECT * FROM erp_inventory_sessions WHERE {} "
            "ORDER BY completed_at DESC, started_at DESC LIMIT 1".format(where),
            parameters,
        ).fetchone()
        if row is None:
            raise InventoryRestorationError(
                "Завершённая инвентаризация бренда не найдена."
            )
        return row

    def _plan(self, connection, brand_name, session_id=None):
        brand = self._brand(connection, brand_name)
        session = self._session(connection, brand["id"], session_id)
        items = connection.execute(
            "SELECT i.id AS item_id, i.product_id, i.snapshot_stock, "
            "p.excel_name_raw AS name, COALESCE(p.excel_article,'') AS article, "
            "p.stock AS current_stock, p.active, p.brand_id, p.excel_brand, "
            "p.deleted_at, p.deleted_source_key "
            "FROM erp_inventory_items i "
            "LEFT JOIN catalog_excel_products p ON p.id = i.product_id "
            "WHERE i.session_id = ? AND i.appearance = 'snapshot' "
            "ORDER BY i.product_id",
            (session["id"],),
        ).fetchall()
        if not items:
            raise InventoryRestorationError(
                "В инвентаризации нет исходных товарных позиций."
            )
        missing = [row["product_id"] for row in items if row["name"] is None]
        if missing:
            raise InventoryRestorationError(
                "Не найдены карточки товаров: {}.".format(
                    ", ".join(str(value) for value in missing)
                )
            )
        changes = []
        for row in items:
            target = int(row["snapshot_stock"])
            current = float(row["current_stock"] or 0)
            card_changed = (
                int(row["active"] or 0) != 1
                or int(row["brand_id"] or 0) != int(brand["id"])
                or row["excel_brand"] != brand["name"]
                or row["deleted_at"] is not None
            )
            if current != target or card_changed:
                changes.append({
                    "item_id": row["item_id"],
                    "product_id": int(row["product_id"]),
                    "name": row["name"],
                    "article": row["article"],
                    "current_stock": current,
                    "target_stock": target,
                    "restore_delta": target - current,
                    "restore_card": bool(card_changed),
                })
        card_totals = connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active_total, "
            "SUM(CASE WHEN active = 1 AND stock > 0 THEN 1 ELSE 0 END) AS in_stock, "
            "COALESCE(SUM(CASE WHEN active = 1 THEN stock ELSE 0 END),0) AS active_stock "
            "FROM catalog_excel_products WHERE brand_id = ? AND created_at <= ?",
            (brand["id"], session["started_at"]),
        ).fetchone()
        active_sessions = connection.execute(
            "SELECT id FROM erp_inventory_sessions "
            "WHERE brand_id = ? AND status = 'active' ORDER BY started_at",
            (brand["id"],),
        ).fetchall()
        return {
            "brand_id": int(brand["id"]),
            "brand_name": brand["name"],
            "session_id": session["id"],
            "started_at": session["started_at"],
            "completed_at": session["completed_at"],
            "snapshot_positions": len(items),
            "snapshot_stock": sum(int(row["snapshot_stock"]) for row in items),
            "current_snapshot_stock": sum(float(row["current_stock"] or 0) for row in items),
            "changes": changes,
            "positions_to_restore": len(changes),
            "stock_delta": sum(row["restore_delta"] for row in changes),
            "catalog_positions_before": int(card_totals["total"] or 0),
            "active_catalog_positions_before": int(card_totals["active_total"] or 0),
            "current_in_stock_positions": int(card_totals["in_stock"] or 0),
            "current_active_stock": float(card_totals["active_stock"] or 0),
            "active_session_ids": [row["id"] for row in active_sessions],
        }

    def plan(self, brand_name, session_id=None):
        self.database.initialize()
        with self.database.connect() as connection:
            return self._plan(connection, brand_name, session_id)

    def apply(self, brand_name, session_id=None, reason="", user_name=""):
        reason = " ".join(str(reason or "").split())
        if not reason:
            raise InventoryRestorationError("Укажите причину восстановления.")
        self.database.initialize()
        with self.database.transaction() as connection:
            plan = self._plan(connection, brand_name, session_id)
            now = utc_now()
            for active_session_id in plan["active_session_ids"]:
                connection.execute(
                    "UPDATE erp_inventory_sessions SET status = 'cancelled', "
                    "active_brand_id = NULL, cancelled_by = ?, cancelled_at = ?, "
                    "cancelled_reason = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'active'",
                    (user_name or None, now, reason, now, active_session_id),
                )
                AuditJournal(self.database).record(
                    "inventory", active_session_id, "cancelled",
                    "Инвентаризация · {}".format(plan["brand_name"]),
                    metadata={"brand": plan["brand_name"], "reason": reason},
                    actor_name=user_name, actor_type="system",
                    status="cancelled", connection=connection,
                )

            operations = []
            for change in plan["changes"]:
                key = "inventory-restore:{}:{}".format(
                    plan["session_id"], change["product_id"]
                )
                existing = connection.execute(
                    "SELECT id, stock_after FROM catalog_stock_movements "
                    "WHERE idempotency_key = ? LIMIT 1", (key,),
                ).fetchone()
                if existing is not None:
                    if float(existing["stock_after"]) != change["target_stock"]:
                        raise InventoryRestorationError(
                            "Существующая операция восстановления имеет другой итоговый остаток."
                        )
                    continue
                product = connection.execute(
                    "SELECT * FROM catalog_excel_products WHERE id = ?",
                    (change["product_id"],),
                ).fetchone()
                if product is None:
                    raise InventoryRestorationError(
                        "Карточка товара {} исчезла во время восстановления.".format(
                            change["product_id"]
                        )
                    )
                source_key = product["source_key"]
                if product["deleted_at"] is not None:
                    source_key = product["deleted_source_key"] or source_key
                cursor = connection.execute(
                    "UPDATE catalog_excel_products SET stock = ?, stock_source = 'inventory', "
                    "active = 1, brand_id = ?, excel_brand = ?, source_key = ?, "
                    "deleted_at = NULL, deleted_by = NULL, deleted_stock = NULL, "
                    "delete_mode = NULL, deleted_source_key = NULL, updated_at = ? "
                    "WHERE id = ? AND stock = ?",
                    (
                        change["target_stock"], plan["brand_id"], plan["brand_name"],
                        source_key, now, change["product_id"], product["stock"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise InventoryRestorationError(
                        "Остаток товара {} изменился во время восстановления.".format(
                            change["product_id"]
                        )
                    )
                movement_id = None
                if change["restore_delta"]:
                    movement_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO catalog_stock_movements ("
                        "id, product_id, movement_type, quantity_delta, stock_before, "
                        "stock_after, idempotency_key, tenant_id, source_type, source_id, "
                        "source_line_id, operation_kind, source, user_name, comment, created_at"
                        ") VALUES (?, ?, 'inventory_adjustment', ?, ?, ?, ?, 'default', "
                        "'inventory_restore', ?, ?, 'restore', 'Vechasu ERP', ?, ?, ?)",
                        (
                            movement_id, change["product_id"], change["restore_delta"],
                            change["current_stock"], change["target_stock"], key,
                            plan["session_id"], change["item_id"], user_name or None,
                            reason, now,
                        ),
                    )
                AuditJournal(self.database).record(
                    "product", change["product_id"], "restored", change["name"],
                    change["article"],
                    before={"stock": change["current_stock"]},
                    after={"stock": change["target_stock"]},
                    metadata={
                        "article": change["article"], "brand": plan["brand_name"],
                        "inventory_id": plan["session_id"], "reason": reason,
                    },
                    actor_name=user_name, actor_type="system", connection=connection,
                )
                operations.append({
                    "movement_id": movement_id,
                    "product_id": change["product_id"],
                    "stock_before": change["current_stock"],
                    "stock_after": change["target_stock"],
                    "delta": change["restore_delta"],
                })

            if operations or plan["active_session_ids"]:
                AuditJournal(self.database).record(
                    "inventory", plan["session_id"], "restored",
                    "Восстановление товаров {} после ошибочной инвентаризации".format(
                        plan["brand_name"]
                    ),
                    before={"stock": plan["current_snapshot_stock"]},
                    after={"stock": plan["snapshot_stock"]},
                    metadata={
                        "brand": plan["brand_name"], "brand_id": plan["brand_id"],
                        "inventory_id": plan["session_id"], "reason": reason,
                        "restored_positions": len(operations),
                        "movement_ids": [row["movement_id"] for row in operations],
                    },
                    actor_name=user_name, actor_type="system",
                    status="restored", connection=connection,
                )
            result = dict(plan)
            result.update({
                "applied": True,
                "created_operations": operations,
                "created_operation_count": len(operations),
                "cancelled_session_ids": plan["active_session_ids"],
            })
            return result
