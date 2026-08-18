"""Exact, shared sale discount calculations."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CENT = Decimal("0.01")
HUNDRED = Decimal("100")
DISCOUNT_TYPES = {"none", "percent", "fixed"}


def decimal_money(value, label="Цена"):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("{} должна быть числом.".format(label))
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("{} должна быть неотрицательной.".format(label))
    return parsed.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_sale_pricing(original_price, discount_type="none",
                           discount_value=0, reason=""):
    original = decimal_money(original_price, "Исходная цена")
    discount_type = str(discount_type or "none").strip().lower()
    if discount_type not in DISCOUNT_TYPES:
        raise ValueError("Выберите процентную или фиксированную скидку.")
    if original is None:
        if discount_type != "none":
            raise ValueError("Для скидки укажите исходную цену.")
        return {
            "original_unit_price": None,
            "discount_type": "none",
            "discount_value": "0.00",
            "discount_amount": "0.00",
            "discount_reason": str(reason or "").strip(),
            "unit_price": None,
        }
    value = decimal_money(discount_value or 0, "Скидка") or Decimal("0")
    if discount_type == "percent":
        if value > HUNDRED:
            raise ValueError("Процент скидки не может быть больше 100.")
        amount = (original * value / HUNDRED).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
    elif discount_type == "fixed":
        amount = min(value, original)
    else:
        value = Decimal("0")
        amount = Decimal("0")
    final = max(original - amount, Decimal("0")).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return {
        "original_unit_price": format(original, ".2f"),
        "discount_type": discount_type,
        "discount_value": format(value, ".2f"),
        "discount_amount": format(amount, ".2f"),
        "discount_reason": str(reason or "").strip()[:240],
        "unit_price": format(final, ".2f"),
    }


def order_line_pricing(product):
    """Treat Bitrix PRICE as paid price and BASE_PRICE as the original price."""
    paid = product.get("price")
    if paid in (None, ""):
        paid = product.get("PRICE")
    original = product.get("base_price")
    if original in (None, ""):
        original = product.get("BASE_PRICE")
    if original in (None, ""):
        original = paid
    original_value = decimal_money(original, "Исходная цена")
    paid_value = decimal_money(paid, "Итоговая цена")
    if original_value is None:
        return calculate_sale_pricing(None)
    if paid_value is None:
        paid_value = original_value
    paid_value = min(paid_value, original_value)
    discount = original_value - paid_value
    return calculate_sale_pricing(
        original_value,
        "fixed" if discount else "none",
        discount,
        "Скидка из заказа" if discount else "",
    )
