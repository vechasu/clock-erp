import os
import sqlite3
from datetime import datetime, timedelta, timezone

from app.catalog_db import CatalogDatabase


ERP_UNCONFIRMED = "unconfirmed"
ERP_CONFIRMED = "confirmed"
ERP_ASSEMBLED = "assembled"
ERP_STATUS_NAMES = {
    ERP_UNCONFIRMED: "Не подтверждён",
    ERP_CONFIRMED: "Подтверждён",
    ERP_ASSEMBLED: "Собран",
}
ERP_TO_UI_CODE = {
    ERP_UNCONFIRMED: "N",
    ERP_CONFIRMED: "A",
    ERP_ASSEMBLED: "D",
}


class OrderStatusError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _codes(name, default):
    return tuple(
        code.strip().upper()
        for code in os.getenv(name, default).split(",")
        if code.strip()
    )


class OrderStatusMapping:
    """Explicit, configurable mapping for verified Bitrix sale-order statuses."""

    def __init__(self):
        self.inbound = {
            ERP_UNCONFIRMED: _codes(
                "BITRIX_ORDER_STATUS_UNCONFIRMED_CODES", "N,O,0"
            ),
            ERP_CONFIRMED: _codes("BITRIX_ORDER_STATUS_CONFIRMED_CODES", "A"),
            ERP_ASSEMBLED: _codes("BITRIX_ORDER_STATUS_ASSEMBLED_CODES", "D"),
        }
        self.outbound = {
            ERP_UNCONFIRMED: os.getenv(
                "BITRIX_ORDER_STATUS_UNCONFIRMED", "N"
            ).strip().upper(),
            ERP_CONFIRMED: os.getenv(
                "BITRIX_ORDER_STATUS_CONFIRMED", "A"
            ).strip().upper(),
            ERP_ASSEMBLED: os.getenv(
                "BITRIX_ORDER_STATUS_ASSEMBLED", "D"
            ).strip().upper(),
        }

    def from_bitrix(self, code):
        normalized = str(code or "").strip().upper()
        for status, codes in self.inbound.items():
            if normalized in codes:
                return status
        return None


