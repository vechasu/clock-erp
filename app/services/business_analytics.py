"""Read-only business analytics over the canonical local ERP catalog database.

Metric contract:
* a sale is one distinct, performed ``erp_sales`` document;
* business date is ``erp_sales.created_at`` in the ERP (+03:00) timezone;
* revenue is the final unit price multiplied by non-returned units;
* units are the non-returned quantities of all sale lines;
* cancelled, deleted and archived documents are excluded;
* orders are never counted as sales and Bitrix is never queried at request time.
"""

from __future__ import print_function

from datetime import date, datetime, timedelta

from app.catalog_db import CatalogDatabase


ERP_TIMEZONE_LABEL = "Europe/Moscow (UTC+03:00)"
LOW_STOCK_DAYS = 14
MIN_FORECAST_DAYS = 5
MIN_DECLINE_SALES = 5

SECTIONS = (
    ("summary", "Сводка"),
    ("sales", "Продажи"),
    ("products", "Товары"),
    ("channels", "Каналы"),
    ("orders", "Заказы"),
    ("customers", "Клиенты"),
    ("stock", "Остатки и закупки"),
    ("inventory", "Инвентаризации"),
    ("repairs", "Ремонты"),
    ("profit", "Прибыль"),
)

SOURCE_LABELS = {
    "tictactoy": "Tictactoy",
    "wildberries": "Wildberries",
    "amazon": "Amazon",
    "ziiiro": "Ziro",
    "ziro": "Ziro",
    "manual": "Ручные продажи",
}


def _date(value, fallback):
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


def _money(value):
    return "{:,.2f} ₽".format(float(value or 0)).replace(",", " ")


def _number(value):
    number = float(value or 0)
    return str(int(number)) if number.is_integer() else "{:,.2f}".format(number).replace(",", " ")


def _change(current, previous):
    if current is None or previous is None:
        return {
            "absolute": None, "absolute_display": "—", "percent": None,
            "percent_display": "—", "direction": "flat",
        }
    absolute = float(current or 0) - float(previous or 0)
    percent = None if not previous else absolute / float(previous) * 100
    return {
        "absolute": absolute,
        "absolute_display": _number(absolute),
        "percent": percent,
        "percent_display": "—" if percent is None else "{:+.1f}%".format(percent),
        "direction": "up" if absolute > 0 else "down" if absolute < 0 else "flat",
    }


def parse_filters(arguments, today=None):
    today = today or date.today()
    end_date = _date(arguments.get("to"), today)
    start_date = _date(arguments.get("from"), end_date - timedelta(days=29))
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    if (end_date - start_date).days > 730:
        start_date = end_date - timedelta(days=730)
    return {
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "days": (end_date - start_date).days + 1,
        "channel": str(arguments.get("channel") or "").strip().casefold(),
        "brand": str(arguments.get("brand") or "").strip(),
        "category": str(arguments.get("category") or "").strip(),
        "model": str(arguments.get("model") or "").strip(),
        "product": str(arguments.get("product") or "").strip(),
    }


