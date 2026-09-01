"""Order lifecycle projection backed by the shared immutable audit journal."""

from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.order_status import ERP_STATUS_NAMES


def _parse_timestamp(value):
    raw = str(value or "").strip().replace("Z", "+00:00")
    if len(raw) >= 6 and raw[-6] in {"+", "-"} and raw[-3] == ":":
        raw = raw[:-3] + raw[-2:]
    parsed = None
    for date_format in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
    ):
        try:
            parsed = datetime.strptime(raw, date_format)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError("Unsupported lifecycle timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append("{} дн".format(days))
    if hours:
        parts.append("{} ч".format(hours))
    if minutes:
        parts.append("{} мин".format(minutes))
    if seconds or not parts:
        parts.append("{} сек".format(seconds))
    return " ".join(parts)


class OrderLifecycle:
    """Build deterministic durations from audit facts without storing them."""

    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    def _records(self, order_id):
        order_id = str(order_id or "").strip()
        if not order_id:
            return []
        self.database.initialize()
        with self.database.connect() as connection:
            sale_ids = [str(row[0]) for row in connection.execute(
                "SELECT id FROM erp_sales WHERE external_order_id=?",
                (order_id,),
            ).fetchall()]
            conditions = ["(entity_type='order' AND entity_id=?)"]
            parameters = [order_id]
            if sale_ids:
                placeholders = ",".join("?" for _value in sale_ids)
                conditions.append(
                    "(entity_type='sale' AND entity_id IN ({}))".format(
                        placeholders
                    )
                )
                parameters.extend(sale_ids)
            rows = connection.execute(
                "SELECT * FROM erp_audit_events WHERE {} "
                "ORDER BY occurred_at, id".format(" OR ".join(conditions)),
                parameters,
            ).fetchall()
        return [AuditJournal._deserialize(row) for row in rows]

    @staticmethod
    def _present(event):
        action = event["action"]
        entity_type = event["entity_type"]
        changes = event.get("changes") or {}
        metadata = event.get("metadata") or {}
        status_change = changes.get("status") or {}
        if entity_type == "order" and action in {"created", "system_created"}:
            title = "Заказ создан"
            detail = ERP_STATUS_NAMES.get(status_change.get("after"), "")
        elif entity_type == "order" and action == "status_changed":
            title = "Статус изменён"
            detail = metadata.get("text_snapshot") or "{} → {}".format(
                ERP_STATUS_NAMES.get(status_change.get("before"), status_change.get("before") or "—"),
                ERP_STATUS_NAMES.get(status_change.get("after"), status_change.get("after") or "—"),
            )
        elif entity_type == "sale" and action in {"created", "system_created"}:
            title = "Продажа проведена"
            detail = "Продажа №{}".format(metadata.get("number") or event["entity_id"])
        elif entity_type == "sale" and action in {"cancelled", "refused", "deleted"}:
            title = {
                "cancelled": "Продажа отменена",
                "refused": "Продажа отменена по отказу",
                "deleted": "Продажа удалена",
            }[action]
            detail = "Продажа №{}".format(metadata.get("number") or event["entity_id"])
        else:
            return None
        return {
            "id": event["id"],
            "entity_type": entity_type,
            "entity_id": event["entity_id"],
            "action": action,
            "occurred_at": event["occurred_at"],
            "actor_id": event.get("actor_id"),
            "actor_type": event.get("actor_type"),
            "actor_name": event.get("actor_display_name_snapshot") or "Система",
            "title": title,
            "detail": detail,
            "origin": metadata.get("origin") or "runtime",
        }

    def timeline(self, order_id):
        events = []
        previous_at = None
        for record in self._records(order_id):
            event = self._present(record)
            if event is None:
                continue
            occurred_at = _parse_timestamp(event["occurred_at"])
            elapsed = None if previous_at is None else max(
                0, int((occurred_at - previous_at).total_seconds())
            )
            event["elapsed_seconds"] = elapsed
            event["elapsed_display"] = (
                "" if elapsed is None else format_duration(elapsed)
            )
            local = occurred_at.astimezone()
            event["time_display"] = local.strftime("%H:%M:%S")
            event["date_display"] = local.strftime("%d.%m.%Y")
            events.append(event)
            previous_at = occurred_at
        total = None
        if len(events) > 1 and events[0]["action"] in {"created", "system_created"}:
            terminal = next((
                event for event in events
                if event["entity_type"] == "sale"
                and event["action"] in {"created", "system_created"}
            ), events[-1])
            total = max(0, int((
                _parse_timestamp(terminal["occurred_at"])
                - _parse_timestamp(events[0]["occurred_at"])
            ).total_seconds()))
        return {
            "order_id": str(order_id),
            "events": events,
            "total_seconds": total,
            "total_display": format_duration(total) if total is not None else "",
            "has_multiple_days": len({event["date_display"] for event in events}) > 1,
        }

    def summary(self, order_id, limit=4):
        timeline = self.timeline(order_id)
        timeline["events"] = timeline["events"][-max(1, int(limit)):]
        return timeline