class OrderStatusService:
    def __init__(self, database=None, mapping=None):
        self.database = database or CatalogDatabase()
        self.mapping = mapping or OrderStatusMapping()
        self.database.initialize()

    def _event(
        self, connection, order_id, old_status, new_status, actor,
        source, sync_result, bitrix_status=None, message=None,
    ):
        connection.execute(
            "INSERT INTO erp_order_status_events ("
            "external_order_id, old_status, new_status, actor, source, "
            "bitrix_status, sync_result, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(order_id), old_status, new_status, str(actor or source),
                source, bitrix_status, sync_result, str(message or "") or None,
                _now(),
            ),
        )

    def _queue(self, connection, order_id, erp_status, bitrix_status):
        now = _now()
        updated = connection.execute(
            "UPDATE erp_order_status_sync_queue SET erp_status=?, "
            "bitrix_status=?, attempts=0, next_attempt_at=?, "
            "last_error=NULL, updated_at=? WHERE external_order_id=?",
            (erp_status, bitrix_status, now, now, str(order_id)),
        )
        if updated.rowcount:
            return
        connection.execute(
            "INSERT INTO erp_order_status_sync_queue ("
            "external_order_id, erp_status, bitrix_status, attempts, "
            "next_attempt_at, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?, ?)",
            (str(order_id), erp_status, bitrix_status, now, now, now),
        )

    def get(self, order_id, connection=None):
        owns = connection is None
        connection = connection or self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM erp_order_statuses WHERE external_order_id = ?",
                (str(order_id),),
            ).fetchone()
            return dict(row) if row else None
        finally:
            if owns:
                connection.close()

    def ingest(self, order_id, bitrix_status):
        order_id = str(order_id)
        remote = str(bitrix_status or "").strip().upper()
        mapped = self.mapping.from_bitrix(remote)
        current = self.get(order_id)
        if current is not None:
            if not mapped and current.get("bitrix_status") == remote:
                return current
            if (
                mapped is not None
                and current.get("erp_status") == ERP_ASSEMBLED
                and mapped != ERP_ASSEMBLED
            ):
                return current
            if (
                mapped == current.get("erp_status")
                and current.get("bitrix_status") == remote
            ):
                with self.database.connect() as connection:
                    pending = connection.execute(
                        "SELECT 1 FROM erp_order_status_sync_queue "
                        "WHERE external_order_id=?",
                        (order_id,),
                    ).fetchone()
                if pending is None:
                    return current
        now = _now()
        with self.database.transaction() as connection:
            current = self.get(order_id, connection)
            if current is None:
                initial = mapped or ERP_UNCONFIRMED
                connection.execute(
                    "INSERT INTO erp_order_statuses ("
                    "external_order_id, erp_status, bitrix_status, sync_status, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        order_id, initial, remote or None,
                        "synced" if mapped else "unknown", now, now,
                    ),
                )
                self._event(
                    connection, order_id, None, initial, "Bitrix", "bitrix",
                    "synced" if mapped else "unknown_status", remote,
                    None if mapped else "Неизвестный статус Bitrix сохранён без сопоставления",
                )
                return self.get(order_id, connection)

            if not mapped:
                if current.get("bitrix_status") != remote:
                    pending = connection.execute(
                        "SELECT 1 FROM erp_order_status_sync_queue "
                        "WHERE external_order_id=?", (order_id,),
                    ).fetchone()
                    connection.execute(
                        "UPDATE erp_order_statuses SET bitrix_status=?, "
                        "sync_status=?, updated_at=? WHERE external_order_id=?",
                        (
                            remote or None,
                            "pending" if pending is not None else "unknown",
                            now,
                            order_id,
                        ),
                    )
                    self._event(
                        connection, order_id, current["erp_status"],
                        current["erp_status"], "Bitrix", "bitrix",
                        "unknown_status", remote,
                        "Неизвестный статус Bitrix; статус ERP сохранён",
                    )
                return self.get(order_id, connection)

            pending = connection.execute(
                "SELECT erp_status FROM erp_order_status_sync_queue "
                "WHERE external_order_id=?", (order_id,),
            ).fetchone()
            if pending is not None:
                if pending["erp_status"] == mapped:
                    connection.execute(
                        "DELETE FROM erp_order_status_sync_queue WHERE external_order_id=?",
                        (order_id,),
                    )
                    connection.execute(
                        "UPDATE erp_order_statuses SET bitrix_status=?, "
                        "sync_status='synced', updated_at=? WHERE external_order_id=?",
                        (remote, now, order_id),
                    )
                return self.get(order_id, connection)

            old = current["erp_status"]
            # "Собран" is final and cannot be downgraded by a stale remote read.
            if old == ERP_ASSEMBLED and mapped != ERP_ASSEMBLED:
                return current
            if old != mapped or current.get("bitrix_status") != remote:
                connection.execute(
                    "UPDATE erp_order_statuses SET erp_status=?, bitrix_status=?, "
                    "sync_status='synced', updated_at=? WHERE external_order_id=?",
                    (mapped, remote, now, order_id),
                )
                if old != mapped:
                    self._event(
                        connection, order_id, old, mapped, "Bitrix", "bitrix",
                        "synced", remote,
                    )
            return self.get(order_id, connection)

    def change(self, order_id, target, actor, sale_id=None, connection=None):
        if target not in ERP_STATUS_NAMES:
            raise OrderStatusError("Недопустимый статус заказа")
        owns = connection is None
        context = self.database.transaction() if owns else None
        connection = context.__enter__() if owns else connection
        try:
            current = self.get(order_id, connection)
            if current is None:
                now = _now()
                connection.execute(
                    "INSERT INTO erp_order_statuses (external_order_id, erp_status, "
                    "sync_status, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)",
                    (str(order_id), ERP_UNCONFIRMED, now, now),
                )
                current = self.get(order_id, connection)
            old = current["erp_status"]
            if old == target:
                return current
            allowed = (
                old == ERP_UNCONFIRMED and target == ERP_CONFIRMED
            ) or (
                old == ERP_CONFIRMED and target == ERP_ASSEMBLED and sale_id
            )
            if not allowed:
                raise OrderStatusError("Недопустимый переход статуса")
            if target == ERP_ASSEMBLED and not sale_id:
                raise OrderStatusError("Статус «Собран» требует проведённую продажу")
            bitrix_code = self.mapping.outbound[target]
            now = _now()
            connection.execute(
                "UPDATE erp_order_statuses SET erp_status=?, sale_id=COALESCE(?, sale_id), "
                "sync_status='pending', updated_at=? WHERE external_order_id=?",
                (target, sale_id, now, str(order_id)),
            )
            self._queue(connection, order_id, target, bitrix_code)
            self._event(
                connection, order_id, old, target, actor, "erp", "pending",
                bitrix_code,
            )
            result = self.get(order_id, connection)
            if owns:
                context.__exit__(None, None, None)
            return result
        except Exception as error:
            if owns:
                context.__exit__(type(error), error, error.__traceback__)
            raise

    def overlay(self, order):
        order_id = str(order.get("id") or order.get("external_id") or "")
        if not order_id:
            return order
        try:
            state = self.ingest(order_id, order.get("status"))
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).casefold():
                raise
            bitrix_status = str(order.get("status") or "").strip().upper()
            mapped = self.mapping.from_bitrix(bitrix_status)
            if mapped is None:
                return dict(order)
            state = {
                "bitrix_status": bitrix_status,
                "erp_status": mapped,
                "sync_status": "pending",
            }
        result = dict(order)
        result["bitrix_status"] = state.get("bitrix_status")
        result["erp_status"] = state["erp_status"]
        result["status"] = ERP_TO_UI_CODE[state["erp_status"]]
        result["status_name"] = ERP_STATUS_NAMES[state["erp_status"]]
        result["status_known"] = True
        if state["sync_status"] in {"pending", "error"}:
            result["status_sync_state"] = "pending"
        elif state["sync_status"] == "unknown":
            result["status_sync_state"] = "unknown"
        else:
            result["status_sync_state"] = "synced"
        return result

    def sync_one(self, order_id, sender):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM erp_order_status_sync_queue WHERE external_order_id=?",
                (str(order_id),),
            ).fetchone()
        if row is None:
            return True
        try:
            result = sender(str(order_id), row["bitrix_status"])
        except Exception as error:
            result = {
                "status": "error",
                "code": "BITRIX_{}".format(type(error).__name__.upper()),
            }
        success = isinstance(result, dict) and result.get("status") == "ok"
        now = _now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT erp_status FROM erp_order_statuses WHERE external_order_id=?",
                (str(order_id),),
            ).fetchone()
            if success:
                deleted = connection.execute(
                    "DELETE FROM erp_order_status_sync_queue "
                    "WHERE external_order_id=? AND erp_status=? AND bitrix_status=?",
                    (
                        str(order_id), row["erp_status"], row["bitrix_status"],
                    ),
                )
                if deleted.rowcount != 1:
                    return False
                connection.execute(
                    "UPDATE erp_order_statuses SET bitrix_status=?, "
                    "sync_status='synced', updated_at=? WHERE external_order_id=? "
                    "AND erp_status=?",
                    (
                        row["bitrix_status"], now, str(order_id),
                        row["erp_status"],
                    ),
                )
                self._event(
                    connection, order_id, current["erp_status"],
                    current["erp_status"], "ERP", "erp", "synced",
                    row["bitrix_status"],
                )
            else:
                attempts = int(row["attempts"] or 0) + 1
                delay = min(3600, 30 * (2 ** min(attempts - 1, 7)))
                next_attempt = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
                error_code = str((result or {}).get("code") or "BITRIX_UNAVAILABLE")
                updated = connection.execute(
                    "UPDATE erp_order_status_sync_queue SET attempts=?, "
                    "next_attempt_at=?, last_error=?, updated_at=? "
                    "WHERE external_order_id=? AND erp_status=? AND bitrix_status=?",
                    (
                        attempts, next_attempt, error_code, now, str(order_id),
                        row["erp_status"], row["bitrix_status"],
                    ),
                )
                if updated.rowcount != 1:
                    return False
                connection.execute(
                    "UPDATE erp_order_statuses SET sync_status='error', updated_at=? "
                    "WHERE external_order_id=? AND erp_status=?",
                    (now, str(order_id), row["erp_status"]),
                )
                self._event(
                    connection, order_id, current["erp_status"],
                    current["erp_status"], "ERP", "erp", "retry_scheduled",
                    row["bitrix_status"], error_code,
                )
        return success

    def retry_pending(self, sender, limit=10):
        now = _now()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT external_order_id FROM erp_order_status_sync_queue "
                "WHERE next_attempt_at <= ? ORDER BY updated_at LIMIT ?",
                (now, int(limit)),
            ).fetchall()
        return [self.sync_one(row["external_order_id"], sender) for row in rows]
