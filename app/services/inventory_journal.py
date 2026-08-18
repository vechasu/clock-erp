"""Read-only projection of inventory documents for the shared ERP journal."""

from app.catalog_db import CatalogDatabase


STATUS_LABELS = {
    "active": "Активна",
    "completed": "Завершена",
    "cancelled": "Отменена",
}
RESULT_LABELS = {
    "pending": "Не проверен",
    "confirmed": "Подтверждён",
    "conflict": "Конфликт",
    "error": "Ошибка",
}


class InventoryJournal:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    def enrich_events(self, events):
        inventory_events = [event for event in events if event["entity_type"] == "inventory"]
        if not inventory_events:
            return events
        self.database.initialize()
        identifiers = [event["entity_id"] for event in inventory_events]
        placeholders = ",".join("?" for _ in identifiers)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT s.*, b.name AS brand_name, COUNT(i.id) AS total_positions, "
                "SUM(CASE WHEN i.status IN ('confirmed','adjusted','added','missing') THEN 1 ELSE 0 END) AS calculated_checked_positions, "
                "SUM(CASE WHEN i.status = 'confirmed' THEN 1 ELSE 0 END) AS unchanged_positions, "
                "SUM(CASE WHEN i.status = 'conflict' THEN 1 ELSE 0 END) AS conflict_positions, "
                "SUM(CASE WHEN i.status = 'error' THEN 1 ELSE 0 END) AS error_positions, "
                "SUM(CASE WHEN i.status = 'pending' THEN 1 ELSE 0 END) AS pending_positions, "
                "SUM(CASE WHEN i.quantity_delta > 0 THEN 1 ELSE 0 END) AS increased_positions, "
                "SUM(CASE WHEN i.quantity_delta < 0 THEN 1 ELSE 0 END) AS decreased_positions, "
                "COALESCE(SUM(CASE WHEN i.quantity_delta > 0 THEN i.quantity_delta ELSE 0 END),0) AS positive_delta, "
                "COALESCE(SUM(CASE WHEN i.quantity_delta < 0 THEN i.quantity_delta ELSE 0 END),0) AS negative_delta, "
                "SUM(CASE WHEN i.reactivated = 1 THEN 1 ELSE 0 END) AS reactivated_positions "
                "FROM erp_inventory_sessions s JOIN erp_brands b ON b.id = s.brand_id "
                "LEFT JOIN erp_inventory_items i ON i.session_id = s.id "
                "WHERE s.id IN ({}) GROUP BY s.id".format(placeholders),
                identifiers,
            ).fetchall()
        summaries = {row["id"]: self._summary(dict(row)) for row in rows}
        for event in inventory_events:
            summary = summaries.get(event["entity_id"])
            if summary:
                event["inventory_summary"] = summary
        return events

    def get_document(self, session_id):
        self.database.initialize()
        with self.database.connect() as connection:
            event_rows = connection.execute(
                "SELECT s.*, b.name AS brand_name, COUNT(i.id) AS total_positions, "
                "SUM(CASE WHEN i.status IN ('confirmed','adjusted','added','missing') THEN 1 ELSE 0 END) AS calculated_checked_positions, "
                "SUM(CASE WHEN i.status = 'confirmed' THEN 1 ELSE 0 END) AS unchanged_positions, "
                "SUM(CASE WHEN i.status = 'conflict' THEN 1 ELSE 0 END) AS conflict_positions, "
                "SUM(CASE WHEN i.status = 'error' THEN 1 ELSE 0 END) AS error_positions, "
                "SUM(CASE WHEN i.status = 'pending' THEN 1 ELSE 0 END) AS pending_positions, "
                "SUM(CASE WHEN i.quantity_delta > 0 THEN 1 ELSE 0 END) AS increased_positions, "
                "SUM(CASE WHEN i.quantity_delta < 0 THEN 1 ELSE 0 END) AS decreased_positions, "
                "COALESCE(SUM(CASE WHEN i.quantity_delta > 0 THEN i.quantity_delta ELSE 0 END),0) AS positive_delta, "
                "COALESCE(SUM(CASE WHEN i.quantity_delta < 0 THEN i.quantity_delta ELSE 0 END),0) AS negative_delta, "
                "SUM(CASE WHEN i.reactivated = 1 THEN 1 ELSE 0 END) AS reactivated_positions "
                "FROM erp_inventory_sessions s JOIN erp_brands b ON b.id = s.brand_id "
                "LEFT JOIN erp_inventory_items i ON i.session_id = s.id "
                "WHERE s.id = ? GROUP BY s.id", (str(session_id),),
            ).fetchone()
            if event_rows is None:
                return None
            positions = connection.execute(
                "SELECT i.*, p.excel_name_raw AS name, p.excel_article AS article, "
                "p.bitrix_thumbnail_url AS photo_url, m.id AS linked_movement_id "
                "FROM erp_inventory_items i JOIN catalog_excel_products p ON p.id = i.product_id "
                "LEFT JOIN catalog_stock_movements m ON m.id = i.movement_id "
                "AND m.source_type = 'inventory' AND m.source_id = i.session_id "
                "AND m.source_line_id = i.id WHERE i.session_id = ? "
                "ORDER BY COALESCE(i.confirmed_at, i.snapshot_at), p.excel_name_raw COLLATE NOCASE",
                (str(session_id),),
            ).fetchall()
        summary = self._summary(dict(event_rows))
        return {
            "summary": summary,
            "positions": [self._position(dict(row)) for row in positions],
        }

    @staticmethod
    def _summary(row):
        numeric = (
            "start_positions", "checked_positions", "adjusted_positions", "added_positions",
            "missing_positions", "total_delta", "total_positions", "unchanged_positions",
            "conflict_positions", "error_positions", "pending_positions", "increased_positions",
            "decreased_positions", "positive_delta", "negative_delta", "reactivated_positions",
            "calculated_checked_positions",
        )
        for key in numeric:
            row[key] = int(row.get(key) or 0)
        row["status_label"] = STATUS_LABELS.get(row.get("status"), row.get("status", ""))
        row["checked_positions"] = row["calculated_checked_positions"]
        return row

    @staticmethod
    def _position(row):
        for key in ("snapshot_stock", "actual_stock", "final_stock", "quantity_delta"):
            if row.get(key) is not None:
                row[key] = int(row[key])
        if row.get("status") == "added":
            result = "Реактивирован" if row.get("reactivated") else "Добавлен"
        elif row.get("status") == "missing":
            result = "Не найден"
        elif row.get("status") == "adjusted":
            result = "Излишек" if int(row.get("quantity_delta") or 0) > 0 else "Недостача"
        else:
            result = RESULT_LABELS.get(row.get("status"), row.get("status", ""))
        return {
            "id": row["id"], "product_id": row["product_id"], "name": row["name"],
            "article": row.get("article") or "", "photo_url": row.get("photo_url") or "",
            "stock_before": row["snapshot_stock"], "actual_stock": row.get("actual_stock"),
            "stock_after": row.get("final_stock"), "delta": row.get("quantity_delta"),
            "result": result, "status": row["status"], "user": row.get("confirmed_by") or "",
            "timestamp": row.get("confirmed_at") or row.get("snapshot_at"),
            "movement_id": row.get("linked_movement_id"), "error": row.get("error_message") or "",
            "action_type": (
                "inventory_item_confirmed" if row.get("confirmed_at") else ""
            ),
        }
