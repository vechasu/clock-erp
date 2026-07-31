"""Repeatable audit and recovery for receipt stock movements."""

import json
import uuid
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.product_reconciliation import normalize_text
from app.services.receipt_inventory import ReceiptInventory, utc_now
from app.services.shared_catalog import DuplicateCatalogValueError, SharedCatalog


class ReceiptRecoveryError(ValueError):
    pass


class ReceiptRecovery:
    def __init__(self, database=None, instance_dir=None):
        self.database = database or CatalogDatabase()
        self.instance_dir = Path(instance_dir or "instance")

    def inspect(self, receipt_number):
        receipt_number = str(receipt_number or "").strip()
        if not receipt_number:
            raise ReceiptRecoveryError("Укажите номер прихода.")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM erp_receipts WHERE number = ? "
                "ORDER BY created_at DESC, id",
                (receipt_number,),
            ).fetchall()
            if rows:
                receipt = self._select_receipt(rows, receipt_number)
                return self._managed_plan(
                    connection,
                    receipt,
                    candidate_count=len(rows),
                    mode="dry-run",
                )
            return self._legacy_plan(connection, receipt_number)

    def apply(self, receipt_number, user_name="receipt-recovery"):
        plan = self.inspect(receipt_number)
        if plan["source"] == "legacy_json":
            return self._apply_legacy(plan, user_name=user_name)
        if plan["status"] != "posted":
            raise ReceiptRecoveryError(
                "Восстанавливать движения можно только у проведённого прихода."
            )
        receipt_id = plan["receipt_id"]
        with self.database.transaction() as connection:
            receipt = connection.execute(
                "SELECT * FROM erp_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if receipt is None or receipt["status"] != "posted":
                raise ReceiptRecoveryError(
                    "Статус прихода изменился после dry-run; восстановление остановлено."
                )
            items = connection.execute(
                "SELECT i.*, p.stock, p.active AS product_active "
                "FROM erp_receipt_items i "
                "JOIN catalog_excel_products p ON p.id = i.product_id "
                "WHERE i.receipt_id = ? AND i.active = 1 ORDER BY i.id",
                (receipt_id,),
            ).fetchall()
            for item in items:
                movement = self._receipt_movement(
                    connection,
                    receipt_id,
                    item["id"],
                )
                if movement is not None:
                    continue
                if not item["product_active"]:
                    raise ReceiptRecoveryError(
                        "Карточка товара ID {} архивирована.".format(
                            item["product_id"]
                        )
                    )
                stock_before = float(item["stock"] or 0)
                quantity = float(item["quantity"])
                stock_after = stock_before + quantity
                now = utc_now()
                connection.execute(
                    "UPDATE catalog_excel_products SET stock = ?, "
                    "stock_source = 'receipt', updated_at = ? WHERE id = ?",
                    (stock_after, now, item["product_id"]),
                )
                connection.execute(
                    "INSERT INTO catalog_stock_movements "
                    "(id, product_id, movement_type, quantity_delta, stock_before, "
                    "stock_after, receipt_id, receipt_item_id, idempotency_key, "
                    "tenant_id, source_type, source_id, source_line_id, "
                    "operation_kind, source_number, source, user_name, comment, "
                    "created_at) "
                    "VALUES (?, ?, 'receipt', ?, ?, ?, ?, ?, ?, ?, 'receipt', ?, ?, "
                    "'post', ?, 'Приход', ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        item["product_id"],
                        quantity,
                        stock_before,
                        stock_after,
                        receipt_id,
                        item["id"],
                        "receipt-post:{}:{}:{}".format(
                            receipt["tenant_id"],
                            receipt_id,
                            item["id"],
                        ),
                        receipt["tenant_id"],
                        receipt_id,
                        str(item["id"]),
                        str(receipt["number"] or receipt_id),
                        user_name,
                        "Восстановление прихода №{}".format(
                            receipt["number"] or receipt_id
                        ),
                        now,
                    ),
                )
            result = self._managed_plan(
                connection,
                receipt,
                candidate_count=plan["candidate_count"],
                mode="apply",
            )
            connection.execute(
                "INSERT INTO erp_receipt_recovery_audit "
                "(id, receipt_id, receipt_number, mode, result_json, created_at) "
                "VALUES (?, ?, ?, 'apply', ?, ?)",
                (
                    str(uuid.uuid4()),
                    receipt_id,
                    receipt["number"],
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
        return result

    @staticmethod
    def _select_receipt(rows, receipt_number):
        posted = [row for row in rows if row["status"] == "posted"]
        if len(posted) == 1:
            return posted[0]
        if len(posted) > 1:
            raise ReceiptRecoveryError(
                "Найдено несколько проведённых приходов с номером {}.".format(
                    receipt_number
                )
            )
        drafts = [row for row in rows if row["status"] == "draft"]
        if len(drafts) == 1:
            return drafts[0]
        if len(drafts) > 1:
            raise ReceiptRecoveryError(
                "Найдено несколько черновиков с номером {}.".format(
                    receipt_number
                )
            )
        return rows[0]

    @classmethod
    def _managed_plan(
        cls,
        connection,
        receipt,
        candidate_count,
        mode,
    ):
        items = connection.execute(
            "SELECT i.*, p.excel_name_raw AS product_name, p.stock, p.active "
            "FROM erp_receipt_items i "
            "LEFT JOIN catalog_excel_products p ON p.id = i.product_id "
            "WHERE i.receipt_id = ? AND i.active = 1 ORDER BY i.id",
            (receipt["id"],),
        ).fetchall()
        positions = []
        for item in items:
            movement = cls._receipt_movement(
                connection,
                receipt["id"],
                item["id"],
            )
            stock_before = (
                float(movement["stock_before"])
                if movement is not None and movement["stock_before"] is not None
                else float(item["stock"] or 0)
            )
            stock_after = (
                float(movement["stock_after"])
                if movement is not None
                else stock_before + float(item["quantity"])
            )
            positions.append({
                "receipt_item_id": item["id"],
                "product_id": str(item["product_id"]),
                "product_name": item["product_name"] or "",
                "quantity": float(item["quantity"]),
                "product_exists": item["product_name"] is not None,
                "movement_exists": movement is not None,
                "movement_id": movement["id"] if movement is not None else "",
                "stock_current": float(item["stock"] or 0),
                "stock_before": stock_before,
                "stock_after": stock_after,
                "action": "none" if movement is not None else "add_movement_and_stock",
            })
        return {
            "mode": mode,
            "source": "erp_receipts",
            "receipt_id": receipt["id"],
            "receipt_number": receipt["number"],
            "status": receipt["status"],
            "tenant_id": receipt["tenant_id"],
            "candidate_count": candidate_count,
            "positions": positions,
            "changes_required": sum(
                item["action"] != "none" for item in positions
            ),
        }

    @staticmethod
    def _receipt_movement(connection, receipt_id, receipt_item_id):
        return connection.execute(
            "SELECT * FROM catalog_stock_movements "
            "WHERE receipt_id = ? AND receipt_item_id = ? "
            "AND movement_type = 'receipt' ORDER BY created_at LIMIT 1",
            (receipt_id, receipt_item_id),
        ).fetchone()

    def _legacy_plan(self, connection, receipt_number):
        records = self._load_legacy_receipts()
        candidates = [
            item for item in records
            if str(item.get("number") or "").strip() == receipt_number
        ]
        if not candidates:
            raise ReceiptRecoveryError(
                "Приход {} не найден.".format(receipt_number)
            )
        active = [
            item for item in candidates
            if str(item.get("status") or "posted") != "cancelled"
        ]
        if len(active) != 1:
            raise ReceiptRecoveryError(
                "Невозможно однозначно выбрать активный приход {}.".format(
                    receipt_number
                )
            )
        receipt = active[0]
        positions = []
        for index, position in enumerate(
            receipt.get("positions") or [receipt]
        ):
            product = self._resolve_legacy_product(connection, position)
            quantity = float(position.get("quantity") or 0)
            positions.append({
                "position_index": index,
                "raw_product_id": str(position.get("product_id") or ""),
                "product_id": str(product["id"]) if product else "",
                "product_name": str(
                    position.get("product_name")
                    or position.get("name")
                    or ""
                ),
                "quantity": quantity,
                "product_exists": product is not None,
                "movement_exists": False,
                "stock_current": float(product["stock"] or 0) if product else 0,
                "stock_before": float(product["stock"] or 0) if product else 0,
                "stock_after": (
                    float(product["stock"] or 0) + quantity
                    if product else quantity
                ),
                "action": (
                    "create_ledger_and_movement"
                    if product else "create_product_ledger_and_movement"
                ),
                "source": dict(position),
            })
        return {
            "mode": "dry-run",
            "source": "legacy_json",
            "receipt_id": str(receipt.get("id") or ""),
            "receipt_number": receipt_number,
            "status": str(receipt.get("status") or "posted"),
            "tenant_id": "default",
            "candidate_count": len(candidates),
            "positions": positions,
            "changes_required": len(positions),
            "receipt": dict(receipt),
        }

    @staticmethod
    def _resolve_legacy_product(connection, position):
        raw_id = str(
            position.get("catalog_product_id")
            or position.get("product_id")
            or ""
        ).strip()
        if raw_id.isdigit():
            row = connection.execute(
                "SELECT * FROM catalog_excel_products "
                "WHERE id = ? AND active = 1",
                (int(raw_id),),
            ).fetchone()
            if row is not None:
                return row
        if raw_id:
            row = connection.execute(
                "SELECT * FROM catalog_excel_products "
                "WHERE moysklad_product_id = ? AND active = 1",
                (raw_id,),
            ).fetchone()
            if row is not None:
                return row

        name = normalize_text(
            position.get("product_name") or position.get("name")
        )
        brand = str(position.get("brand") or "").strip().casefold()
        category = str(position.get("category") or "").strip().casefold()
        article = str(position.get("article") or "").strip().casefold()
        barcode = str(position.get("barcode") or position.get("code") or "").strip()
        if not name or not brand or not category or not (article or barcode):
            return None
        rows = connection.execute(
            "SELECT p.* FROM catalog_excel_products p "
            "LEFT JOIN erp_brands b ON b.id = p.brand_id "
            "LEFT JOIN erp_categories c ON c.id = p.category_id "
            "LEFT JOIN catalog_products cp ON cp.id = p.bitrix_catalog_product_id "
            "WHERE p.active = 1 AND p.normalized_name = ? "
            "AND lower(COALESCE(b.name, p.excel_brand, '')) = ? "
            "AND lower(COALESCE(c.name, p.excel_category, '')) = ? "
            "AND (? = '' OR lower(COALESCE(p.excel_article, '')) = ?) "
            "AND (? = '' OR COALESCE(cp.barcode, '') = ?)",
            (name, brand, category, article, article, barcode, barcode),
        ).fetchall()
        return rows[0] if len(rows) == 1 else None

    def _apply_legacy(self, plan, user_name):
        receipt = dict(plan["receipt"])
        prepared = []
        products = ExcelProductCatalog(self.database)
        shared = SharedCatalog(self.database)
        for planned in plan["positions"]:
            source = dict(planned["source"])
            product_id = planned["product_id"]
            if not product_id:
                name = str(
                    source.get("product_name") or source.get("name") or ""
                ).strip()
                brand = str(source.get("brand") or "").strip()
                category = str(source.get("category") or "").strip()
                if not name or not brand or not category:
                    raise ReceiptRecoveryError(
                        "Недостаточно данных для создания общей карточки позиции {}.".format(
                            planned["position_index"] + 1
                        )
                    )
                try:
                    product = products.create_product(
                        name=name,
                        article=str(source.get("article") or ""),
                        brand=brand,
                        category=category,
                        cell=str(source.get("cell") or ""),
                        stock=0,
                        brand_id=source.get("brand_id"),
                        category_id=source.get("category_id"),
                        enforce_unique=True,
                    )
                except DuplicateCatalogValueError as error:
                    product = error.existing
                product_id = str(product["id"])
                raw_id = str(source.get("product_id") or "").strip()
                if raw_id and not raw_id.isdigit():
                    shared.set_moysklad_product_id(product_id, raw_id)
            prepared.append({
                "product_id": product_id,
                "quantity": source.get("quantity"),
                "purchase_price": source.get("purchase_price") or 0,
            })
        receipt["id"] = plan["receipt_id"] or str(uuid.uuid4())
        receipt["number"] = plan["receipt_number"]
        receipt["receipt_date"] = (
            receipt.get("receipt_date")
            or receipt.get("created_at")
            or utc_now()
        )
        result = ReceiptInventory(self.database).create_receipt(
            receipt,
            prepared,
            idempotency_key="receipt-recovery:{}".format(receipt["id"]),
            user_name=user_name,
        )
        final = self.inspect(plan["receipt_number"])
        final["mode"] = "apply"
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_receipt_recovery_audit "
                "(id, receipt_id, receipt_number, mode, result_json, created_at) "
                "VALUES (?, ?, ?, 'apply', ?, ?)",
                (
                    str(uuid.uuid4()),
                    result["id"],
                    plan["receipt_number"],
                    json.dumps(final, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
        return final

    def _load_legacy_receipts(self):
        path = self.instance_dir / "receipts.json"
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return payload if isinstance(payload, list) else []
