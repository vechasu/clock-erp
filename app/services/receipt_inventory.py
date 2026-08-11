"""Atomic local receipt stock ledger with idempotent create/update/cancel."""

import json
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal


class ReceiptInventoryError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def positive_number(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ReceiptInventoryError("{} должно быть числом.".format(label))
    if not math.isfinite(number) or number <= 0:
        raise ReceiptInventoryError("{} должно быть больше нуля.".format(label))
    return number


def positive_integer(value, label):
    if isinstance(value, bool) or value in (None, ""):
        raise ReceiptInventoryError(
            "{} должно быть целым положительным числом.".format(label)
        )
    if isinstance(value, str):
        raw = value.strip()
        if not raw or "," in raw:
            raise ReceiptInventoryError(
                "{} должно быть целым положительным числом.".format(label)
            )
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ReceiptInventoryError(
            "{} должно быть целым положительным числом.".format(label)
        )
    if not math.isfinite(number) or number <= 0 or not number.is_integer():
        raise ReceiptInventoryError(
            "{} должно быть целым положительным числом.".format(label)
        )
    return int(number)


def optional_nonnegative_number(value, label):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ReceiptInventoryError("{} должно быть числом.".format(label))
    if not math.isfinite(number) or number < 0:
        raise ReceiptInventoryError(
            "{} должно быть неотрицательным.".format(label)
        )
    return number


class ReceiptInventory:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    def create_receipt(
        self,
        receipt,
        positions,
        idempotency_key="",
        user_name="",
        failure_hook=None,
        tenant_id="default",
    ):
        receipt_id = str(receipt.get("id") or "").strip()
        if not receipt_id:
            raise ReceiptInventoryError("У прихода отсутствует ID.")
        prepared = self._prepare_positions(positions)
        now = utc_now()
        idempotency_key = str(idempotency_key or "").strip() or None
        tenant_id = self._tenant_id(tenant_id)
        self.database.initialize()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id, status FROM erp_receipts WHERE id = ? "
                "OR (? IS NOT NULL AND tenant_id = ? AND idempotency_key = ?) "
                "LIMIT 1",
                (
                    receipt_id,
                    idempotency_key,
                    tenant_id,
                    idempotency_key,
                ),
            ).fetchone()
            created = existing is None
            if existing is not None:
                if existing["status"] != "draft":
                    return self._receipt_payload(connection, existing["id"])
                receipt_id = existing["id"]
            else:
                self._insert_draft(
                    connection,
                    receipt,
                    prepared,
                    receipt_id=receipt_id,
                    idempotency_key=idempotency_key,
                    user_name=user_name,
                    tenant_id=tenant_id,
                    now=now,
                )
            self._post_draft(
                connection,
                receipt_id,
                user_name=user_name,
                failure_hook=failure_hook,
                now=now,
            )
            if created:
                AuditJournal(self.database).record(
                    "receipt", receipt_id, "created",
                    "Приход #{}".format(receipt.get("number") or receipt_id),
                    after={
                        "status": "posted",
                        "quantity": sum(item["quantity"] for item in prepared),
                        "document": receipt.get("number") or receipt_id,
                        "comment": receipt.get("comment") or receipt.get("note") or "",
                        "receipt_date": receipt.get("receipt_date") or receipt.get("created_at"),
                    },
                    metadata={"number": receipt.get("number") or receipt_id},
                    actor_id=user_name, actor_name=user_name,
                    actor_type="user" if user_name else "system",
                    status="posted", connection=connection,
                )
        return self.get_receipt(receipt_id)

    def create_draft(
        self,
        receipt,
        positions,
        idempotency_key="",
        user_name="",
        tenant_id="default",
    ):
        """Persist editable receipt lines without changing stock."""
        receipt_id = str(receipt.get("id") or "").strip()
        if not receipt_id:
            raise ReceiptInventoryError("У прихода отсутствует ID.")
        prepared = self._prepare_positions(positions)
        idempotency_key = str(idempotency_key or "").strip() or None
        tenant_id = self._tenant_id(tenant_id)
        now = utc_now()
        self.database.initialize()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM erp_receipts WHERE id = ? "
                "OR (? IS NOT NULL AND tenant_id = ? AND idempotency_key = ?) "
                "LIMIT 1",
                (
                    receipt_id,
                    idempotency_key,
                    tenant_id,
                    idempotency_key,
                ),
            ).fetchone()
            if existing is not None:
                return self._receipt_payload(connection, existing["id"])
            self._insert_draft(
                connection,
                receipt,
                prepared,
                receipt_id=receipt_id,
                idempotency_key=idempotency_key,
                user_name=user_name,
                tenant_id=tenant_id,
                now=now,
            )
            AuditJournal(self.database).record(
                "receipt", receipt_id, "created",
                "Приход #{}".format(receipt.get("number") or receipt_id),
                after={
                    "status": "draft",
                    "quantity": sum(item["quantity"] for item in prepared),
                    "document": receipt.get("number") or receipt_id,
                    "comment": receipt.get("comment") or receipt.get("note") or "",
                    "receipt_date": receipt.get("receipt_date") or receipt.get("created_at"),
                }, metadata={"number": receipt.get("number") or receipt_id},
                actor_id=user_name, actor_name=user_name,
                actor_type="user" if user_name else "system",
                status="draft", connection=connection,
            )
        return self.get_receipt(receipt_id)

    def update_draft(
        self,
        receipt_id,
        receipt,
        positions,
        user_name="",
    ):
        """Replace draft lines while keeping stock and movement history untouched."""
        receipt_id = str(receipt_id or "").strip()
        prepared = self._prepare_positions(positions)
        now = utc_now()
        self.database.initialize()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM erp_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if current is None:
                raise ReceiptInventoryError("Черновик прихода не найден.")
            if current["status"] != "draft":
                raise ReceiptInventoryError(
                    "Проведённый приход изменяется только через корректировку."
                )
            old_items = connection.execute(
                "SELECT quantity, purchase_price FROM erp_receipt_items "
                "WHERE receipt_id = ? AND active = 1", (receipt_id,)
            ).fetchall()
            products = self._load_products(connection, prepared)
            connection.execute(
                "UPDATE erp_receipt_items SET active = 0 "
                "WHERE receipt_id = ? AND active = 1",
                (receipt_id,),
            )
            self._insert_items(
                connection,
                receipt_id,
                prepared,
                products,
                now,
            )
            connection.execute(
                "UPDATE erp_receipts SET number = ?, comment = ?, receipt_date = ?, "
                "user_name = ?, metadata_json = ?, updated_at = ? WHERE id = ?",
                (
                    str(receipt.get("number") or current["number"] or "") or None,
                    str(
                        receipt.get("comment")
                        if "comment" in receipt
                        else receipt.get("note")
                        if "note" in receipt
                        else current["comment"] or ""
                    ),
                    str(
                        receipt.get("receipt_date")
                        or current["receipt_date"]
                    )[:10],
                    str(user_name or current["user_name"] or "") or None,
                    json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                    now,
                    receipt_id,
                ),
            )
            before = {
                "status": current["status"],
                "quantity": sum(float(item["quantity"]) for item in old_items),
                "document": current["number"] or receipt_id,
                "comment": current["comment"] or "",
                "receipt_date": current["receipt_date"],
            }
            after = {
                "status": current["status"],
                "quantity": sum(item["quantity"] for item in prepared),
                "document": receipt.get("number") or current["number"] or receipt_id,
                "comment": receipt.get("comment") if "comment" in receipt else receipt.get("note") if "note" in receipt else current["comment"] or "",
                "receipt_date": receipt.get("receipt_date") or current["receipt_date"],
            }
            if before != after:
                changed = {key for key in before if before[key] != after[key]}
                action = "comment_added" if changed == {"comment"} and after["comment"] else "updated"
                AuditJournal(self.database).record(
                    "receipt", receipt_id, action,
                    "Приход #{}".format(after["document"]),
                    before=before, after=after,
                    metadata={
                        "number": after["document"],
                        "text_snapshot": after["comment"] if action == "comment_added" else "",
                    }, actor_id=user_name, actor_name=user_name,
                    actor_type="user" if user_name else "system",
                    status="draft", connection=connection,
                )
        return self.get_receipt(receipt_id)

    def post_receipt(
        self,
        receipt_id,
        user_name="",
        failure_hook=None,
    ):
        """Post a saved draft once; repeated calls are stock-neutral."""
        receipt_id = str(receipt_id or "").strip()
        now = utc_now()
        self.database.initialize()
        with self.database.transaction() as connection:
            before = connection.execute(
                "SELECT number, status FROM erp_receipts WHERE id = ?", (receipt_id,)
            ).fetchone()
            self._post_draft(
                connection,
                receipt_id,
                user_name=user_name,
                failure_hook=failure_hook,
                now=now,
            )
            if before is not None and before["status"] == "draft":
                AuditJournal(self.database).record(
                    "receipt", receipt_id, "status_changed",
                    "Приход #{}".format(before["number"] or receipt_id),
                    before={"status": "draft"}, after={"status": "posted"},
                    metadata={"number": before["number"] or receipt_id},
                    actor_id=user_name, actor_name=user_name,
                    actor_type="user" if user_name else "system",
                    status="posted", connection=connection,
                )
        return self.get_receipt(receipt_id)

    def update_receipt(
        self,
        receipt_id,
        receipt,
        positions,
        idempotency_key="",
        user_name="",
        failure_hook=None,
    ):
        receipt_id = str(receipt_id or "").strip()
        prepared = self._prepare_positions(positions)
        now = utc_now()
        idempotency_key = str(idempotency_key or "").strip() or None
        self.database.initialize()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM erp_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if current is None:
                raise ReceiptInventoryError("Приход не найден в локальном журнале.")
            if current["status"] != "posted":
                raise ReceiptInventoryError(
                    "Изменять остаток можно только у проведённого прихода."
                )
            if idempotency_key:
                repeated = connection.execute(
                    "SELECT 1 FROM catalog_stock_movements "
                    "WHERE idempotency_key LIKE ? LIMIT 1",
                    (idempotency_key + ":%",),
                ).fetchone()
                if repeated is not None:
                    return self._receipt_payload(connection, receipt_id)

            old_rows = connection.execute(
                "SELECT * FROM erp_receipt_items "
                "WHERE receipt_id = ? AND active = 1",
                (receipt_id,),
            ).fetchall()
            old_product_ids = {int(row["product_id"]) for row in old_rows}
            new_product_ids = {
                int(position["product_id"]) for position in prepared
            }
            if new_product_ids != old_product_ids:
                raise ReceiptInventoryError(
                    "Товар проведённого прихода изменить нельзя. "
                    "Отмените приход и создайте новый."
                )
            old_totals = defaultdict(float)
            for row in old_rows:
                old_totals[int(row["product_id"])] += float(row["quantity"])
            new_totals = defaultdict(float)
            for position in prepared:
                new_totals[position["product_id"]] += position["quantity"]
            products = self._load_products(
                connection,
                [
                    {"product_id": product_id}
                    for product_id in set(old_totals) | set(new_totals)
                ],
                include_archived=True,
            )
            deltas = {
                product_id: new_totals[product_id] - old_totals[product_id]
                for product_id in set(old_totals) | set(new_totals)
            }
            for product_id, delta in deltas.items():
                stock = float(products[product_id]["stock"] or 0)
                if stock + delta < -0.000001:
                    raise ReceiptInventoryError(
                        "Приход нельзя изменить: по товару ID {} "
                        "недостаточно остатка для отката.".format(product_id)
                    )

            connection.execute(
                "UPDATE erp_receipt_items SET active = 0 "
                "WHERE receipt_id = ? AND active = 1",
                (receipt_id,),
            )
            new_item_ids = {}
            for index, position in enumerate(prepared):
                product = products[position["product_id"]]
                connection.execute(
                    "INSERT INTO erp_receipt_items "
                    "(receipt_id, product_id, brand_id, category_id, quantity, "
                    "purchase_price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        receipt_id,
                        position["product_id"],
                        product["brand_id"],
                        product["category_id"],
                        position["quantity"],
                        position["purchase_price"],
                        now,
                    ),
                )
                new_item_ids[position["product_id"]] = connection.execute(
                    "SELECT last_insert_rowid()"
                ).fetchone()[0]

            for index, (product_id, delta) in enumerate(sorted(deltas.items())):
                if abs(delta) < 0.000001:
                    continue
                stock_before = float(products[product_id]["stock"] or 0)
                stock_after = stock_before + delta
                connection.execute(
                    "UPDATE catalog_excel_products SET stock = ?, "
                    "stock_source = 'receipt', updated_at = ? WHERE id = ?",
                    (stock_after, now, product_id),
                )
                connection.execute(
                    "INSERT INTO catalog_stock_movements "
                    "(id, product_id, movement_type, quantity_delta, stock_before, "
                    "stock_after, receipt_id, receipt_item_id, idempotency_key, "
                    "tenant_id, source_type, source_id, source_line_id, "
                    "operation_kind, source_number, source, user_name, comment, "
                    "created_at) "
                    "VALUES (?, ?, 'manual_adjustment', ?, ?, ?, ?, ?, ?, ?, "
                    "'receipt', ?, ?, 'adjust', ?, 'Приход', ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        product_id,
                        delta,
                        stock_before,
                        stock_after,
                        receipt_id,
                        new_item_ids.get(product_id),
                        (
                            "{}:{}".format(idempotency_key, index)
                            if idempotency_key
                            else None
                        ),
                        current["tenant_id"],
                        receipt_id,
                        str(new_item_ids.get(product_id) or "product:{}".format(
                            product_id
                        )),
                        str(receipt.get("number") or current["number"] or receipt_id),
                        str(user_name or "") or None,
                        "Корректировка прихода №{}".format(
                            receipt.get("number") or receipt_id
                        ),
                        now,
                    ),
                )
            connection.execute(
                "UPDATE erp_receipts SET number = ?, comment = ?, receipt_date = ?, "
                "metadata_json = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    str(
                        receipt.get("document_number")
                        or receipt.get("number")
                        or current["number"]
                        or ""
                    ).strip() or None,
                    str(
                        receipt.get("comment")
                        if "comment" in receipt
                        else receipt.get("note")
                        if "note" in receipt
                        else current["comment"] or ""
                    ),
                    str(
                        receipt.get("receipt_date")
                        or current["receipt_date"]
                    )[:10],
                    json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                    now,
                    receipt_id,
                ),
            )
            if failure_hook:
                failure_hook(connection)
            document = str(
                receipt.get("document_number")
                or receipt.get("number")
                or current["number"]
                or receipt_id
            )
            before = {
                "status": current["status"],
                "quantity": sum(float(row["quantity"]) for row in old_rows),
                "document": current["number"] or receipt_id,
                "comment": current["comment"] or "",
                "receipt_date": current["receipt_date"],
                "purchase_price": sum(
                    float(row["purchase_price"] or 0) for row in old_rows
                ),
            }
            after = {
                "status": current["status"],
                "quantity": sum(item["quantity"] for item in prepared),
                "document": document,
                "comment": receipt.get("comment") if "comment" in receipt else receipt.get("note") if "note" in receipt else current["comment"] or "",
                "receipt_date": receipt.get("receipt_date") or current["receipt_date"],
                "purchase_price": sum(
                    float(item["purchase_price"] or 0) for item in prepared
                ),
            }
            if before != after:
                changed = {key for key in before if before[key] != after[key]}
                action = "comment_added" if changed == {"comment"} and after["comment"] else "updated"
                AuditJournal(self.database).record(
                    "receipt", receipt_id, action,
                    "Приход #{}".format(document), before=before, after=after,
                    metadata={
                        "number": document,
                        "text_snapshot": after["comment"] if action == "comment_added" else "",
                    }, actor_id=user_name, actor_name=user_name,
                    actor_type="user" if user_name else "system",
                    status="posted", connection=connection,
                )
        return self.get_receipt(receipt_id)

    def can_cancel(self, receipt_id):
        receipt_id = str(receipt_id or "").strip()
        self.database.initialize()
        with self.database.connect() as connection:
            receipt = connection.execute(
                "SELECT * FROM erp_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if receipt is None:
                return False
            if receipt["status"] == "cancelled":
                return True
            rows = connection.execute(
                "SELECT i.product_id, SUM(i.quantity) AS quantity, p.stock "
                "FROM erp_receipt_items i "
                "JOIN catalog_excel_products p ON p.id = i.product_id "
                "WHERE i.receipt_id = ? AND i.active = 1 GROUP BY i.product_id",
                (receipt_id,),
            ).fetchall()
            for row in rows:
                if float(row["stock"] or 0) < float(row["quantity"] or 0):
                    raise ReceiptInventoryError(
                        "Приход нельзя отменить: товар ID {} уже частично списан.".format(
                            row["product_id"]
                        )
                    )
        return True

    def cancel_receipt(
        self,
        receipt_id,
        idempotency_key="",
        user_name="",
        failure_hook=None,
        reason="",
    ):
        receipt_id = str(receipt_id or "").strip()
        now = utc_now()
        idempotency_key = str(idempotency_key or "").strip() or None
        self.database.initialize()
        with self.database.transaction() as connection:
            receipt = connection.execute(
                "SELECT * FROM erp_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if receipt is None:
                raise ReceiptInventoryError("Приход не найден в локальном журнале.")
            if receipt["status"] == "cancelled":
                return self._receipt_payload(connection, receipt_id)
            rows = connection.execute(
                "SELECT i.*, p.stock FROM erp_receipt_items i "
                "JOIN catalog_excel_products p ON p.id = i.product_id "
                "WHERE i.receipt_id = ? AND i.active = 1 ORDER BY i.id",
                (receipt_id,),
            ).fetchall()
            totals = defaultdict(float)
            products = {}
            for row in rows:
                totals[int(row["product_id"])] += float(row["quantity"])
                products[int(row["product_id"])] = row
            for product_id, quantity in totals.items():
                if float(products[product_id]["stock"] or 0) < quantity:
                    raise ReceiptInventoryError(
                        "Приход нельзя отменить: товар ID {} уже частично списан.".format(
                            product_id
                        )
                    )
            for index, (product_id, quantity) in enumerate(sorted(totals.items())):
                stock_before = float(products[product_id]["stock"] or 0)
                stock_after = stock_before - quantity
                connection.execute(
                    "UPDATE catalog_excel_products SET stock = ?, "
                    "stock_source = 'receipt_cancel', updated_at = ? WHERE id = ?",
                    (stock_after, now, product_id),
                )
                connection.execute(
                    "INSERT INTO catalog_stock_movements "
                    "(id, product_id, movement_type, quantity_delta, stock_before, "
                    "stock_after, receipt_id, idempotency_key, tenant_id, "
                    "source_type, source_id, source_line_id, operation_kind, "
                    "source_number, source, user_name, comment, created_at) "
                    "VALUES (?, ?, 'cancellation', ?, ?, ?, ?, ?, ?, "
                    "'receipt', ?, ?, 'cancel', ?, 'Приход', ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        product_id,
                        -quantity,
                        stock_before,
                        stock_after,
                        receipt_id,
                        (
                            "{}:{}".format(idempotency_key, index)
                            if idempotency_key
                            else None
                        ),
                        receipt["tenant_id"],
                        receipt_id,
                        "product:{}".format(product_id),
                        str(receipt["number"] or receipt_id),
                        str(user_name or "") or None,
                        "Отмена прихода №{}".format(
                            receipt["number"] or receipt_id
                        ),
                        now,
                    ),
                )
            connection.execute(
                "UPDATE erp_receipts SET status = 'cancelled', "
                "cancelled_at = ?, cancelled_by = ?, cancellation_reason = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    now,
                    str(user_name or "") or None,
                    str(reason or "").strip() or None,
                    now,
                    receipt_id,
                ),
            )
            if failure_hook:
                failure_hook(connection)
            AuditJournal(self.database).record(
                "receipt", receipt_id, "cancelled",
                "Приход #{}".format(receipt["number"] or receipt_id),
                before={"status": receipt["status"]},
                after={"status": "cancelled"},
                metadata={
                    "number": receipt["number"] or receipt_id,
                    "reason": str(reason or "").strip(),
                }, actor_id=user_name, actor_name=user_name,
                actor_type="user" if user_name else "system",
                status="cancelled", connection=connection,
            )
        return self.get_receipt(receipt_id)

    def get_receipt(self, receipt_id):
        self.database.initialize()
        with self.database.connect() as connection:
            return self._receipt_payload(connection, receipt_id)

    @staticmethod
    def _receipt_payload(connection, receipt_id):
        receipt = connection.execute(
            "SELECT * FROM erp_receipts WHERE id = ?",
            (str(receipt_id),),
        ).fetchone()
        if receipt is None:
            return None
        items = connection.execute(
            "SELECT * FROM erp_receipt_items "
            "WHERE receipt_id = ? AND active = 1 ORDER BY id",
            (str(receipt_id),),
        ).fetchall()
        result = dict(receipt)
        try:
            result["metadata"] = json.loads(result["metadata_json"] or "{}")
        except (TypeError, ValueError):
            result["metadata"] = {}
        result["items"] = [dict(item) for item in items]
        return result

    def exists(self, receipt_id):
        return self.get_receipt(receipt_id) is not None

    def get_receipt_by_idempotency(self, idempotency_key, tenant_id="default"):
        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            return None
        tenant_id = self._tenant_id(tenant_id)
        self.database.initialize()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM erp_receipts "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
            return (
                self._receipt_payload(connection, row["id"])
                if row is not None
                else None
            )

    @staticmethod
    def _tenant_id(value):
        tenant_id = str(value or "").strip()
        if not tenant_id:
            raise ReceiptInventoryError("Укажите компанию для прихода.")
        return tenant_id

    @classmethod
    def _insert_draft(
        cls,
        connection,
        receipt,
        prepared,
        receipt_id,
        idempotency_key,
        user_name,
        tenant_id,
        now,
    ):
        products = cls._load_products(connection, prepared)
        connection.execute(
            "INSERT INTO erp_receipts "
            "(id, tenant_id, number, comment, status, receipt_date, user_name, "
            "idempotency_key, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
            (
                receipt_id,
                tenant_id,
                str(receipt.get("number") or "") or None,
                str(
                    receipt.get("comment")
                    if "comment" in receipt
                    else receipt.get("note") or ""
                ),
                str(
                    receipt.get("receipt_date")
                    or receipt.get("created_at")
                    or now
                )[:10],
                str(user_name or "") or None,
                idempotency_key,
                json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        cls._insert_items(
            connection,
            receipt_id,
            prepared,
            products,
            now,
        )

    @staticmethod
    def _insert_items(connection, receipt_id, prepared, products, now):
        for position in prepared:
            product = products[position["product_id"]]
            connection.execute(
                "INSERT INTO erp_receipt_items "
                "(receipt_id, product_id, brand_id, category_id, quantity, "
                "purchase_price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt_id,
                    position["product_id"],
                    product["brand_id"],
                    product["category_id"],
                    position["quantity"],
                    position["purchase_price"],
                    now,
                ),
            )

    @classmethod
    def _post_draft(
        cls,
        connection,
        receipt_id,
        user_name,
        failure_hook,
        now,
    ):
        receipt = connection.execute(
            "SELECT * FROM erp_receipts WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        if receipt is None:
            raise ReceiptInventoryError("Приход не найден в локальном журнале.")
        if receipt["status"] == "posted":
            return
        if receipt["status"] == "cancelled":
            raise ReceiptInventoryError("Отменённый приход нельзя провести повторно.")
        items = connection.execute(
            "SELECT * FROM erp_receipt_items "
            "WHERE receipt_id = ? AND active = 1 ORDER BY id",
            (receipt_id,),
        ).fetchall()
        if not items:
            raise ReceiptInventoryError("Добавьте хотя бы один товар.")
        products = cls._load_products(
            connection,
            [{"product_id": item["product_id"]} for item in items],
        )
        for item in items:
            product_id = int(item["product_id"])
            product = products[product_id]
            stock_before = float(product["stock"] or 0)
            quantity = float(item["quantity"])
            stock_after = stock_before + quantity
            connection.execute(
                "UPDATE catalog_excel_products SET stock = ?, "
                "stock_source = 'receipt', updated_at = ? "
                "WHERE id = ? AND active = 1",
                (stock_after, now, product_id),
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
                    product_id,
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
                    str(user_name or receipt["user_name"] or "") or None,
                    "Приход №{}".format(receipt["number"] or receipt_id),
                    now,
                ),
            )
            products[product_id] = {
                **dict(product),
                "stock": stock_after,
            }
        if failure_hook:
            failure_hook(connection)
        connection.execute(
            "UPDATE erp_receipts SET status = 'posted', user_name = ?, "
            "updated_at = ? WHERE id = ? AND status = 'draft'",
            (
                str(user_name or receipt["user_name"] or "") or None,
                now,
                receipt_id,
            ),
        )

    @staticmethod
    def _prepare_positions(positions):
        if not isinstance(positions, list) or not positions:
            raise ReceiptInventoryError("Добавьте хотя бы один товар.")
        prepared = []
        for index, position in enumerate(positions, start=1):
            if not isinstance(position, dict):
                raise ReceiptInventoryError(
                    "Позиция {} заполнена некорректно.".format(index)
                )
            try:
                product_id = int(position.get("product_id"))
            except (TypeError, ValueError):
                raise ReceiptInventoryError(
                    "Товар в позиции {} не найден.".format(index)
                )
            prepared.append({
                "product_id": product_id,
                "quantity": positive_integer(
                    position.get("quantity"),
                    "Количество",
                ),
                "purchase_price": optional_nonnegative_number(
                    position.get("purchase_price"),
                    "Цена закупки",
                ),
            })
        return prepared

    @staticmethod
    def _load_products(connection, positions, include_archived=False):
        product_ids = sorted({
            int(position["product_id"])
            for position in positions
        })
        placeholders = ", ".join("?" for _ in product_ids)
        active_sql = "" if include_archived else " AND active = 1"
        rows = connection.execute(
            "SELECT id, stock, brand_id, category_id, active "
            "FROM catalog_excel_products WHERE id IN ({}){}".format(
                placeholders,
                active_sql,
            ),
            product_ids,
        ).fetchall()
        products = {int(row["id"]): row for row in rows}
        missing = [product_id for product_id in product_ids if product_id not in products]
        if missing:
            raise ReceiptInventoryError(
                "Товар не найден: ID {}.".format(", ".join(map(str, missing)))
            )
        return products