class BusinessAnalytics(object):
    """Single query layer shared by HTML, drill-down fragments and CSV."""

    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    @staticmethod
    def _sale_where(filters, alias="s"):
        clauses = [
            "date({0}.created_at) BETWEEN ? AND ?".format(alias),
            "{0}.cancelled_at IS NULL".format(alias),
            "{0}.deleted_at IS NULL".format(alias),
            "{0}.archived_at IS NULL".format(alias),
        ]
        values = [filters["from"], filters["to"]]
        if filters["channel"]:
            clauses.append("lower({}.source) = ?".format(alias))
            values.append(filters["channel"])
        if filters["brand"]:
            clauses.append("coalesce(b.name,p.excel_brand,'') = ?")
            values.append(filters["brand"])
        if filters["category"]:
            clauses.append("coalesce(c.name,p.excel_category,'') = ?")
            values.append(filters["category"])
        if filters["model"]:
            clauses.append("coalesce(m.name,p.model,'') = ?")
            values.append(filters["model"])
        if filters["product"]:
            clauses.append("cast(p.id as text) = ?")
            values.append(filters["product"])
        return " AND ".join(clauses), values

    @staticmethod
    def _joins():
        return (
            " FROM erp_sales s JOIN erp_sale_items i ON i.sale_id=s.id "
            "JOIN catalog_excel_products p ON p.id=i.product_id "
            "LEFT JOIN erp_brands b ON b.id=coalesce(i.brand_id,p.brand_id) "
            "LEFT JOIN erp_categories c ON c.id=coalesce(i.category_id,p.category_id) "
            "LEFT JOIN erp_models m ON m.id=p.model_id "
        )

    def _metrics(self, connection, filters):
        where, values = self._sale_where(filters)
        row = connection.execute(
            "SELECT count(DISTINCT CASE WHEN i.quantity-i.returned_quantity>0 THEN s.id END) sales, "
            "coalesce(sum(max(i.quantity-i.returned_quantity,0)),0) units, "
            "coalesce(sum(max(i.quantity-i.returned_quantity,0)*coalesce(i.unit_price,0)),0) revenue, "
            "sum(CASE WHEN i.quantity-i.returned_quantity>0 AND i.unit_price IS NULL THEN 1 ELSE 0 END) unknown_prices, "
            "coalesce(sum(cast(i.discount_amount as real)*max(i.quantity-i.returned_quantity,0)),0) discounts "
            + self._joins() + " WHERE " + where,
            values,
        ).fetchone()
        sales = int(row["sales"] or 0)
        revenue = None if int(row["unknown_prices"] or 0) else float(row["revenue"] or 0)
        units = float(row["units"] or 0)
        return {
            "sales": sales,
            "units": units,
            "revenue": revenue,
            "average_order": revenue / sales if revenue is not None and sales else (0 if not sales else None),
            "average_unit": revenue / units if revenue is not None and units else (0 if not units else None),
            "discounts": float(row["discounts"] or 0),
        }

    @staticmethod
    def _comparison_filters(filters):
        start = _date(filters["from"], date.today())
        end = _date(filters["to"], start)
        days = (end - start).days + 1
        previous_end = start - timedelta(days=1)
        previous = dict(filters)
        previous["from"] = (previous_end - timedelta(days=days - 1)).isoformat()
        previous["to"] = previous_end.isoformat()
        return previous

    def _breakdown(self, connection, filters, expression, limit=30):
        where, values = self._sale_where(filters)
        rows = connection.execute(
            "SELECT " + expression + " label, count(DISTINCT CASE WHEN i.quantity-i.returned_quantity>0 THEN s.id END) sales, "
            "coalesce(sum(max(i.quantity-i.returned_quantity,0)),0) units, "
            "coalesce(sum(max(i.quantity-i.returned_quantity,0)*coalesce(i.unit_price,0)),0) revenue, "
            "sum(CASE WHEN i.quantity-i.returned_quantity>0 AND i.unit_price IS NULL THEN 1 ELSE 0 END) unknown_prices "
            + self._joins() + " WHERE " + where + " GROUP BY " + expression
            + " ORDER BY revenue DESC, sales DESC LIMIT ?",
            values + [int(limit)],
        ).fetchall()
        complete = all(not int(row["unknown_prices"] or 0) for row in rows)
        total = sum(float(row["revenue"] or 0) for row in rows) if complete else None
        return [{
            "label": str(row["label"] or "Неизвестно"),
            "sales": int(row["sales"] or 0),
            "units": float(row["units"] or 0),
            "revenue": None if int(row["unknown_prices"] or 0) else float(row["revenue"] or 0),
            "share": float(row["revenue"] or 0) / total * 100 if total else None,
        } for row in rows]

    def _catalog_options(self, connection):
        brands = [row[0] for row in connection.execute(
            "SELECT DISTINCT coalesce(b.name,p.excel_brand) name FROM catalog_excel_products p "
            "LEFT JOIN erp_brands b ON b.id=p.brand_id WHERE p.active=1 AND trim(coalesce(b.name,p.excel_brand,''))<>'' ORDER BY name"
        ).fetchall()]
        categories = [row[0] for row in connection.execute(
            "SELECT DISTINCT coalesce(c.name,p.excel_category) name FROM catalog_excel_products p "
            "LEFT JOIN erp_categories c ON c.id=p.category_id WHERE p.active=1 AND trim(coalesce(c.name,p.excel_category,''))<>'' ORDER BY name"
        ).fetchall()]
        return brands, categories

    def context(self, section, filters):
        section = section if section in dict(SECTIONS) else "summary"
        with self.database.connect() as connection:
            current = self._metrics(connection, filters)
            previous_filters = self._comparison_filters(filters)
            previous = self._metrics(connection, previous_filters)
            brands, categories = self._catalog_options(connection)
            result = {
                "section": section,
                "section_label": dict(SECTIONS)[section],
                "sections": SECTIONS,
                "filters": filters,
                "brands": brands,
                "categories": categories,
                "sources": [(key, label) for key, label in SOURCE_LABELS.items() if key != "ziro"],
                "current": current,
                "previous": previous,
                "changes": {key: _change(current[key], previous[key]) for key in ("revenue", "sales", "units", "average_order")},
                "timezone": ERP_TIMEZONE_LABEL,
            }
            if section in ("summary", "sales"):
                result["daily"] = self._breakdown(connection, filters, "date(s.created_at)", 800)
                result["daily_max"] = max(
                    [row["revenue"] for row in result["daily"] if row["revenue"] is not None] or [0]
                )
                result["channels"] = self._breakdown(connection, filters, "lower(s.source)", 20)
                result["brands_rows"] = self._breakdown(connection, filters, "coalesce(b.name,p.excel_brand,'Без бренда')", 20)
                result["forecast"] = None
                today = date.today()
                end = _date(filters["to"], today)
                start = _date(filters["from"], end)
                if current["revenue"] is not None and filters["days"] >= MIN_FORECAST_DAYS and end.year == today.year and end.month == today.month:
                    elapsed = max(1, (end - max(start, end.replace(day=1))).days + 1)
                    month_end = (end.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
                    result["forecast"] = current["revenue"] / elapsed * month_end.day
                result["attention"] = self._attention(connection, filters, current, previous)
            elif section == "products":
                result["rows"] = self._product_rows(connection, filters)
            elif section == "channels":
                result["rows"] = self._breakdown(connection, filters, "lower(s.source)", 20)
            elif section == "stock":
                result["rows"] = self._stock_rows(connection, filters)
            elif section == "inventory":
                result["rows"] = self._inventory_rows(connection)
            elif section == "orders":
                result["rows"] = [dict(row) for row in connection.execute(
                    "SELECT erp_status label,count(*) value FROM erp_order_statuses GROUP BY erp_status ORDER BY value DESC"
                ).fetchall()]
                result["source_note"] = "Показаны только фактические локальные этапы ERP: не подтверждён, подтверждён, собран. Оплата, отправка и доставка не моделируются."
            elif section == "profit":
                coverage = connection.execute(
                    "SELECT count(*) total, sum(CASE WHEN ri.purchase_price IS NOT NULL THEN 1 ELSE 0 END) known "
                    "FROM erp_sale_items i LEFT JOIN erp_receipt_items ri ON ri.product_id=i.product_id AND ri.active=1"
                ).fetchone()
                result["profit_coverage"] = (float(coverage["known"] or 0) / float(coverage["total"] or 1) * 100)
                result["unavailable_reason"] = "Нет подтверждённой связи каждой проданной единицы с закупочной партией, комиссиями площадок и логистикой. Прибыль не рассчитывается."
            else:
                result["unavailable_reason"] = (
                    "Источник хранится отдельно и пока не имеет безопасного единого аналитического контракта с проведёнными продажами. "
                    "Раздел не подставляет нули и не объединяет записи предположительно."
                )
            return result

    def _product_rows(self, connection, filters):
        where, values = self._sale_where(filters)
        return [dict(row) for row in connection.execute(
            "SELECT p.id, p.excel_name_raw name, coalesce(b.name,p.excel_brand,'Без бренда') brand, "
            "coalesce(c.name,p.excel_category,'Без категории') category, coalesce(m.name,p.model,'—') model, "
            "p.stock, count(DISTINCT s.id) sales, coalesce(sum(max(i.quantity-i.returned_quantity,0)),0) units, "
            "coalesce(sum(max(i.quantity-i.returned_quantity,0)*coalesce(i.unit_price,0)),0) revenue, "
            "sum(CASE WHEN i.quantity-i.returned_quantity>0 AND i.unit_price IS NULL THEN 1 ELSE 0 END) unknown_prices, max(s.created_at) last_sale "
            + self._joins() + " WHERE " + where + " GROUP BY p.id ORDER BY revenue DESC LIMIT 200",
            values,
        ).fetchall()]

    def _stock_rows(self, connection, filters):
        sales_filters = dict(filters)
        where, values = self._sale_where(sales_filters)
        query = (
            "SELECT p.id,p.excel_name_raw name,coalesce(b.name,p.excel_brand,'Без бренда') brand,p.stock,"
            "coalesce(sum(CASE WHEN " + where + " THEN max(i.quantity-i.returned_quantity,0) ELSE 0 END),0) units "
            "FROM catalog_excel_products p LEFT JOIN erp_brands b ON b.id=p.brand_id "
            "LEFT JOIN erp_sale_items i ON i.product_id=p.id LEFT JOIN erp_sales s ON s.id=i.sale_id "
            "LEFT JOIN erp_categories c ON c.id=coalesce(i.category_id,p.category_id) LEFT JOIN erp_models m ON m.id=p.model_id "
            "WHERE p.active=1 GROUP BY p.id ORDER BY CASE WHEN p.stock<=0 THEN 0 ELSE 1 END, units DESC LIMIT 200"
        )
        rows = []
        for row in connection.execute(query, values).fetchall():
            item = dict(row)
            velocity = float(item["units"] or 0) / float(filters["days"] or 1)
            item["days_cover"] = float(item["stock"] or 0) / velocity if velocity > 0 else None
            if item["stock"] <= 0 and velocity > 0:
                item["recommendation"] = "Заказать срочно"
            elif item["days_cover"] is not None and item["days_cover"] < LOW_STOCK_DAYS:
                item["recommendation"] = "Заказать"
            elif velocity == 0:
                item["recommendation"] = "Пока не заказывать"
            else:
                item["recommendation"] = "Запас достаточен"
            item["recommended_quantity"] = max(0, int(round(velocity * 30 - float(item["stock"] or 0))))
            rows.append(item)
        return rows

    @staticmethod
    def _inventory_rows(connection):
        return [dict(row) for row in connection.execute(
            "SELECT id,coalesce(scope_brand_name,'Весь каталог') scope,status,started_at,completed_at,"
            "checked_positions,adjusted_positions,missing_positions,total_delta "
            "FROM erp_inventory_sessions ORDER BY started_at DESC LIMIT 100"
        ).fetchall()]

    def _attention(self, connection, filters, current, previous):
        events = []
        out_count = int(connection.execute(
            "SELECT count(*) FROM catalog_excel_products WHERE active=1 AND stock<=0"
        ).fetchone()[0])
        if out_count:
            events.append({"level": "warning", "title": "Товары закончились", "detail": "{} активных позиций имеют нулевой остаток.".format(out_count), "href": "/app/analytics?section=stock"})
        if previous["sales"] >= MIN_DECLINE_SALES and current["sales"] < previous["sales"]:
            change = _change(current["sales"], previous["sales"])
            events.append({"level": "warning", "title": "Количество продаж снизилось", "detail": "Изменение к равному предыдущему периоду: {}.".format(change["percent_display"]), "href": "/app/analytics?section=sales"})
        stale = connection.execute("SELECT max(finished_at) FROM catalog_sync_runs WHERE status='completed'").fetchone()[0]
        if stale:
            last = _date(stale, date.today())
            if (date.today() - last).days > 2:
                events.append({"level": "warning", "title": "Каталог Bitrix давно не обновлялся", "detail": "Последняя успешная локальная синхронизация: {}.".format(last.isoformat()), "href": "/app/settings"})
        return events

    def csv_rows(self, context):
        section = context["section"]
        if section in ("products", "channels", "stock", "inventory", "orders"):
            return context.get("rows", [])
        if section in ("summary", "sales"):
            return context.get("daily", [])
        return []


__all__ = ["BusinessAnalytics", "SECTIONS", "SOURCE_LABELS", "parse_filters"]
