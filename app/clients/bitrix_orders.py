import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

STATUS_NAMES = {
    "N": "Новый",
    "O": "Новый",
    "0": "Новый",
    "A": "Подтверждён",
    "T": "Не дозвонились",
    "D": "Собран",
    "C": "Отказ",
}


class BitrixReadOnlyError(RuntimeError):
    """A safe error that never includes credentials from a request URL."""


class BitrixOrdersReadOnlyClient:
    """Read-only client for the existing Tictactoy Bitrix order endpoints."""

    def __init__(
        self,
        orders_url=None,
        order_url=None,
        timeout=(3.05, 15),
        max_retries=3,
        token=None,
        session=None,
    ):
        if not orders_url or not order_url:
            raise BitrixReadOnlyError(
                "Bitrix order endpoints must be configured explicitly"
            )
        self.orders_url = str(orders_url)
        self.order_url = str(order_url)
        for endpoint in (self.orders_url, self.order_url):
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" or not parsed.hostname:
                raise BitrixReadOnlyError("Bitrix order endpoints must use HTTPS")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.headers = {"Accept": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.session = session or requests.Session()

    def _get_json(self, url, params=None):
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    headers=self.headers,
                )
            except requests.RequestException as error:
                if attempt >= self.max_retries:
                    raise BitrixReadOnlyError(
                        f"Bitrix request failed ({type(error).__name__})"
                    ) from None
                time.sleep(min(0.5 * (2 ** attempt), 4))
                continue

            if response.status_code in {401, 403}:
                raise BitrixReadOnlyError(
                    f"Bitrix access denied: HTTP {response.status_code}"
                )

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt >= self.max_retries:
                    raise BitrixReadOnlyError(
                        f"Bitrix temporary error: HTTP {response.status_code}"
                    )
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = (
                        min(float(retry_after), 10)
                        if retry_after
                        else 0.5 * (2 ** attempt)
                    )
                except (TypeError, ValueError):
                    delay = 0.5 * (2 ** attempt)
                time.sleep(max(0, min(delay, 10)))
                continue

            if response.status_code >= 400:
                raise BitrixReadOnlyError(
                    f"Bitrix request failed: HTTP {response.status_code}"
                )

            try:
                payload = response.json()
            except ValueError:
                raise BitrixReadOnlyError("Bitrix returned non-JSON data") from None

            if not isinstance(payload, dict):
                raise BitrixReadOnlyError("Bitrix returned an unexpected JSON structure")

            return payload

        raise BitrixReadOnlyError("Bitrix request failed")

    def get_latest_orders(self, limit=10):
        limit = max(1, min(int(limit), 10))
        payload = self._get_json(self.orders_url, params={"limit": limit})
        rows = payload.get("orders") or []

        if not isinstance(rows, list):
            raise BitrixReadOnlyError("Bitrix order list is not an array")

        selected = rows[:limit]
        orders = []
        request_count = 1

        for row in selected:
            if not isinstance(row, dict):
                continue
            order_id = row.get("id") or row.get("ID")
            if order_id in (None, ""):
                continue
            detail = self._get_json(self.order_url, params={"id": order_id})
            request_count += 1
            order = detail.get("order")
            if isinstance(order, dict):
                orders.append(order)

        return {
            "orders": orders,
            "requested_limit": limit,
            "server_list_count": len(rows),
            "server_honored_limit": len(rows) <= limit,
            "pagination": {
                "supported_by_current_endpoint": False,
                "note": (
                    "The current endpoint returned a fixed recent-order window and "
                    "did not expose total/next/offset metadata."
                ),
            },
            "request_count": request_count,
        }

    def list_orders(self, limit=50):
        """Return the bounded summary window without the N+1 detail requests."""
        limit = max(1, min(int(limit), 100))
        payload = self._get_json(self.orders_url, params={"limit": limit})
        rows = payload.get("orders") or payload.get("result") or []
        if isinstance(rows, dict):
            rows = rows.get("orders") or rows.get("items") or []
        if not isinstance(rows, list):
            raise BitrixReadOnlyError("Bitrix order list is not an array")
        return [row for row in rows[:limit] if isinstance(row, dict)]

    def get_order(self, order_id):
        payload = self._get_json(self.order_url, params={"id": str(order_id)})
        order = payload.get("order") or payload.get("result")
        if isinstance(order, dict) and isinstance(order.get("order"), dict):
            order = order["order"]
        if order is None:
            return None
        if not isinstance(order, dict):
            raise BitrixReadOnlyError("Bitrix order card has an unexpected structure")
        return order


def money(value):
    if value in (None, ""):
        return None
    try:
        return float(
            Decimal(str(value).replace(",", ".")).quantize(Decimal("0.01"))
        )
    except (InvalidOperation, TypeError, ValueError):
        return None


def number(value, default=None):
    if value in (None, ""):
        return default
    try:
        return float(Decimal(str(value).replace(",", ".")))
    except (InvalidOperation, TypeError, ValueError):
        return default


