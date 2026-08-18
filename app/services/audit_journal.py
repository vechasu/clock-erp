"""Append-only business audit journal for ERP mutations."""

import base64
import json
from datetime import datetime, time as datetime_time, timedelta, timezone

from app.catalog_db import CatalogDatabase


ENTITY_TYPES = {"product", "sale", "receipt", "brand", "category", "inventory"}
ACTION_TYPES = {
    "created",
    "updated",
    "status_changed",
    "photo_added",
    "photo_replaced",
    "photo_removed",
    "cancelled",
    "refused",
    "deleted",
    "comment_added",
    "system_created",
}
FIELD_WHITELISTS = {
    "product": {
        "name", "article", "brand", "category", "price", "cell", "stock",
    },
    "sale": {
        "status", "payment", "tracking", "quantity", "unit_price", "source",
        "comment", "order_number",
    },
    "receipt": {
        "status", "quantity", "document", "comment", "receipt_date",
        "purchase_price",
    },
    "brand": {"name"},
    "category": {"name"},
    "inventory": set(),
}
SENSITIVE_MARKERS = {
    "password", "passwd", "secret", "token", "authorization", "credential",
    "cookie", "session", "api_key", "webhook",
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_key(key):
    normalized = str(key or "").strip().casefold()
    return not any(marker in normalized for marker in SENSITIVE_MARKERS)


def _safe_value(value, depth=0):
    if depth > 4:
        return None
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item, depth + 1)
            for key, item in value.items()
            if _safe_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth + 1) for item in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = value if not isinstance(value, str) else value[:2000]
        return text
    return str(value)[:500]


def whitelisted_changes(entity_type, before, after):
    allowed = FIELD_WHITELISTS.get(entity_type, set())
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    changes = {}
    for field in sorted(allowed):
        if field not in before and field not in after:
            continue
        old = _safe_value(before.get(field))
        new = _safe_value(after.get(field))
        if old != new:
            changes[field] = {"before": old, "after": new}
    return changes


def actor_snapshot(actor_id="", actor_name="", actor_type="user"):
    actor_type = actor_type if actor_type in {"user", "system", "external"} else "user"
    actor_id = str(actor_id or "").strip()
    actor_name = str(actor_name or "").strip()
    if not actor_name and actor_id and actor_type == "user":
        actor_name = actor_id
    if not actor_name:
        actor_name = "Система" if actor_type != "user" else "Неизвестный пользователь"
    return actor_id or None, actor_type, actor_name[:240]


