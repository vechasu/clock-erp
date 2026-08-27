"""Read models and additive review workflow for completed inventories."""

import json
import math
from datetime import datetime, timedelta, timezone

from app.catalog_db import CatalogDatabase
from app.services.brand_inventory import InventoryConflict, InventoryError, utc_now


REASONS = {
    "sale_missing": "Продажа не проведена",
    "receipt_missing": "Приход не внесён",
    "previous_stock_error": "Ошибка предыдущего остатка",
    "found_elsewhere": "Товар найден в другом месте или категории",
    "damage": "Повреждение или брак",
    "loss": "Потеря",
    "product_card_error": "Ошибка карточки товара",
    "count_error": "Ошибка подсчёта",
    "recount_required": "Требуется повторный пересчёт",
    "other": "Другая причина",
}
DECISIONS = {
    "recount": "Повторно пересчитать",
    "confirm_actual": "Подтвердить фактическое количество",
    "keep_stock": "Оставить остаток без изменения",
    "adjust": "Выполнить корректировку",
    "create_task": "Создать задачу",
    "resolved": "Отметить как разобранное",
}
REVIEW_STATUSES = {
    "new": "Новое",
    "recount": "Требуется пересчёт",
    "investigating": "На разборе",
    "awaiting_confirmation": "Ожидает подтверждения",
    "resolved": "Разобрано",
}


def inventory_accuracy(total_positions, discrepancy_positions):
    total = int(total_positions or 0)
    discrepancies = int(discrepancy_positions or 0)
    if total <= 0:
        return 100.0
    return round(max(0.0, 100.0 * (total - discrepancies) / total), 1)


