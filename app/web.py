import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
import requests
from app.clients.moysklad import MoySkladClient
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

ORDERS_URL = "https://tictactoy.ru/api/orders.php"
ORDER_URL = "https://tictactoy.ru/api/order.php?id="
UPDATE_ORDER_STATUS_URL = "https://tictactoy.ru/api/update_order_status.php"

UPDATE_ORDER_STATUS_TOKEN = "clock_erp_secret_2026_change_me"

ORDERS_CACHE = {
    "items": [],
    "loaded_at": 0,
}

ORDERS_CACHE_SECONDS = 60

WAREHOUSE_CACHE = {
    "items": [],
    "loaded_at": 0,
}

WAREHOUSE_CACHE_SECONDS = 300

STATUS_NAMES = {
    "N": "Не подтвержден",
    "A": "Подтвержден",
    "T": "Не дозвонились",
    "D": "Собран",
    "C": "Отказ",
    "c": "Отказ",
}


def to_float(value):
    if value is None or value == "":
        return 0.0

    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return 0.0


def get_property(order, code):
    properties = order.get("properties") or []

    for prop in properties:
        if prop.get("code") == code:
            value = prop.get("value")
            if value is not None and value != "":
                return value

    return ""


def calculate_products_total(products):
    total = 0.0

    for product in products:
        price = to_float(product.get("price") or product.get("PRICE"))
        quantity = to_float(product.get("quantity") or product.get("QUANTITY") or 1)
        total += price * quantity

    return total


def normalize_order(order):
    if not order:
        return None

    user = order.get("user") or {}

    status = order.get("status") or order.get("STATUS_ID") or order.get("status_id") or "unknown"

    customer = (
        get_property(order, "FIO")
        or user.get("name")
        or order.get("customer")
        or order.get("client")
        or order.get("name")
        or ""
    )

    phone = (
        get_property(order, "PHONE")
        or user.get("phone")
        or order.get("phone")
        or ""
    )

    email = (
        get_property(order, "EMAIL")
        or user.get("email")
        or order.get("email")
        or ""
    )

    address = (
        get_property(order, "ADDRESS")
        or order.get("address")
        or ""
    )

    city = (
        get_property(order, "CITY")
        or order.get("city")
        or ""
    )

    paid = order.get("paid") or order.get("PAYED") or ""
    paid_name = "Оплачен" if paid == "Y" else "Не оплачен"

    products = order.get("products") or []

    order_total = to_float(
        order.get("price")
        or order.get("PRICE")
        or order.get("sum")
        or order.get("SUM")
    )

    products_total = calculate_products_total(products)
    delivery_price = order_total - products_total

    if delivery_price < 0:
        delivery_price = 0.0

    order["status"] = status
    order["status_name"] = STATUS_NAMES.get(status, status)

    order["customer"] = customer
    order["phone"] = phone
    order["email"] = email
    order["address"] = address
    order["city"] = city

    order["paid"] = paid
    order["paid_name"] = paid_name

    order["products"] = products
    order["products_count"] = len(products)

    order["order_total"] = order_total
    order["products_total"] = products_total
    order["delivery_price"] = delivery_price

    return order


def get_order(order_id):
    response = requests.get(ORDER_URL + str(order_id), timeout=1)
    response.raise_for_status()

    order = response.json().get("order")
    return normalize_order(order)


def get_orders(force=False):
    now = time.time()

    if not force and ORDERS_CACHE["items"] and now - ORDERS_CACHE["loaded_at"] < ORDERS_CACHE_SECONDS:
        return ORDERS_CACHE["items"]

    try:
        response = requests.get(ORDERS_URL, timeout=20)
        response.raise_for_status()

        short_orders = response.json().get("orders", [])

        orders = []

        for short_order in short_orders:
            normalized_order = normalize_order(short_order)
            if normalized_order:
                orders.append(normalized_order)

        ORDERS_CACHE["items"] = orders
        ORDERS_CACHE["loaded_at"] = now

        return orders

    except Exception as error:
        print(f"Ошибка загрузки списка заказов: {error}")

        if ORDERS_CACHE["items"]:
            return ORDERS_CACHE["items"]

        return []


def update_order_status(order_id, new_status):
    allowed_statuses = ["N", "A", "T", "D", "C", "c"]

    if new_status not in allowed_statuses:
        return {
            "status": "error",
            "message": "Недопустимый статус"
        }

    try:
        response = requests.post(
            UPDATE_ORDER_STATUS_URL,
            data={
                "token": UPDATE_ORDER_STATUS_TOKEN,
                "order_id": str(order_id),
                "status": new_status,
            },
            timeout=15
        )

        if not response.ok:
            return {
                "status": "error",
                "message": f"Битрикс не принял статус {new_status}. HTTP {response.status_code}: {response.text[:300]}"
            }

        return response.json()

    except Exception as error:
        return {
            "status": "error",
            "message": f"Ошибка при смене статуса: {error}"
        }


@app.route("/")
def index():
    orders = get_orders()
    selected_order = orders[0] if orders else None

    return render_template(
        "orders.html",
        orders=orders,
        selected_order=selected_order
    )


