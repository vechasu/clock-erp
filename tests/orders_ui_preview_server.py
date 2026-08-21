"""Read-only fixture server for responsive orders UI smoke checks."""

from app import web


def fixture_orders():
    statuses = ("N", "A", "D")
    rows = []
    for index in range(45):
        order_id = str(22000 + index)
        rows.append({
            "id": order_id,
            "number": order_id,
            "status": statuses[index % len(statuses)],
            "status_name": web.STATUS_NAMES[statuses[index % len(statuses)]],
            "customer": (
                "Александра-Мария Константиновна Оченьдлинная-Фамилия"
                if index == 0 else "Клиент {}".format(index + 1)
            ),
            "phone": "+7 999 123-45-{:02d}".format(index),
            "created_at": "2026-08-{:02d} 10:30".format(21 - index % 20),
            "order_total": 12990 + index * 100,
            "paid": "Y" if index % 2 else "N",
            "paid_name": "Оплачен" if index % 2 else "Не оплачен",
            "payment": "Банковская карта" if index % 2 else "При получении",
            "delivery": "СДЭК до пункта выдачи",
            "source": "Интернет-магазин",
            "products_count": 2,
            "products": [],
            "sync_state": "complete",
        })
    return rows


ORDERS = fixture_orders()


def fixture_order(order_id):
    base = dict(next(
        (row for row in ORDERS if row["id"] == str(order_id)), ORDERS[0]
    ))
    base.update({
        "email": "customer@example.test",
        "country": "Россия",
        "region": "Ханты-Мансийский автономный округ — Югра",
        "city": "Ханты-Мансийск",
        "address": (
            "улица Академика Очень Длинное Название, дом 123, корпус 45, "
            "строение 67, подъезд 8, этаж 19, квартира 999"
        ),
        "tracking": "TTT-22000-LONG-TRACKING-NUMBER",
        "comment": "Позвонить клиенту за час до передачи заказа курьеру.",
        "products_total": 25000,
        "discount": 500,
        "delivery_price": 490,
        "order_total": 24990,
        "calculation_complete": True,
        "calculation_consistent": True,
        "products": [
            {
                "basket_id": "line-1",
                "product_id": "bx-1",
                "name": "Будильник с очень длинным названием коллекции и модели для проверки переноса",
                "brand": "Tictactoy",
                "category": "Будильники",
                "quantity": 1,
                "price": 12990,
                "line_total": 12990,
            },
            {
                "basket_id": "line-2",
                "product_id": "bx-2",
                "name": "Настенные часы",
                "brand": "Vechasu",
                "category": "Часы",
                "quantity": 1,
                "price": 12010,
                "line_total": 12010,
            },
        ],
    })
    return base


def mapping_context(products, **_kwargs):
    return {
        "line:{}".format(item["basket_id"]): {
            "state": "unmapped",
            "state_label": "Не сопоставлен",
            "product": None,
        }
        for item in products
    }


web.app.config.update(TESTING=True, AUTH_TESTING=False)
web.get_orders = lambda force=False: ORDERS
web.get_order = fixture_order
web.load_order_product_mappings = lambda _order_id: {}
web.build_catalog_product_order_counts = lambda *args, **kwargs: {}
web.build_order_product_mapping_context = mapping_context
web.get_order_conducted_sale = lambda _order_id: None
web.has_legacy_order_stock_writeoff = lambda _order_id: False


if __name__ == "__main__":
    web.app.run(host="127.0.0.1", port=4175, debug=False, use_reloader=False)