def _duration(started_at, completed_at):
    if not started_at or not completed_at:
        return None
    try:
        start = datetime.strptime(str(started_at)[:19], "%Y-%m-%dT%H:%M:%S")
        finish = datetime.strptime(str(completed_at)[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    seconds = max(0, int((finish - start).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return "{} ч {} мин".format(hours, minutes) if hours else "{} мин".format(minutes)


def _document_status(row):
    if row.get("status") == "active":
        return "active", "В процессе"
    if row.get("status") == "cancelled":
        return "cancelled", "Отменена"
    discrepancies = int(row.get("discrepancy_positions") or 0)
    if not discrepancies:
        return "clean", "Без расхождений"
    unresolved = int(row.get("unresolved_positions") or 0)
    if not unresolved:
        return "resolved", "Разобрана"
    if int(row.get("in_review_positions") or 0):
        return "investigating", "На разборе"
    return "needs_review", "Требует разбора"


class InventoryControl:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    def initialize(self):
        self.database.initialize()

    @staticmethod
    def _base_select():
        return (
            "SELECT s.*,n.document_number,b.name brand_name,c.name category_name,mo.name model_name,"
            "COUNT(i.id) total_positions,"
            "COALESCE(SUM(CASE WHEN i.status IN ('confirmed','adjusted','added','missing') THEN 1 ELSE 0 END),0) checked_positions,"
            "COALESCE(SUM(CASE WHEN COALESCE(i.quantity_delta,0)<>0 THEN 1 ELSE 0 END),0) discrepancy_positions,"
            "COALESCE(SUM(CASE WHEN i.quantity_delta>0 THEN i.quantity_delta ELSE 0 END),0) surplus,"
            "COALESCE(SUM(CASE WHEN i.quantity_delta<0 THEN -i.quantity_delta ELSE 0 END),0) shortage,"
            "COALESCE(SUM(CASE WHEN COALESCE(i.quantity_delta,0)<>0 AND COALESCE(r.review_status,'new')<>'resolved' THEN 1 ELSE 0 END),0) unresolved_positions,"
            "COALESCE(SUM(CASE WHEN r.review_status IN ('investigating','recount','awaiting_confirmation') THEN 1 ELSE 0 END),0) in_review_positions "
            "FROM erp_inventory_sessions s JOIN erp_brands b ON b.id=s.brand_id "
            "JOIN erp_inventory_document_numbers n ON n.session_id=s.id "
            "LEFT JOIN erp_categories c ON c.id=s.category_id LEFT JOIN erp_models mo ON mo.id=s.model_id "
            "LEFT JOIN erp_inventory_items i ON i.session_id=s.id "
            "LEFT JOIN erp_inventory_reviews r ON r.item_id=i.id "
        )

    @staticmethod
    def _prepare_document(row):
        item = dict(row)
        for key in ("total_positions", "checked_positions", "discrepancy_positions",
                    "surplus", "shortage", "unresolved_positions", "in_review_positions"):
            item[key] = int(item.get(key) or 0)
        parts = [item.get("scope_brand_name") or item.get("brand_name") or "—"]
        category = item.get("scope_category_name") or item.get("category_name")
        model = item.get("scope_model_name") or item.get("model_name")
        if category:
            parts.append(category)
        if model:
            parts.append(model)
        item["scope_label"] = " → ".join(parts)
        item["employee"] = item.get("completed_by") or item.get("cancelled_by") or item.get("started_by") or ""
        item["accuracy"] = inventory_accuracy(item["total_positions"], item["discrepancy_positions"])
        item["duration"] = _duration(item.get("started_at"), item.get("completed_at"))
        item["control_status"], item["control_status_label"] = _document_status(item)
        return item

    @staticmethod
    def _filters(filters, alias="s"):
        filters = filters or {}
        where, params = [], []
        mapping = (("brand_id", "brand_id"), ("category_id", "category_id"), ("model_id", "model_id"))
        for key, column in mapping:
            value = str(filters.get(key) or "").strip()
            if value:
                where.append("{}.{}=?".format(alias, column))
                params.append(value)
        date_from, date_to = str(filters.get("date_from") or "").strip(), str(filters.get("date_to") or "").strip()
        if date_from:
            where.append("substr({}.started_at,1,10)>=?".format(alias)); params.append(date_from)
        if date_to:
            where.append("substr({}.started_at,1,10)<=?".format(alias)); params.append(date_to)
        return where, params

    def history(self, filters=None, page=1, per_page=50):
        self.initialize()
        filters = filters or {}
        where, params = self._filters(filters)
        query = str(filters.get("q") or "").strip()
        if query:
            where.append("(n.document_number LIKE ? OR s.id LIKE ? OR COALESCE(s.scope_brand_name,b.name) LIKE ? OR COALESCE(s.started_by,'') LIKE ? OR COALESCE(s.completed_by,'') LIKE ?)")
            params.extend(["%{}%".format(query)] * 5)
        employee = str(filters.get("employee") or "").strip()
        if employee:
            where.append("COALESCE(s.completed_by,s.cancelled_by,s.started_by,'')=?"); params.append(employee)
        raw_status = str(filters.get("status") or "").strip()
        if raw_status in ("active", "completed", "cancelled"):
            where.append("s.status=?"); params.append(raw_status)
        if str(filters.get("discrepancies") or "") == "1":
            where.append("EXISTS(SELECT 1 FROM erp_inventory_items di WHERE di.session_id=s.id AND COALESCE(di.quantity_delta,0)<>0)")
        clause = " WHERE " + " AND ".join(where) if where else ""
        count_sql = "SELECT COUNT(*) FROM erp_inventory_sessions s JOIN erp_brands b ON b.id=s.brand_id JOIN erp_inventory_document_numbers n ON n.session_id=s.id" + clause
        page, per_page = max(1, int(page or 1)), max(1, min(100, int(per_page or 50)))
        with self.database.connect() as connection:
            total = int(connection.execute(count_sql, params).fetchone()[0])
            page = min(page, max(1, int(math.ceil(float(total) / per_page))))
            rows = connection.execute(
                self._base_select() + clause + " GROUP BY s.id ORDER BY s.started_at DESC,s.id DESC LIMIT ? OFFSET ?",
                params + [per_page, (page - 1) * per_page],
            ).fetchall()
        return {"rows": [self._prepare_document(row) for row in rows], "total": total,
                "page": page, "per_page": per_page, "pages": max(1, int(math.ceil(float(total) / per_page)))}

    def document(self, session_id):
        self.initialize()
        with self.database.connect() as connection:
            row = connection.execute(self._base_select() + " WHERE s.id=? GROUP BY s.id", (str(session_id),)).fetchone()
        if row is None:
            raise InventoryError("Инвентаризация не найдена.")
        return self._prepare_document(row)

    def document_items(self, session_id, item_filter="all"):
        self.initialize()
        where = ["i.session_id=?"]
        params = [str(session_id)]
        if item_filter == "discrepancies": where.append("COALESCE(i.quantity_delta,0)<>0")
        elif item_filter == "shortage": where.append("i.quantity_delta<0")
        elif item_filter == "surplus": where.append("i.quantity_delta>0")
        elif item_filter == "unresolved": where.append("COALESCE(r.review_status,'new')<>'resolved' AND COALESCE(i.quantity_delta,0)<>0")
        elif item_filter == "resolved": where.append("r.review_status='resolved'")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT i.*,COALESCE(i.snapshot_name,p.excel_name_raw) name,COALESCE(i.snapshot_article,p.excel_article) article,"
                "COALESCE(i.snapshot_brand_name,p.excel_brand) brand_name,COALESCE(i.snapshot_category_name,p.excel_category) category_name,"
                "COALESCE(i.snapshot_model_name,p.model) model_name,COALESCE(i.snapshot_photo_url,p.bitrix_thumbnail_url,p.bitrix_primary_image_url) photo_url,"
                "COALESCE(r.review_status,'new') review_status,r.reason_code,r.reason_comment,r.decision_code,r.assignee_user_id,r.assignee_name,r.task_id,r.adjustment_movement_id "
                "FROM erp_inventory_items i JOIN catalog_excel_products p ON p.id=i.product_id LEFT JOIN erp_inventory_reviews r ON r.item_id=i.id "
                "WHERE " + " AND ".join(where) + " ORDER BY (COALESCE(i.quantity_delta,0)=0),name COLLATE NOCASE,i.id", params
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row); delta = int(item.get("quantity_delta") or 0)
            item["result"] = "surplus" if delta > 0 else "shortage" if delta < 0 else "match"
            item["reason_label"] = REASONS.get(item.get("reason_code"), "—")
            item["decision_label"] = DECISIONS.get(item.get("decision_code"), "—")
            item["review_status_label"] = REVIEW_STATUSES.get(item.get("review_status"), "—")
            result.append(item)
        return result

    def discrepancies(self, filters=None, page=1, per_page=50):
        self.initialize(); filters = filters or {}; where, params = self._filters(filters)
        where.extend(["s.status='completed'", "COALESCE(i.quantity_delta,0)<>0"])
        query = str(filters.get("q") or "").strip()
        if query:
            where.append("(n.document_number LIKE ? OR COALESCE(i.snapshot_name,p.excel_name_raw) LIKE ? OR COALESCE(i.snapshot_article,p.excel_article,'') LIKE ?)")
            params.extend(["%{}%".format(query)] * 3)
        direction = str(filters.get("direction") or "")
        if direction == "shortage": where.append("i.quantity_delta<0")
        elif direction == "surplus": where.append("i.quantity_delta>0")
        reason, status = str(filters.get("reason") or ""), str(filters.get("review_status") or "")
        if reason: where.append("r.reason_code=?"); params.append(reason)
        if status: where.append("COALESCE(r.review_status,'new')=?"); params.append(status)
        employee = str(filters.get("employee") or "").strip()
        if employee:
            where.append("COALESCE(s.completed_by,s.started_by,'')=?"); params.append(employee)
        clause = " WHERE " + " AND ".join(where)
        joins = " FROM erp_inventory_items i JOIN erp_inventory_sessions s ON s.id=i.session_id JOIN erp_inventory_document_numbers n ON n.session_id=s.id JOIN catalog_excel_products p ON p.id=i.product_id LEFT JOIN erp_inventory_reviews r ON r.item_id=i.id "
        page, per_page = max(1, int(page or 1)), max(1, min(100, int(per_page or 50)))
        with self.database.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*)" + joins + clause, params).fetchone()[0])
            page = min(page, max(1, int(math.ceil(float(total) / per_page))))
            rows = connection.execute(
                "SELECT i.id item_id,i.session_id,i.product_id,n.document_number,s.completed_at,COALESCE(i.snapshot_name,p.excel_name_raw) name,"
                "COALESCE(i.snapshot_article,p.excel_article) article,COALESCE(i.snapshot_brand_name,p.excel_brand) brand_name,"
                "COALESCE(i.snapshot_category_name,p.excel_category) category_name,COALESCE(i.snapshot_model_name,p.model) model_name,"
                "i.snapshot_stock,i.actual_stock,i.quantity_delta,COALESCE(r.review_status,'new') review_status,r.reason_code,r.reason_comment,r.decision_code,r.assignee_user_id,r.assignee_name,r.task_id "
                + joins + clause + " ORDER BY (COALESCE(r.review_status,'new')='resolved'),s.completed_at DESC,i.id LIMIT ? OFFSET ?",
                params + [per_page, (page - 1) * per_page],
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["reason_label"] = REASONS.get(item.get("reason_code"), "—"); item["review_status_label"] = REVIEW_STATUSES.get(item["review_status"], "—"); result.append(item)
        return {"rows": result, "total": total, "page": page, "per_page": per_page, "pages": max(1, int(math.ceil(float(total) / per_page)))}

    def discrepancy(self, item_id):
        self.initialize()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT i.id item_id,i.session_id,i.product_id,n.document_number,"
                "COALESCE(i.snapshot_name,p.excel_name_raw) name,COALESCE(i.snapshot_article,p.excel_article) article,"
                "i.quantity_delta,r.task_id FROM erp_inventory_items i "
                "JOIN erp_inventory_sessions s ON s.id=i.session_id "
                "JOIN erp_inventory_document_numbers n ON n.session_id=s.id "
                "JOIN catalog_excel_products p ON p.id=i.product_id "
                "LEFT JOIN erp_inventory_reviews r ON r.item_id=i.id "
                "WHERE i.id=? AND s.status='completed' AND COALESCE(i.quantity_delta,0)<>0",
                (str(item_id),),
            ).fetchone()
        return dict(row) if row else None

    def update_review(self, item_id, payload, actor_id="", actor_name=""):
        reason = str(payload.get("reason_code") or "").strip() or None
        comment = str(payload.get("reason_comment") or "").strip() or None
        decision = str(payload.get("decision_code") or "").strip() or None
        status = str(payload.get("review_status") or "new").strip()
        if reason and reason not in REASONS: raise InventoryError("Неизвестная причина расхождения.")
        if reason == "other" and not comment: raise InventoryError("Для другой причины обязателен комментарий.")
        if decision and decision not in DECISIONS: raise InventoryError("Неизвестное решение.")
        if status not in REVIEW_STATUSES: raise InventoryError("Неизвестный статус разбора.")
        assignee_id = payload.get("assignee_user_id") or None
        assignee_name = str(payload.get("assignee_name") or "").strip() or None
        now = utc_now(); self.initialize()
        with self.database.transaction() as connection:
            item = connection.execute("SELECT i.id FROM erp_inventory_items i JOIN erp_inventory_sessions s ON s.id=i.session_id WHERE i.id=? AND s.status='completed' AND COALESCE(i.quantity_delta,0)<>0", (str(item_id),)).fetchone()
            if item is None: raise InventoryError("Расхождение не найдено.")
            before = connection.execute("SELECT * FROM erp_inventory_reviews WHERE item_id=?", (str(item_id),)).fetchone()
            connection.execute(
                "INSERT OR IGNORE INTO erp_inventory_reviews(item_id,created_at,updated_at) VALUES(?,?,?)", (str(item_id), now, now)
            )
            connection.execute(
                "UPDATE erp_inventory_reviews SET review_status=?,reason_code=?,reason_comment=?,decision_code=?,assignee_user_id=?,assignee_name=?,updated_at=?,updated_by=? WHERE item_id=?",
                (status, reason, comment, decision, assignee_id, assignee_name, now, actor_name or actor_id or None, str(item_id)),
            )
            connection.execute(
                "INSERT INTO erp_inventory_review_events(item_id,action,actor_id,actor_name,details_json,created_at) VALUES(?,?,?,?,?,?)",
                (str(item_id), "review_updated", str(actor_id or "") or None, actor_name or None,
                 json.dumps({"before": dict(before) if before else None, "after": {"status": status, "reason": reason, "decision": decision}}, ensure_ascii=False, sort_keys=True), now),
            )
            row = connection.execute("SELECT * FROM erp_inventory_reviews WHERE item_id=?", (str(item_id),)).fetchone()
        return dict(row)

    def link_task(self, item_id, task_id, actor_id="", actor_name=""):
        self.initialize(); now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT task_id FROM erp_inventory_reviews WHERE item_id=?", (str(item_id),)).fetchone()
            if row is None: raise InventoryError("Расхождение не найдено.")
            if row["task_id"] is not None: return int(row["task_id"]), False
            connection.execute("UPDATE erp_inventory_reviews SET task_id=?,decision_code='create_task',updated_at=?,updated_by=? WHERE item_id=? AND task_id IS NULL", (int(task_id), now, actor_name or None, str(item_id)))
            connection.execute("INSERT INTO erp_inventory_review_events(item_id,action,actor_id,actor_name,details_json,created_at) VALUES(?,?,?,?,?,?)", (str(item_id), "task_created", str(actor_id or "") or None, actor_name or None, json.dumps({"task_id": int(task_id)}), now))
        return int(task_id), True

    def events(self, session_id):
        self.initialize()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT e.* FROM erp_inventory_review_events e JOIN erp_inventory_items i ON i.id=e.item_id WHERE i.session_id=? ORDER BY e.created_at DESC,e.id DESC", (str(session_id),)
            ).fetchall()
        return [dict(row) for row in rows]

    def kpis(self, date_from="", date_to=""):
        self.initialize(); where, params = [], []
        if date_from: where.append("substr(s.completed_at,1,10)>=?"); params.append(date_from)
        if date_to: where.append("substr(s.completed_at,1,10)<=?"); params.append(date_to)
        clause = " AND " + " AND ".join(where) if where else ""
        seven_days = (datetime.now(timezone.utc).date() - timedelta(days=6)).isoformat()
        with self.database.connect() as connection:
            active = int(connection.execute("SELECT COUNT(*) FROM erp_inventory_sessions WHERE status='active'").fetchone()[0])
            row = connection.execute(
                "SELECT COUNT(i.id) total,COALESCE(SUM(CASE WHEN COALESCE(i.quantity_delta,0)<>0 THEN 1 ELSE 0 END),0) differences "
                "FROM erp_inventory_sessions s LEFT JOIN erp_inventory_items i ON i.session_id=s.id WHERE s.status='completed'" + clause, params
            ).fetchone()
            week = connection.execute(
                "SELECT COALESCE(SUM(CASE WHEN i.quantity_delta<0 THEN -i.quantity_delta ELSE 0 END),0),COALESCE(SUM(CASE WHEN i.quantity_delta>0 THEN i.quantity_delta ELSE 0 END),0) FROM erp_inventory_sessions s JOIN erp_inventory_items i ON i.session_id=s.id WHERE s.status='completed' AND substr(s.completed_at,1,10)>=?", (seven_days,)
            ).fetchone()
            unresolved = int(connection.execute("SELECT COUNT(*) FROM erp_inventory_reviews WHERE review_status<>'resolved'").fetchone()[0])
        brands = self.brand_summary()
        due = sum(item["control_status"] in {"due", "overdue"} for item in brands)
        overdue = sum(item["control_status"] == "overdue" for item in brands)
        return {"active": active, "unresolved": unresolved,
                "shortage_7d": int(week[0]), "surplus_7d": int(week[1]),
                "accuracy": inventory_accuracy(row[0], row[1]),
                "brands_due": due, "overdue_brands": overdue}

    def brand_summary(self):
        self.initialize(); today = datetime.now(timezone.utc).date()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT b.id,b.name brand_name,COALESCE(SUM(CASE WHEN p.active=1 AND p.stock>0 THEN 1 ELSE 0 END),0) in_stock,"
                "bc.enabled,COALESCE(bc.interval_days,90) interval_days,bc.assignee_user_id,bc.assignee_name,"
                "EXISTS(SELECT 1 FROM erp_inventory_sessions ax WHERE ax.brand_id=b.id AND ax.status='active') has_active "
                "FROM erp_brands b LEFT JOIN catalog_excel_products p ON p.brand_id=b.id LEFT JOIN erp_inventory_brand_controls bc ON bc.brand_id=b.id WHERE b.active=1 GROUP BY b.id ORDER BY b.name COLLATE NOCASE"
            ).fetchall()
            completed = connection.execute(self._base_select() + " WHERE s.status='completed' GROUP BY s.id ORDER BY s.completed_at DESC,s.id DESC").fetchall()
        latest = {}
        for row in completed:
            item = self._prepare_document(row); latest.setdefault(int(item["brand_id"]), item)
        result = []
        for raw in rows:
            item = dict(raw); last = latest.get(int(item["id"])); days = None
            if last and last.get("completed_at"):
                try: days = (today - datetime.strptime(last["completed_at"][:10], "%Y-%m-%d").date()).days
                except ValueError: pass
            interval = int(item.get("interval_days") or 90); next_date = None
            if last and last.get("completed_at"):
                next_date = (datetime.strptime(last["completed_at"][:10], "%Y-%m-%d").date() + timedelta(days=interval)).isoformat()
            unresolved = int(last.get("unresolved_positions") or 0) if last else 0
            if unresolved: status = "differences"; label = "Есть расхождения"
            elif last is None: status = "never"; label = "Никогда не проверялся"
            elif days > interval: status = "overdue"; label = "Просрочено"
            elif days == interval: status = "due"; label = "Пора проверить"
            elif days >= max(0, interval - 7): status = "soon"; label = "Скоро проверка"
            else: status = "ok"; label = "В порядке"
            item.update({"latest": last, "days_since": days, "next_date": next_date, "unresolved": unresolved, "control_status": status, "control_status_label": label})
            result.append(item)
        priority = {"overdue": 0, "differences": 1, "due": 2, "soon": 3, "never": 4, "ok": 5}
        return sorted(result, key=lambda item: (priority[item["control_status"]], str(item["brand_name"]).casefold()))

    def update_brand_control(self, brand_id, payload, actor=""):
        try: interval = int(payload.get("interval_days") or 90)
        except (TypeError, ValueError): raise InventoryError("Периодичность должна быть числом дней.")
        if interval < 1 or interval > 3650: raise InventoryError("Периодичность должна быть от 1 до 3650 дней.")
        enabled = 1 if str(payload.get("enabled", "1")).lower() in ("1", "true", "on", "yes") else 0
        assignee_id = payload.get("assignee_user_id") or None; assignee_name = str(payload.get("assignee_name") or "").strip() or None
        self.initialize()
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM erp_brands WHERE id=?", (int(brand_id),)).fetchone() is None: raise InventoryError("Бренд не найден.")
            connection.execute("INSERT OR IGNORE INTO erp_inventory_brand_controls(brand_id,enabled,interval_days,updated_at) VALUES(?,?,?,?)", (int(brand_id), enabled, interval, utc_now()))
            connection.execute("UPDATE erp_inventory_brand_controls SET enabled=?,interval_days=?,assignee_user_id=?,assignee_name=?,updated_at=?,updated_by=? WHERE brand_id=?", (enabled, interval, assignee_id, assignee_name, utc_now(), actor or None, int(brand_id)))
        return True

    def analytics(self, date_from, date_to):
        listing = self.history({"date_from": date_from, "date_to": date_to, "status": "completed"}, 1, 100)
        rows = listing["rows"]
        self.initialize()
        with self.database.connect() as connection:
            totals = connection.execute(
                "SELECT COUNT(DISTINCT s.id),COUNT(i.id),"
                "COALESCE(SUM(CASE WHEN COALESCE(i.quantity_delta,0)<>0 THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN i.quantity_delta<0 THEN -i.quantity_delta ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN i.quantity_delta>0 THEN i.quantity_delta ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN COALESCE(i.quantity_delta,0)<>0 AND COALESCE(r.review_status,'new')<>'resolved' THEN 1 ELSE 0 END),0) "
                "FROM erp_inventory_sessions s LEFT JOIN erp_inventory_items i ON i.session_id=s.id "
                "LEFT JOIN erp_inventory_reviews r ON r.item_id=i.id "
                "WHERE s.status='completed' AND substr(s.completed_at,1,10)>=? AND substr(s.completed_at,1,10)<=?",
                (date_from, date_to),
            ).fetchone()
            duration = connection.execute(
                "SELECT COALESCE(AVG((julianday(completed_at)-julianday(started_at))*1440),0) "
                "FROM erp_inventory_sessions WHERE status='completed' "
                "AND substr(completed_at,1,10)>=? AND substr(completed_at,1,10)<=?",
                (date_from, date_to),
            ).fetchone()[0]
            period_where = (
                " WHERE s.status='completed' AND substr(s.completed_at,1,10)>=? "
                "AND substr(s.completed_at,1,10)<=? "
            )
            common = (
                " FROM erp_inventory_sessions s JOIN erp_inventory_items i ON i.session_id=s.id "
                "LEFT JOIN erp_inventory_reviews r ON r.item_id=i.id " + period_where
            )
            daily = connection.execute(
                "SELECT substr(s.completed_at,1,10) label,COUNT(i.id) positions,"
                "SUM(CASE WHEN COALESCE(i.quantity_delta,0)<>0 THEN 1 ELSE 0 END) differences,"
                "SUM(CASE WHEN i.quantity_delta<0 THEN -i.quantity_delta ELSE 0 END) shortage,"
                "SUM(CASE WHEN i.quantity_delta>0 THEN i.quantity_delta ELSE 0 END) surplus" +
                common + "GROUP BY label ORDER BY label", (date_from, date_to),
            ).fetchall()
            worst_brands = connection.execute(
                "SELECT COALESCE(i.snapshot_brand_name,b.name) label,COUNT(i.id) positions,"
                "SUM(CASE WHEN COALESCE(i.quantity_delta,0)<>0 THEN 1 ELSE 0 END) differences" +
                " FROM erp_inventory_sessions s JOIN erp_inventory_items i ON i.session_id=s.id "
                "JOIN erp_brands b ON b.id=s.brand_id " + period_where + "GROUP BY label "
                "HAVING COUNT(i.id)>0 ORDER BY (1.0*differences/positions) DESC,label LIMIT 10",
                (date_from, date_to),
            ).fetchall()
            reasons = connection.execute(
                "SELECT COALESCE(r.reason_code,'unclassified') label,COUNT(*) total" + common +
                "AND COALESCE(i.quantity_delta,0)<>0 GROUP BY label ORDER BY total DESC LIMIT 10",
                (date_from, date_to),
            ).fetchall()
            products = connection.execute(
                "SELECT COALESCE(i.snapshot_name,p.excel_name_raw) label,COUNT(*) total,"
                "SUM(ABS(i.quantity_delta)) units FROM erp_inventory_sessions s "
                "JOIN erp_inventory_items i ON i.session_id=s.id "
                "JOIN catalog_excel_products p ON p.id=i.product_id " + period_where +
                "AND COALESCE(i.quantity_delta,0)<>0 GROUP BY i.product_id,label "
                "ORDER BY units DESC,total DESC LIMIT 10", (date_from, date_to),
            ).fetchall()
            employees = connection.execute(
                "SELECT COALESCE(completed_by,started_by,'—') label,COUNT(*) total "
                "FROM erp_inventory_sessions WHERE status='completed' "
                "AND substr(completed_at,1,10)>=? AND substr(completed_at,1,10)<=? "
                "GROUP BY label ORDER BY total DESC,label", (date_from, date_to),
            ).fetchall()
        daily_rows = []
        for row in daily:
            item = dict(row)
            item["accuracy"] = inventory_accuracy(item["positions"], item["differences"])
            daily_rows.append(item)
        brand_rows = []
        for row in worst_brands:
            item = dict(row)
            item["accuracy"] = inventory_accuracy(item["positions"], item["differences"])
            brand_rows.append(item)
        return {
            "documents": int(totals[0] or 0), "positions": int(totals[1] or 0),
            "accuracy": inventory_accuracy(totals[1], totals[2]),
            "shortage": int(totals[3] or 0), "surplus": int(totals[4] or 0),
            "unresolved": int(totals[5] or 0),
            "average_duration_minutes": int(round(float(duration or 0))),
            "rows": rows, "daily": daily_rows, "worst_brands": brand_rows,
            "reasons": [{**dict(row), "label": REASONS.get(row["label"], "Без причины")} for row in reasons],
            "products": [dict(row) for row in products],
            "employees": [dict(row) for row in employees],
        }

    def adjust_once(self, item_id, target_stock, actor="", reason=""):
        """Refuse a second adjustment: completion is the stock write authority."""
        del target_stock, actor, reason
        self.initialize()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT i.movement_id FROM erp_inventory_items i "
                "JOIN erp_inventory_sessions s ON s.id=i.session_id "
                "WHERE i.id=? AND s.status='completed' AND COALESCE(i.quantity_delta,0)<>0",
                (str(item_id),),
            ).fetchone()
        if row is None:
            raise InventoryError("Расхождение не найдено.")
        raise InventoryConflict(
            "Корректировка по этому расхождению уже применена при завершении инвентаризации."
        )