@app.route("/order/<int:order_id>")
def order_page(order_id):
    orders = get_orders()
    selected_order = None

    for order in orders:
        if str(order.get("id")) == str(order_id) or str(order.get("ID")) == str(order_id):
            selected_order = order
            break

    try:
        full_order = get_order(order_id)
        if full_order:
            selected_order = full_order
    except Exception as error:
        print(f"Полная карточка заказа {order_id} не загрузилась быстро: {error}")

    return render_template(
        "orders.html",
        orders=orders,
        selected_order=selected_order
    )


@app.route("/order/<int:order_id>/status", methods=["POST"])
def order_status_update(order_id):
    new_status = request.form.get("status", "")

    result = update_order_status(order_id, new_status)
    get_orders(force=True)

    if result.get("status") == "ok":
        return redirect(url_for(
            "order_page",
            order_id=order_id,
            notice="success",
            message="Статус заказа обновлен"
        ))

    return redirect(url_for(
        "order_page",
        order_id=order_id,
        notice="error",
        message=result.get("message", "Ошибка смены статуса")
    ))


def get_warehouse_items(limit=100, force=False):
    now = time.time()

    if not force and WAREHOUSE_CACHE["items"] and now - WAREHOUSE_CACHE["loaded_at"] < WAREHOUSE_CACHE_SECONDS:
        return WAREHOUSE_CACHE["items"]

    try:
        client = MoySkladClient()

        stock_data = client.get("/report/stock/all", params={"limit": limit})
        product_data = client.get("/entity/product", params={"limit": limit})

        stock_rows = stock_data.get("rows", []) if stock_data else []
        product_rows = product_data.get("rows", []) if product_data else []

        stock_by_code = {}

        for row in stock_rows:
            code = str(row.get("code") or "").strip()

            if code:
                stock_by_code[code] = row

        items = []

        for product in product_rows:
            code = str(product.get("code") or "").strip()
            stock_row = stock_by_code.get(code, {})

            items.append({
                "id": product.get("id") or "",
                "name": product.get("name") or stock_row.get("name") or "",
                "article": product.get("article") or stock_row.get("article") or "",
                "code": code,
                "stock": stock_row.get("stock") or 0,
                "reserve": stock_row.get("reserve") or 0,
                "quantity": stock_row.get("quantity") or 0,
            })

        # Если в отчёте остатков есть позиция, которой нет в /entity/product, всё равно покажем её
        product_codes = set(str(product.get("code") or "").strip() for product in product_rows)

        for row in stock_rows:
            code = str(row.get("code") or "").strip()

            if code and code not in product_codes:
                items.append({
                    "id": "",
                    "name": row.get("name") or "",
                    "article": row.get("article") or "",
                    "code": code,
                    "stock": row.get("stock") or 0,
                    "reserve": row.get("reserve") or 0,
                    "quantity": row.get("quantity") or 0,
                })

        WAREHOUSE_CACHE["items"] = items
        WAREHOUSE_CACHE["loaded_at"] = now

        return items

    except Exception as error:
        print(f"Ошибка загрузки склада: {error}")
        return WAREHOUSE_CACHE["items"]


@app.route("/warehouse")
def warehouse_page():
    query = request.args.get("q", "").strip().lower()
    items = get_warehouse_items(limit=100)

    if query:
        items = [
            item for item in items
            if query in item["name"].lower()
            or query in item["article"].lower()
            or query in item["code"].lower()
        ]

    total_stock = sum(float(item["stock"]) for item in items)
    total_reserve = sum(float(item["reserve"]) for item in items)
    total_available = sum(float(item["quantity"]) for item in items)

    return render_template(
        "warehouse.html",
        items=items,
        query=query,
        total_stock=total_stock,
        total_reserve=total_reserve,
        total_available=total_available,
    )


@app.route("/warehouse/add", methods=["POST"])
def warehouse_add_product():
    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip()
    article = request.form.get("article", "").strip()

    if not name or not code:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Название и код обязательны"
        ))

    try:
        client = MoySkladClient()
        product = client.create_product(name=name, code=code, article=article or None)

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        if product:
            return redirect(url_for(
                "warehouse_page",
                notice="success",
                message="Позиция добавлена в МойСклад"
            ))

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="МойСклад не создал позицию"
        ))

    except Exception as error:
        print(f"Ошибка добавления позиции: {error}")
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Ошибка добавления позиции"
        ))


@app.route("/warehouse/archive", methods=["POST"])
def warehouse_archive_product():
    code = request.form.get("code", "").strip()

    if not code:
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Не указан код товара"
        ))

    try:
        client = MoySkladClient()
        product = client.find_product_by_code(code)

        if not product:
            return redirect(url_for(
                "warehouse_page",
                notice="error",
                message="Товар не найден в МойСклад"
            ))

        result = client.archive_product(product.get("id"))

        WAREHOUSE_CACHE["items"] = []
        WAREHOUSE_CACHE["loaded_at"] = 0

        if result:
            return redirect(url_for(
                "warehouse_page",
                notice="success",
                message="Позиция убрана в архив МойСклад"
            ))

        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="МойСклад не убрал позицию"
        ))

    except Exception as error:
        print(f"Ошибка архивации позиции: {error}")
        return redirect(url_for(
            "warehouse_page",
            notice="error",
            message="Ошибка удаления позиции"
        ))



if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
