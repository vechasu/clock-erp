from flask import request

from app import web


def _fixture_order(many=False):
    item_count = 64 if many else 1
    items = [
        {
            "name": (
                f"Позиция {index + 1}: часы с очень длинным названием, "
                "которое проверяет перенос текста без обрезания"
            ),
            "sku": f"WATCH-{index + 1:03d}",
            "quantity": 2 if index == 0 else 1,
            "sale_unit_price": 1250.5,
            "line_total": 2501 if index == 0 else 1250.5,
        }
        for index in range(item_count)
    ]
    products_total = sum(float(item["line_total"]) for item in items)
    return {
        "id": "18593",
        "number": "18593",
        "created_at": "2026-08-21T10:30:00",
        "status_name": "Подтверждён",
        "customer": "Тестовый покупатель",
        "phone": "+7 900 000-00-00",
        "delivery": "СДЭК: доставка до ПВЗ",
        "country": "Россия",
        "region": "Москва",
        "city": "Москва",
        "address": "Россия, Москва, Москва, ул. Тверская, д. 1#SMSC1",
        "tracking": "TEST-18593",
        "comment": "Тестовый комментарий для проверки переноса без обрезания.",
        "payment": "Оплата в пункте выдачи",
        "paid": "N",
        "products_total": products_total,
        "discount": 100,
        "delivery_price": 399,
        "order_total": products_total + 299,
        "items": items,
    }


def _get_fixture_order(_order_id):
    return _fixture_order(many=request.args.get("case") == "many")


web.app.config.update(TESTING=True, AUTH_TESTING=False)
web.get_order = _get_fixture_order


if __name__ == "__main__":
    web.app.run(host="127.0.0.1", port=4174, debug=False, use_reloader=False)
