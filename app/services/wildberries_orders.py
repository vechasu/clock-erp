"""Normalize and persist Wildberries FBS assembly orders without side effects."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def _text(value):
    return str(value or "").strip()


def _price(order):
    value = order.get("convertedFinalPrice")
    if value is None:
        value = order.get("finalPrice")
    if value is None:
        value = order.get("convertedPrice")
    if value is None:
        value = order.get("price")
    try:
        return float((Decimal(str(value)) / Decimal("100")).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def normalize_wildberries_order(order, synced_at=None):
    """Convert one assembly task to the existing order-card contract."""
    if not isinstance(order, dict) or order.get("id") in (None, ""):
        return None
    wb_order_id = _text(order["id"])
    skus = [_text(value) for value in (order.get("skus") or []) if _text(value)]
    article = _text(order.get("article"))
    item_name = article or "Товар Wildberries"
    supplier_status = _text(order.get("supplierStatus")) or "new"
    wb_status = _text(order.get("wbStatus"))
    status_label = supplier_status
    if wb_status:
        status_label += " · " + wb_status
    created_at = _text(order.get("createdAt"))
    total = _price(order)
    product = {
        "source": "wildberries",
        "id": wb_order_id,
        "order_item_id": wb_order_id,
        "product_id": "",
        "name": item_name,
        "quantity": 1,
        "price": total,
        "line_total": total,
        "article": article,
        "sku": skus[0] if skus else "",
        "barcode": skus[0] if skus else "",
        "skus": skus,
        "nm_id": order.get("nmId"),
        "chrt_id": order.get("chrtId"),
    }
    return {
        "id": "wb:" + wb_order_id,
        "number": wb_order_id,
        "source": "wildberries",
        "source_name": "Wildberries",
        "wb_order_id": wb_order_id,
        "order_uid": _text(order.get("orderUid")),
        "rid": _text(order.get("rid")),
        "status": supplier_status,
        "status_name": status_label,
        "supplier_status": supplier_status,
        "wb_status": wb_status,
        "created_at": created_at,
        "date": created_at,
        "order_total": total,
        "price": total,
        "customer": "Покупатель Wildberries",
        "phone": "",
        "warehouse_id": order.get("warehouseId"),
        "office_id": order.get("officeId"),
        "delivery_type": _text(order.get("deliveryType")),
        "article": article,
        "skus": skus,
        "nm_id": order.get("nmId"),
        "chrt_id": order.get("chrtId"),
        "currency_code": order.get("convertedCurrencyCode") or order.get("currencyCode"),
        "synced_at": synced_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "products": [product],
        "item_units": 1,
        "read_only": True,
    }


def synchronize_wildberries_orders(client, store, synced_at=None):
    rows = client.get_new_orders()
    normalized = []
    errors = 0
    for row in rows:
        order = normalize_wildberries_order(row, synced_at=synced_at)
        if order is None:
            errors += 1
        else:
            normalized.append(order)
    result = store.upsert_wildberries(normalized)
    result.update({"received": len(rows), "errors": errors})
    return result
