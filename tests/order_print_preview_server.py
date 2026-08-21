from flask import request

from app import web


def _item(index, long_name=False):
    name = f"Будильник BC03 Black, позиция {index + 1}"
    if long_name:
        name += " — очень длинное название товара для проверки аккуратного переноса внутри узкой печатной таблицы" * 2
    return {
        "name": name,
        "quantity": 2 if index == 0 else 1,
        "sale_unit_price": 5557,
        "line_total": 11114 if index == 0 else 5557,
    }


def _fixture_order(case="one"):
    item_count = {"one": 1, "multiple": 4, "long": 3, "missing": 2, "many": 32}.get(case, 1)
    items = [_item(index, long_name=case == "long") for index in range(item_count)]
    products_total = sum(float(item["line_total"]) for item in items)
    order = {
        "external_id": "21110",
        "number": "21110",
        "created_at": "2026-08-20T15:00:52",
        "customer": "Михаил Афанасенко",
        "phone": "+79154072737",
        "email": "mva_197@yandex.ru",
        "delivery": "СДЭК: Доставка курьером Москва",
        "country": "Россия",
        "region": "Москва",
        "city": "Москва",
        "address": "ПВЗ СДЭК МОСКВА ГВАРДЕЙСКАЯ Д.3 СТР 1",
        "postal_code": "121471",
        "payment": "При получении (карта / наличные)",
        "products_total": products_total,
        "discount": 0,
        "delivery_price": 390,
        "order_total": products_total + 390,
        "items": items,
    }
    if case == "long":
        order.update({
            "customer": "Александра-Мария Константиновна Оченьдлинная-Фамилия",
            "address": "улица Академика Очень Длинное Название, дом 123, корпус 45, строение 67, подъезд 8, этаж 19, квартира 999, дополнительный ориентир у зелёной арки",
            "region": "Ханты-Мансийский автономный округ — Югра",
        })
    if case == "missing":
        order.update({
            "email": None,
            "phone": None,
            "postal_code": None,
            "region": None,
            "address": None,
            "delivery": None,
        })
    return order


def _get_fixture_order(_order_id):
    return _fixture_order(request.args.get("case", "one"))


web.app.config.update(TESTING=True, AUTH_TESTING=False)
web.get_order = _get_fixture_order


if __name__ == "__main__":
    web.app.run(host="127.0.0.1", port=4174, debug=False, use_reloader=False)
