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

import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import quote

from app.catalog_db import CatalogDatabase


ERP_TIMEZONE_LABEL = "Europe/Moscow (UTC+03:00)"
LOW_STOCK_DAYS = 14
MIN_FORECAST_DAYS = 5
MIN_DECLINE_SALES = 5
STOCK_HORIZONS = (30, 60, 90)
STOCK_PAGE_SIZES = (25, 50, 100)

METRIC_REGISTRY = {
    "revenue": {
        "label": "Выручка", "source": "Проведённые продажи ERP",
        "formula": "Σ (цена единицы × проданное количество после возвратов)",
        "exclusions": "Отменённые, удалённые, архивные продажи и возвращённые единицы",
        "limitations": "Не показывается, если хотя бы у одной проданной позиции нет цены.",
    },
    "sales": {
        "label": "Продажи", "source": "Проведённые продажи ERP",
        "formula": "Количество уникальных документов с положительным количеством после возвратов",
        "exclusions": "Заказы, отменённые, удалённые, архивные и полностью возвращённые продажи",
        "limitations": "Дата — created_at документа в часовом поясе ERP.",
    },
    "units": {
        "label": "Проданные единицы", "source": "Строки проведённых продаж ERP",
        "formula": "Σ max(количество − возвращено, 0)",
        "exclusions": "Единицы из отменённых, удалённых и архивных документов",
        "limitations": "Количество не заменяется числом строк документа.",
    },
    "average_order": {
        "label": "Средний чек", "source": "Проведённые продажи ERP",
        "formula": "Выручка ÷ количество продаж",
        "exclusions": "Те же исключения, что у выручки и продаж",
        "limitations": "Не рассчитывается при неполных ценах; при отсутствии продаж равен 0.",
    },
    "stock_recommendation": {
        "label": "Рекомендация закупки", "source": "Остатки, продажи ERP и локальный контур закупок",
        "formula": "max(0, средние продажи за 90 дней × горизонт − остаток − открытый заказ)",
        "exclusions": "Отменённые заказы поставщика и закрытые клиентские запросы",
        "limitations": "Lead time и страховой запас не хранятся: количество предварительное, срочность подтверждается спросом.",
    },
}

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
    def choice(name, allowed, default):
        value = str(arguments.get(name) or default).strip().casefold()
        return value if value in allowed else default

    def bounded_int(name, default, allowed=None):
        try:
            value = int(arguments.get(name) or default)
        except (TypeError, ValueError):
            value = default
        return value if allowed is None or value in allowed else default

    return {
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "days": (end_date - start_date).days + 1,
        "channel": str(arguments.get("channel") or "").strip().casefold(),
        "brand": str(arguments.get("brand") or "").strip(),
        "category": str(arguments.get("category") or "").strip(),
        "model": str(arguments.get("model") or "").strip(),
        "product": str(arguments.get("product") or "").strip(),
        "q": str(arguments.get("q") or "").strip()[:160],
        "horizon": bounded_int("horizon", 60, STOCK_HORIZONS),
        "stock_state": choice("stock_state", {"all", "out", "positive"}, "all"),
        "recommendation": choice("recommendation", {"all", "urgent", "plan", "ordered", "stale", "enough", "insufficient"}, "all"),
        "confidence": choice("confidence", {"all", "high", "medium", "low"}, "all"),
        "sort": choice("sort", {"urgency", "demand", "stock", "age", "name"}, "urgency"),
        "page": max(1, bounded_int("page", 1)),
        "per_page": bounded_int("per_page", 50, STOCK_PAGE_SIZES),
        "signal_type": choice("signal_type", {"all", "stockout", "low_cover", "stale", "sales_decline", "inventory"}, "all"),
        "urgency": choice("urgency", {"all", "critical", "high", "medium", "low"}, "all"),
    }