class AuditJournal:
    """Writes immutable events and exposes read-only journal queries."""

    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    def record(
        self,
        entity_type,
        entity_id,
        action,
        object_label,
        object_secondary="",
        before=None,
        after=None,
        changes=None,
        metadata=None,
        actor_id="",
        actor_name="",
        actor_type="user",
        occurred_at=None,
        status="",
        source="",
        connection=None,
    ):
        if entity_type not in ENTITY_TYPES:
            raise ValueError("Unsupported audit entity type")
        if action not in ACTION_TYPES:
            raise ValueError("Unsupported audit action")
        entity_id = str(entity_id or "").strip()
        object_label = str(object_label or "").strip()
        if not entity_id or not object_label:
            raise ValueError("Audit entity id and label are required")
        safe_changes = (
            whitelisted_changes(entity_type, before, after)
            if changes is None
            else {
                field: _safe_value(value)
                for field, value in changes.items()
                if field in FIELD_WHITELISTS[entity_type] and _safe_key(field)
            }
        )
        safe_metadata = _safe_value(metadata or {})
        actor_id, actor_type, actor_name = actor_snapshot(
            actor_id, actor_name, actor_type
        )
        occurred_at = str(occurred_at or utc_now())
        secondary = str(object_secondary or "")[:500]
        status = str(status or "")[:120]
        source = str(source or "")[:120]
        search_parts = [
            object_label, secondary, actor_name, source,
            str(safe_metadata.get("text_snapshot") or ""),
            str(safe_metadata.get("article") or ""),
            str(safe_metadata.get("number") or ""),
            str(safe_metadata.get("brand_name_snapshot") or ""),
            str(safe_metadata.get("brand") or ""),
        ]
        values = (
            entity_type, entity_id, action, actor_id, actor_type, actor_name,
            occurred_at, object_label[:500], secondary,
            json.dumps(safe_changes, ensure_ascii=False, sort_keys=True),
            json.dumps(safe_metadata, ensure_ascii=False, sort_keys=True),
            " ".join(search_parts).casefold()[:6000], status, source,
        )

        def insert(target):
            cursor = target.execute(
                "INSERT INTO erp_audit_events ("
                "entity_type, entity_id, action, actor_id, actor_type, "
                "actor_display_name_snapshot, occurred_at, object_label_snapshot, "
                "object_secondary_snapshot, changes_json, metadata_json, "
                "search_text, status_snapshot, source_snapshot"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            return cursor.lastrowid

        if connection is not None:
            return insert(connection)
        self.database.initialize()
        with self.database.transaction() as target:
            return insert(target)

    def list_events(
        self,
        entity_type="",
        entity_id="",
        action="",
        actor="",
        status="",
        source="",
        query="",
        date_from="",
        date_to="",
        cursor="",
        limit=30,
    ):
        self.database.initialize()
        conditions = []
        parameters = []
        if entity_type in ENTITY_TYPES:
            conditions.append("entity_type = ?")
            parameters.append(entity_type)
        if entity_id:
            conditions.append("entity_id = ?")
            parameters.append(str(entity_id))
        if action in ACTION_TYPES:
            conditions.append("action = ?")
            parameters.append(action)
        if actor:
            conditions.append(
                "(actor_display_name_snapshot = ? OR (entity_type = 'inventory' AND EXISTS ("
                "SELECT 1 FROM erp_inventory_sessions ais WHERE ais.id = entity_id AND "
                "(? IN (COALESCE(ais.started_by,''), COALESCE(ais.completed_by,''), "
                "COALESCE(ais.cancelled_by,'')) OR EXISTS (SELECT 1 FROM erp_inventory_items aii "
                "WHERE aii.session_id = ais.id AND aii.confirmed_by = ?)))))"
            )
            parameters.extend([str(actor), str(actor), str(actor)])
        if status and entity_type == "sale":
            conditions.append("status_snapshot = ?")
            parameters.append(str(status))
        if source and entity_type == "sale":
            conditions.append("source_snapshot = ?")
            parameters.append(str(source))
        if query:
            conditions.append(
                "(search_text LIKE ? ESCAPE '\\' OR (entity_type = 'inventory' AND EXISTS ("
                "SELECT 1 FROM erp_inventory_items qi JOIN catalog_excel_products qp "
                "ON qp.id = qi.product_id WHERE qi.session_id = entity_id AND "
                "(qp.excel_name_raw LIKE ? ESCAPE '\\' OR "
                "COALESCE(qp.excel_article,'') LIKE ? ESCAPE '\\'))))"
            )
            escaped = str(query).casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = "%{}%".format(escaped)
            raw_escaped = str(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            raw_term = "%{}%".format(raw_escaped)
            parameters.extend([term, raw_term, raw_term])
        try:
            parsed_from = datetime.strptime(
                str(date_from)[:10], "%Y-%m-%d"
            ).date()
        except (TypeError, ValueError):
            parsed_from = None
        try:
            parsed_to = datetime.strptime(
                str(date_to)[:10], "%Y-%m-%d"
            ).date()
        except (TypeError, ValueError):
            parsed_to = None
        if parsed_from:
            conditions.append("occurred_at >= ?")
            local_timezone = datetime.now().astimezone().tzinfo
            parameters.append(datetime.combine(
                parsed_from, datetime_time.min, tzinfo=local_timezone
            ).astimezone(timezone.utc).isoformat())
        if parsed_to:
            end = parsed_to + timedelta(days=1)
            conditions.append("occurred_at < ?")
            local_timezone = datetime.now().astimezone().tzinfo
            parameters.append(datetime.combine(
                end, datetime_time.min, tzinfo=local_timezone
            ).astimezone(timezone.utc).isoformat())
        decoded = self.decode_cursor(cursor)
        if decoded:
            conditions.append("(occurred_at < ? OR (occurred_at = ? AND id < ?))")
            parameters.extend([decoded[0], decoded[0], decoded[1]])
        limit = min(100, max(1, int(limit)))
        sql = "SELECT * FROM erp_audit_events"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        parameters.append(limit + 1)
        with self.database.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        has_more = len(rows) > limit
        events = [self._deserialize(row) for row in rows[:limit]]
        next_cursor = self.encode_cursor(events[-1]) if has_more and events else ""
        return {"events": events, "next_cursor": next_cursor, "has_more": has_more}

    def get_event(self, event_id):
        self.database.initialize()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM erp_audit_events WHERE id = ?", (int(event_id),)
            ).fetchone()
        return self._deserialize(row) if row else None

    def filter_options(self):
        self.database.initialize()
        with self.database.connect() as connection:
            actors = [row[0] for row in connection.execute(
                "SELECT DISTINCT name FROM ("
                "SELECT actor_display_name_snapshot AS name FROM erp_audit_events UNION ALL "
                "SELECT started_by FROM erp_inventory_sessions UNION ALL "
                "SELECT completed_by FROM erp_inventory_sessions UNION ALL "
                "SELECT cancelled_by FROM erp_inventory_sessions UNION ALL "
                "SELECT confirmed_by FROM erp_inventory_items) "
                "WHERE COALESCE(name,'') <> '' ORDER BY name"
            ).fetchall()]
            actions = [row[0] for row in connection.execute(
                "SELECT DISTINCT action FROM erp_audit_events ORDER BY action"
            ).fetchall()]
        return {"actors": actors, "actions": actions}

    @staticmethod
    def encode_cursor(event):
        raw = json.dumps([event["occurred_at"], event["id"]], separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @staticmethod
    def decode_cursor(value):
        try:
            raw = str(value or "")
            raw += "=" * (-len(raw) % 4)
            timestamp, event_id = json.loads(base64.urlsafe_b64decode(raw).decode())
            return str(timestamp), int(event_id)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _deserialize(row):
        event = dict(row)
        for key in ("changes_json", "metadata_json"):
            try:
                event[key[:-5]] = json.loads(event.pop(key) or "{}")
            except (TypeError, json.JSONDecodeError):
                event[key[:-5]] = {}
        return event
