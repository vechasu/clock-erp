import requests
from flask import Flask, render_template

app = Flask(__name__)

ORDERS_URL = "https://tictactoy.ru/api/orders.php"
ORDER_URL = "https://tictactoy.ru/api/order.php?id="

STATUS_NAMES = {
    "N": "🟡 Новый",
    "D": "🔵 Собран",
    "F": "🟢 Выполнен",
    "C": "🔴 Отменен",
}


def add_status_name(order):
    if order:
        order["status_name"] = STATUS_NAMES.get(
            order.get("status"),
            order.get("status")
        )
    return order


def get_orders():
    response = requests.get(ORDERS_URL)
    orders = response.json().get("orders", [])

    for order in orders:
        add_status_name(order)

    return orders


def get_order(order_id):
    response = requests.get(ORDER_URL + str(order_id))
    order = response.json().get("order")
    return add_status_name(order)


@app.route("/")
def index():
    orders = get_orders()
    selected_order = orders[0] if orders else None

    if selected_order:
        selected_order = get_order(selected_order["id"])

    return render_template(
        "orders.html",
        orders=orders,
        selected_order=selected_order
    )


@app.route("/order/<int:order_id>")
def order_page(order_id):
    orders = get_orders()
    selected_order = get_order(order_id)

    return render_template(
        "orders.html",
        orders=orders,
        selected_order=selected_order
    )


if __name__ == "__main__":
    app.run(debug=True)