class BusinessAnalytics(object):
    """Single query layer shared by HTML, drill-down fragments and CSV."""

    def __init__(self, database=None, purchase_store=None):
        self.database = database or CatalogDatabase(cache_initialization=True)
        self.purchase_store = purchase_store

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
                "metric_registry": METRIC_REGISTRY,
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
                stock = self._stock_rows(connection, filters)
                result.update(stock)
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

    def _purchase_state(self):
        state = {}
        if self.purchase_store is None:
            return state, "Источник закупок не подключён"
        try:
            with self.purchase_store.connect() as connection:
                ordered_rows = connection.execute(
                    "SELECT p.product_id,COALESCE(SUM(CASE WHEN o.status IN ('ordered','partially_received') "
                    "THEN i.quantity-i.received_quantity ELSE 0 END),0) ordered_quantity "
                    "FROM purchase_plan_items p JOIN supplier_order_items i ON i.plan_item_id=p.id "
                    "JOIN supplier_orders o ON o.id=i.order_id WHERE p.product_id IS NOT NULL GROUP BY p.product_id"
                ).fetchall()
                request_rows = connection.execute(
                    "SELECT p.product_id,COUNT(DISTINCT r.id) customer_requests,COALESCE(SUM(r.quantity),0) requested_quantity "
                    "FROM purchase_plan_items p JOIN purchase_plan_requests l ON l.plan_item_id=p.id "
                    "JOIN purchase_requests r ON r.id=l.request_id WHERE p.product_id IS NOT NULL AND r.archived=0 "
                    "AND r.status NOT IN ('notified','sold','closed') GROUP BY p.product_id"
                ).fetchall()
                for row in ordered_rows:
                    state[int(row["product_id"])] = dict(row)
                for row in request_rows:
                    state.setdefault(int(row["product_id"]), {}).update(dict(row))
            return state, None
        except (OSError, RuntimeError, sqlite3.Error):
            return state, "Источник закупок временно недоступен"

    @staticmethod
    def _stock_classify(item, horizon):
        units90 = float(item["units_90"] or 0)
        stock = float(item["stock"] or 0)
        ordered = float(item.get("ordered_quantity") or 0)
        requested = float(item.get("requested_quantity") or 0)
        velocity = units90 / 90.0
        item["velocity"] = velocity
        item["days_cover"] = stock / velocity if velocity > 0 else None
        item["recommended_quantity"] = max(0, int(round(velocity * horizon + requested - stock - ordered)))
        item["confidence"] = "high" if units90 >= 6 else "medium" if units90 >= 3 else "low"
        if ordered > 0:
            code, label = "ordered", "Уже заказано"
        elif stock <= 0 and (units90 >= 2 or requested > 0):
            code, label = "urgent", "Заказать срочно"
        elif velocity > 0 and item["days_cover"] < horizon and units90 >= 3:
            code, label = "plan", "Включить в план"
        elif stock > 0 and (not item.get("last_sale") or int(item.get("days_since_sale") or 0) >= 90):
            code, label = "stale", "Неликвид / проверить"
        elif units90 >= 3:
            code, label = "enough", "Запас достаточен"
        else:
            code, label = "insufficient", "Недостаточно данных"
        item["recommendation_code"] = code
        item["recommendation"] = label
        item["preliminary"] = True
        item["evidence"] = "{} ед. за 90 дней; остаток {}; в заказе {}; запросы клиентов {}".format(
            _number(units90), _number(stock), _number(ordered), int(item.get("customer_requests") or 0)
        )
        return item

    def _stock_rows(self, connection, filters):
        anchor = _date(filters["to"], date.today())
        cut30 = (anchor - timedelta(days=29)).isoformat()
        cut60 = (anchor - timedelta(days=59)).isoformat()
        cut90 = (anchor - timedelta(days=89)).isoformat()
        catalog_clauses = ["p.active=1"]
        valid_sale = "s.cancelled_at IS NULL AND s.deleted_at IS NULL AND s.archived_at IS NULL"
        demand_values = [
            cut30, anchor.isoformat(), cut60, anchor.isoformat(),
            cut90, anchor.isoformat(),
        ]
        if filters["channel"]:
            valid_sale += " AND lower(s.source)=?"
            demand_values.append(filters["channel"])
        catalog_values = []
        if filters["brand"]:
            catalog_clauses.append("coalesce(b.name,p.excel_brand,'')=?")
            catalog_values.append(filters["brand"])
        if filters["category"]:
            catalog_clauses.append("coalesce(c.name,p.excel_category,'')=?")
            catalog_values.append(filters["category"])
        if filters["model"]:
            catalog_clauses.append("coalesce(m.name,p.model,'')=?")
            catalog_values.append(filters["model"])
        if filters["product"]:
            catalog_clauses.append("cast(p.id as text)=?")
            catalog_values.append(filters["product"])
        if filters["q"]:
            catalog_clauses.append("(p.excel_name_raw LIKE ? OR coalesce(b.name,p.excel_brand,'') LIKE ? OR coalesce(p.excel_article,'') LIKE ?)")
            pattern = "%{}%".format(filters["q"])
            catalog_values.extend([pattern, pattern, pattern])
        if filters["stock_state"] == "out":
            catalog_clauses.append("p.stock<=0")
        elif filters["stock_state"] == "positive":
            catalog_clauses.append("p.stock>0")
        demand_query = (
            "SELECT i.product_id,"
            "coalesce(sum(CASE WHEN date(s.created_at) BETWEEN ? AND ? THEN max(i.quantity-i.returned_quantity,0) ELSE 0 END),0) units_30,"
            "coalesce(sum(CASE WHEN date(s.created_at) BETWEEN ? AND ? THEN max(i.quantity-i.returned_quantity,0) ELSE 0 END),0) units_60,"
            "coalesce(sum(CASE WHEN date(s.created_at) BETWEEN ? AND ? THEN max(i.quantity-i.returned_quantity,0) ELSE 0 END),0) units_90,"
            "max(CASE WHEN i.quantity-i.returned_quantity>0 THEN s.created_at END) last_sale "
            "FROM erp_sale_items i JOIN erp_sales s ON s.id=i.sale_id "
            "WHERE " + valid_sale + " GROUP BY i.product_id"
        )
        demand = {int(row["product_id"]): dict(row) for row in connection.execute(
            demand_query, demand_values,
        ).fetchall()}
        catalog_query = (
            "SELECT p.id,p.excel_name_raw name,coalesce(b.name,p.excel_brand,'Без бренда') brand,p.stock "
            "FROM catalog_excel_products p LEFT JOIN erp_brands b ON b.id=p.brand_id "
            "LEFT JOIN erp_categories c ON c.id=p.category_id LEFT JOIN erp_models m ON m.id=p.model_id "
            "WHERE " + " AND ".join(catalog_clauses)
        )
        purchase_state, purchase_error = self._purchase_state()
        rows = []
        for row in connection.execute(catalog_query, catalog_values).fetchall():
            item = dict(row)
            item.update(demand.get(int(item["id"]), {}))
            item.setdefault("units_30", 0)
            item.setdefault("units_60", 0)
            item.setdefault("units_90", 0)
            item.setdefault("last_sale", None)
            item.update(purchase_state.get(int(item["id"]), {}))
            item.setdefault("ordered_quantity", 0)
            item.setdefault("customer_requests", 0)
            item.setdefault("requested_quantity", 0)
            item["days_since_sale"] = ((anchor - _date(item["last_sale"], anchor)).days if item["last_sale"] else None)
            rows.append(self._stock_classify(item, filters["horizon"]))
        if filters["recommendation"] != "all":
            rows = [row for row in rows if row["recommendation_code"] == filters["recommendation"]]
        if filters["confidence"] != "all":
            rows = [row for row in rows if row["confidence"] == filters["confidence"]]
        urgency = {"urgent": 0, "plan": 1, "ordered": 2, "stale": 3, "insufficient": 4, "enough": 5}
        sorters = {
            "urgency": lambda row: (urgency[row["recommendation_code"]], -float(row["units_90"] or 0), row["name"]),
            "demand": lambda row: (-float(row["units_90"] or 0), row["name"]),
            "stock": lambda row: (float(row["stock"] or 0), row["name"]),
            "age": lambda row: (-(row["days_since_sale"] if row["days_since_sale"] is not None else 100000), row["name"]),
            "name": lambda row: row["name"],
        }
        rows.sort(key=sorters[filters["sort"]])
        total = len(rows)
        start = (filters["page"] - 1) * filters["per_page"]
        return {
            "rows": rows[start:start + filters["per_page"]], "stock_all_rows": rows,
            "pagination": {"page": filters["page"], "per_page": filters["per_page"], "total": total,
                           "pages": max(1, (total + filters["per_page"] - 1) // filters["per_page"])},
            "purchase_source_error": purchase_error,
        }

    @staticmethod
    def _inventory_rows(connection):
        return [dict(row) for row in connection.execute(
            "SELECT id,coalesce(scope_brand_name,'Весь каталог') scope,status,started_at,completed_at,"
            "checked_positions,adjusted_positions,missing_positions,total_delta "
            "FROM erp_inventory_sessions ORDER BY started_at DESC LIMIT 100"
        ).fetchall()]

    def _attention(self, connection, filters, current, previous):
        events = []
        stock_result = self._stock_rows(connection, dict(filters, page=1, per_page=100))
        stock_rows = stock_result["stock_all_rows"]
        urgent = [row for row in stock_rows if row["recommendation_code"] == "urgent"]
        low = [row for row in stock_rows if row["recommendation_code"] == "plan"]
        stale_rows = [row for row in stock_rows if row["recommendation_code"] == "stale"]
        updated_at = datetime.now().replace(microsecond=0).isoformat()
        stock_href = "/app/analytics?section=stock&from={}&to={}&channel={}&brand={}&horizon={}".format(
            filters["from"], filters["to"], quote(filters["channel"]), quote(filters["brand"]), filters["horizon"]
        )
        if urgent:
            events.append({"type": "stockout", "urgency": "critical", "confidence": "high" if any(row["confidence"] == "high" for row in urgent) else "medium",
                           "title": "Дефицит с подтверждённым спросом", "detail": "{} позиций без остатка имеют повторный спрос или открытые запросы клиентов.".format(len(urgent)),
                           "evidence": "Сигнал не включает товары без спроса и единичные продажи.", "quantity": sum(row["recommended_quantity"] for row in urgent),
                           "money_impact": None, "updated_at": updated_at, "href": stock_href + "&recommendation=urgent&sort=urgency"})
        if low:
            events.append({"type": "low_cover", "urgency": "high", "confidence": "medium", "title": "Запас ниже горизонта планирования",
                           "detail": "{} позиций с минимум 3 продажами за 90 дней требуют плановой проверки.".format(len(low)),
                           "evidence": "Расчёт по горизонту {} дней; lead time и страховой запас не заданы.".format(filters["horizon"]),
                           "quantity": sum(row["recommended_quantity"] for row in low), "money_impact": None, "updated_at": updated_at,
                           "href": stock_href + "&recommendation=plan&sort=urgency"})
        if stale_rows:
            events.append({"type": "stale", "urgency": "medium", "confidence": "medium", "title": "Остатки без подтверждённого движения",
                           "detail": "{} позиций в наличии не продавались 90 дней или не имеют истории продаж.".format(len(stale_rows)),
                           "evidence": "Деньги риска не показаны: подтверждённой себестоимости по партиям нет.", "quantity": sum(float(row["stock"] or 0) for row in stale_rows),
                           "money_impact": None, "updated_at": updated_at, "href": stock_href + "&recommendation=stale&sort=age"})
        if previous["sales"] >= MIN_DECLINE_SALES and current["sales"] < previous["sales"]:
            change = _change(current["sales"], previous["sales"])
            events.append({"type": "sales_decline", "urgency": "medium", "confidence": "high", "title": "Количество продаж снизилось",
                           "detail": "Изменение к равному предыдущему периоду: {}.".format(change["percent_display"]),
                           "evidence": "Предыдущий период содержит {} продаж (минимум для сигнала — {}).".format(previous["sales"], MIN_DECLINE_SALES),
                           "quantity": previous["sales"] - current["sales"], "money_impact": None, "updated_at": updated_at,
                           "href": "/app/analytics?section=sales&from={}&to={}&channel={}&brand={}".format(filters["from"], filters["to"], quote(filters["channel"]), quote(filters["brand"]))})
        current_brands = {row["label"]: row for row in self._breakdown(connection, filters, "coalesce(b.name,p.excel_brand,'Без бренда')", 100)}
        previous_brands = {row["label"]: row for row in self._breakdown(connection, self._comparison_filters(filters), "coalesce(b.name,p.excel_brand,'Без бренда')", 100)}
        brand_declines = []
        for label, before in previous_brands.items():
            after = current_brands.get(label, {"sales": 0, "revenue": None})
            if before["sales"] >= 3 and after["sales"] < before["sales"]:
                brand_declines.append((before["sales"] - after["sales"], label, before, after))
        for difference, label, before, after in sorted(brand_declines, reverse=True)[:3]:
            events.append({"type": "sales_decline", "urgency": "medium", "confidence": "medium", "title": "Спад продаж бренда: {}".format(label),
                           "detail": "{} → {} продаж к равному предыдущему периоду.".format(before["sales"], after["sales"]),
                           "evidence": "Сигнал создаётся только при минимум 3 продажах бренда в базе сравнения.", "quantity": difference,
                           "money_impact": None, "updated_at": updated_at,
                           "href": "/app/analytics?section=sales&from={}&to={}&channel={}&brand={}".format(filters["from"], filters["to"], quote(filters["channel"]), quote(label))})
        inventory = connection.execute(
            "SELECT count(*) sessions,coalesce(sum(adjusted_positions+missing_positions),0) discrepancies,max(completed_at) updated_at "
            "FROM erp_inventory_sessions WHERE completed_at IS NOT NULL AND adjusted_positions+missing_positions>0"
        ).fetchone()
        if int(inventory["sessions"] or 0) >= 2:
            events.append({"type": "inventory", "urgency": "high", "confidence": "high", "title": "Повторяющиеся расхождения инвентаризаций",
                           "detail": "Расхождения зафиксированы в {} завершённых сессиях.".format(inventory["sessions"]),
                           "evidence": "Суммарно {} скорректированных или отсутствующих позиций.".format(inventory["discrepancies"]),
                           "quantity": int(inventory["discrepancies"] or 0), "money_impact": None,
                           "updated_at": inventory["updated_at"] or updated_at, "href": "/app/analytics?section=inventory"})
        stale = connection.execute("SELECT max(finished_at) FROM catalog_sync_runs WHERE status='completed'").fetchone()[0]
        if stale:
            last = _date(stale, date.today())
            if (date.today() - last).days > 2:
                events.append({"type": "sync", "urgency": "high", "confidence": "high", "title": "Каталог Bitrix давно не обновлялся",
                               "detail": "Последняя успешная локальная синхронизация: {}.".format(last.isoformat()), "evidence": "Возраст локального снимка превышает 2 дня.",
                               "quantity": None, "money_impact": None, "updated_at": updated_at, "href": "/app/settings"})
        if filters["signal_type"] != "all":
            events = [event for event in events if event["type"] == filters["signal_type"]]
        if filters["urgency"] != "all":
            events = [event for event in events if event["urgency"] == filters["urgency"]]
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        return sorted(events, key=lambda event: (order[event["urgency"]], event["title"]))

    def csv_rows(self, context):
        section = context["section"]
        if section == "customers":
            return context.get("customer_analytics", {}).get("top", [])
        if section in ("products", "channels", "stock", "inventory", "orders"):
            return context.get("stock_all_rows", []) if section == "stock" else context.get("rows", [])
        if section in ("summary", "sales"):
            return context.get("daily", [])
        return []


__all__ = ["BusinessAnalytics", "METRIC_REGISTRY", "SECTIONS", "SOURCE_LABELS", "parse_filters"]