def first_value(data, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def property_values(order):
    result = {}
    raw_properties = order.get("properties") or order.get("PROPERTIES") or []
    if isinstance(raw_properties, dict):
        raw_properties = [
            {"code": key, "value": value} for key, value in raw_properties.items()
        ]
    for prop in raw_properties:
        if not isinstance(prop, dict):
            continue
        code = str(prop.get("code") or prop.get("CODE") or "").strip().upper()
        name = str(prop.get("name") or prop.get("NAME") or "").strip().upper()
        value = prop.get("value", prop.get("VALUE"))
        if value not in (None, "", [], {}):
            if code:
                result[code] = value
            if name:
                result["NAME:" + name] = value
    return result


def status_presentation(value):
    code = str(value if value not in (None, "") else "").strip()
    normalized = code.upper()
    return {
        "code": normalized or "UNKNOWN",
        "label": STATUS_NAMES.get(normalized, "Неизвестный статус"),
        "known": normalized in STATUS_NAMES,
    }


def normalize_order(order):
    if not isinstance(order, dict):
        return None
    properties = property_values(order)

    user = order.get("user") if isinstance(order.get("user"), dict) else {}
    items = []
    raw_items = (
        order.get("products") or order.get("PRODUCTS")
        or order.get("basket") or order.get("BASKET") or []
    )
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        quantity = number(first_value(raw_item, "quantity", "QUANTITY"), 1.0)
        sale_unit_price = money(first_value(raw_item, "price", "PRICE"))
        original_unit_price = money(
            first_value(raw_item, "base_price", "BASE_PRICE", "price_base", "PRICE_BASE")
        )
        purchase_unit_price = money(
            first_value(raw_item, "purchase_price", "PURCHASING_PRICE", "cost_price")
        )
        discount = money(
            first_value(raw_item, "discount", "DISCOUNT_PRICE", "discount_price")
        )
        if (
            discount is None
            and original_unit_price is not None
            and sale_unit_price is not None
        ):
            discount = max(0.0, original_unit_price - sale_unit_price)
        line_total = money(first_value(raw_item, "sum", "SUM", "total", "TOTAL"))
        line_total_source = "api" if line_total is not None else None
        if line_total is None and sale_unit_price is not None and quantity is not None:
            line_total = money(Decimal(str(sale_unit_price)) * Decimal(str(quantity)))
            line_total_source = "computed_sale_price_times_quantity"

        items.append({
            "bitrix_product_id": str(
                first_value(raw_item, "product_id", "PRODUCT_ID", "id", "ID") or ""
            ),
            "name": str(first_value(raw_item, "name", "NAME") or ""),
            "xml_id": str(first_value(raw_item, "xml_id", "XML_ID") or ""),
            "sku": str(
                first_value(
                    raw_item, "sku", "SKU", "article", "ARTICLE", "code", "CODE"
                )
                or ""
            ),
            "quantity": quantity,
            "original_unit_price": original_unit_price,
            "sale_unit_price": sale_unit_price,
            "discount_per_unit": discount,
            "line_total": line_total,
            "line_total_source": line_total_source,
            "purchase_unit_price": purchase_unit_price,
            "currency": str(
                first_value(raw_item, "currency", "CURRENCY")
                or first_value(order, "currency", "CURRENCY")
                or ""
            ),
            "image_url": str(first_value(
                raw_item, "image", "IMAGE", "image_url", "IMAGE_URL", "picture"
            ) or ""),
            "brand": str(first_value(raw_item, "brand", "BRAND") or ""),
            "category": str(first_value(raw_item, "category", "CATEGORY") or ""),
            "raw": raw_item,
        })

    status = status_presentation(first_value(order, "status", "STATUS_ID", "status_id"))
    products_total = money(sum(
        Decimal(str(item["line_total"])) for item in items
        if item.get("line_total") is not None
    )) if items and all(item.get("line_total") is not None for item in items) else None
    discount = money(first_value(
        order, "discount", "DISCOUNT", "discount_price", "DISCOUNT_PRICE"
    ))
    delivery_price = money(first_value(
        order, "delivery_price", "DELIVERY_PRICE", "price_delivery",
        "PRICE_DELIVERY", "shipment_price", "SHIPMENT_PRICE"
    ))
    delivery_price_source = "api" if delivery_price is not None else None
    if delivery_price is None:
        delivery_price = money(
            properties.get("DELIVERY_PRICE") or properties.get("PRICE_DELIVERY")
        )
        if delivery_price is not None:
            delivery_price_source = "property"
    total = money(first_value(order, "price", "PRICE", "sum", "SUM", "total"))
    comparable = None not in (products_total, total)
    if comparable and discount is None:
        discount = 0.0
    if comparable and delivery_price is None:
        derived_delivery = (
            Decimal(str(total)) - Decimal(str(products_total))
            + Decimal(str(discount or 0))
        ).quantize(Decimal("0.01"))
        if derived_delivery >= 0:
            delivery_price = money(derived_delivery)
            delivery_price_source = "derived_reconciled_remainder"
    reconciliation_complete = comparable and delivery_price is not None
    reconciliation_ok = bool(
        reconciliation_complete
        and money(Decimal(str(products_total)) - Decimal(str(discount or 0))
                  + Decimal(str(delivery_price))) == total
    )

    first_name = first_value(user, "first_name", "FIRST_NAME", "name", "NAME")
    last_name = first_value(user, "last_name", "LAST_NAME")
    user_name = " ".join(str(value).strip() for value in (first_name, last_name) if value)
    customer = (
        first_value(order, "customer", "client", "name", "USER_NAME")
        or user_name or properties.get("FIO")
    )
    address = (
        first_value(order, "address", "ADDRESS", "delivery_address")
        or properties.get("ADDRESS")
    )
    city = first_value(order, "city", "CITY") or properties.get("CITY")
    region = (
        first_value(order, "region", "region_name", "REGION", "REGION_NAME")
        or properties.get("REGION") or properties.get("REGION_NAME")
        or properties.get("LOCATION")
    )
    required = {
        "customer": customer,
        "phone": first_value(order, "phone", "PHONE") or user.get("phone") or properties.get("PHONE"),
        "items": items,
        "total": total,
        "status": status["known"],
    }
    missing = [key for key, value in required.items() if value in (None, "", [], False)]
    sync_state = "complete" if not missing else "partial"

    return {
        "external_id": str(first_value(order, "id", "ID") or ""),
        "external_source": "bitrix",
        "number": str(
            first_value(order, "number", "ACCOUNT_NUMBER", "account_number") or ""
        ),
        "created_at": first_value(order, "date", "DATE_INSERT", "date_insert"),
        "updated_at": first_value(order, "date_update", "DATE_UPDATE", "updated_at"),
        "status": status["code"],
        "status_name": status["label"],
        "status_known": status["known"],
        "customer": customer,
        "phone": (
            first_value(order, "phone")
            or user.get("phone")
            or properties.get("PHONE")
        ),
        "email": (
            first_value(order, "email")
            or user.get("email")
            or properties.get("EMAIL")
        ),
        "source": (
            first_value(order, "source", "SOURCE", "trade_binding", "AFFILIATE_ID")
            or properties.get("FROM2")
            or properties.get("FROM")
        ),
        "source_kind": (
            "technical_order_source"
            if first_value(order, "source", "SOURCE", "trade_binding", "AFFILIATE_ID")
            else "marketing_attribution"
            if properties.get("FROM2") or properties.get("FROM")
            else None
        ),
        "payment": first_value(order, "payment", "pay_system", "PAY_SYSTEM_NAME"),
        "paid": first_value(order, "paid", "PAYED"),
        "delivery": first_value(order, "delivery", "delivery_name", "DELIVERY_NAME"),
        "comment": first_value(
            order, "comment", "USER_DESCRIPTION", "COMMENTS", "REASON_CANCELED"
        ),
        "address": address,
        "city": city,
        "region": region,
        "tracking": (
            first_value(order, "tracking", "tracking_number", "TRACKING_NUMBER")
            or properties.get("TRACKING_NUMBER")
        ),
        "items": items,
        "products": [item["raw"] for item in items],
        "products_count": len(items),
        "products_total": products_total,
        "discount": discount,
        "delivery_price": delivery_price,
        "delivery_price_source": delivery_price_source,
        "total": total,
        "order_total": total,
        "calculation_complete": reconciliation_complete,
        "calculation_consistent": reconciliation_ok,
        "sync_state": sync_state,
        "sync_missing": missing,
        "currency": str(first_value(order, "currency", "CURRENCY") or ""),
    }


def normalize_key(value):
    return str(value or "").strip().casefold()


def _unique_index(products, keys):
    index = {}
    for product in products:
        for key in keys:
            value = normalize_key(product.get(key))
            if value:
                index.setdefault(value, []).append(product)
    return index


def match_items(items, products, legacy_mappings=None):
    legacy_mappings = legacy_mappings if isinstance(legacy_mappings, dict) else {}
    by_id = {str(product.get("id") or ""): product for product in products}
    by_xml = _unique_index(products, ("externalCode", "xml_id"))
    by_sku = _unique_index(products, ("article", "code", "sku"))
    by_name = _unique_index(products, ("name",))
    results = []

    for item in items:
        match = None
        method = None
        candidates = []
        legacy = legacy_mappings.get(str(item.get("bitrix_product_id") or ""))
        if isinstance(legacy, dict):
            match = by_id.get(str(legacy.get("moysklad_product_id") or ""))
            if match:
                method = "confirmed_legacy_mapping"

        for field, index, label in (
            ("xml_id", by_xml, "xml_id"),
            ("sku", by_sku, "sku"),
            ("name", by_name, "exact_name"),
        ):
            if match:
                break
            key = normalize_key(item.get(field))
            if not key:
                continue
            candidates = index.get(key, [])
            if len(candidates) == 1:
                match = candidates[0]
                method = label
                break
            if len(candidates) > 1:
                method = f"ambiguous_{label}"
                break

        results.append({
            **item,
            "match_status": "matched" if match else "requires_mapping",
            "match_method": method or "not_found",
            "moysklad_product_id": str(match.get("id") or "") if match else "",
            "moysklad_product_name": str(match.get("name") or "") if match else "",
            "candidate_count": len(candidates) if not match else 1,
        })

    return results
