import requests
from flask import Flask, render_template

app = Flask(__name__)

ORDERS_URL = "https://tictactoy.ru/api/orders.php"
ORDER_URL = "https://tictactoy.ru/api/order.php?id="

STATUS_NAMES = {
    "N": "Новый",
    "D": "В работе",
    "F": "Выполнен",
    "C": "Отменен",
}


def get_property(order, code):
    properties = order.get("properties") or []

    for prop in properties:
        if prop.get("code") == code:
            value = prop.get("value")
            if value is not None and value != "":
                return value

    return ""


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

    return order


def get_order(order_id):
    response = requests.get(ORDER_URL + str(order_id), timeout=15)
    response.raise_for_status()

    order = response.json().get("order")
    return normalize_order(order)


def get_orders():
    response = requests.get(ORDERS_URL, timeout=15)
    response.raise_for_status()

    short_orders = response.json().get("orders", [])

    orders = []

    for short_order in short_orders:
        order_id = short_order.get("id") or short_order.get("ID")

        if not order_id:
            normalized_short_order = normalize_order(short_order)
            if normalized_short_order:
                orders.append(normalized_short_order)
            continue

        try:
            full_order = get_order(order_id)
            if full_order:
                orders.append(full_order)
            else:
                orders.append(normalize_order(short_order))
        except Exception:
            orders.append(normalize_order(short_order))

    return orders


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
        if str(order.get("id")) == str(order_id):
            selected_order = order
            break

    if not selected_order:
        selected_order = get_order(order_id)

    return render_template(
        "orders.html",
        orders=orders,
        selected_order=selected_order
    )


if __name__ == "__main__":
    app.run(debug=True)