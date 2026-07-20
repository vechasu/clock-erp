"""Read-only access and normalization for a Bitrix product catalog export."""

import logging
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
PURCHASE_PRICE_MARKERS = {"purchase", "purchasing", "cost", "закупочная", "себестоимость"}


class BitrixCatalogReadOnlyError(RuntimeError):
    """An error whose text never includes credentials or URL query strings."""


def _safe_url(url):
    parsed = urlsplit(str(url or ""))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _first(data, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _text(value):
    return str(value or "").strip()


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(Decimal(str(value).replace(" ", "").replace(",", ".")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _boolean(value, default=False):
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    return str(value).strip().casefold() in {"1", "y", "yes", "true", "да"}


def normalize_category(raw):
    raw = raw if isinstance(raw, dict) else {}
    path = _first(raw, "path", "PATH", "path_names", "SECTION_PATH") or []
    if isinstance(path, str):
        path = [part.strip() for part in path.replace("\\", "/").split("/") if part.strip()]
    normalized_path = []
    path_items = []
    for part in path if isinstance(path, list) else []:
        if isinstance(part, dict):
            part_name = _text(_first(part, "name", "NAME"))
            if part_name:
                normalized_path.append(part_name)
                path_items.append({
                    "id": _text(_first(part, "id", "ID")),
                    "name": part_name,
                })
            continue
        if _text(part):
            normalized_path.append(_text(part))
    name = _text(_first(raw, "name", "NAME"))
    if name and (not normalized_path or normalized_path[-1].casefold() != name.casefold()):
        normalized_path.append(name)
    return {
        "id": _text(_first(raw, "id", "ID", "section_id", "IBLOCK_SECTION_ID")),
        "xml_id": _text(_first(raw, "xml_id", "XML_ID")),
        "name": name,
        "code": _text(_first(raw, "code", "CODE", "symbolic_code")),
        "parent_id": _text(_first(raw, "parent_id", "PARENT_ID", "IBLOCK_SECTION_ID")),
        "depth": int(_number(_first(raw, "depth", "DEPTH_LEVEL")) or max(len(normalized_path) - 1, 0)),
        "sort": int(_number(_first(raw, "sort", "SORT")) or 500),
        "active": _boolean(_first(raw, "active", "ACTIVE"), True),
        "path": normalized_path,
        "path_items": path_items,
    }


def normalize_property(raw):
    if not isinstance(raw, dict):
        return {
            "id": "", "code": "", "name": "", "type": "string",
            "value": raw, "display_value": raw, "multiple": isinstance(raw, list),
        }
    value = _first(raw, "value", "VALUE", "values", "VALUES")
    display = _first(raw, "display_value", "DISPLAY_VALUE", "value_text", "VALUE_ENUM")
    multiple = _boolean(_first(raw, "multiple", "MULTIPLE"), isinstance(value, list))
    if multiple and value is not None and not isinstance(value, list):
        value = [value]
    if display is None:
        display = value
    return {
        "id": _text(_first(raw, "id", "ID")),
        "code": _text(_first(raw, "code", "CODE")),
        "name": _text(_first(raw, "name", "NAME")),
        "type": _text(_first(raw, "type", "TYPE", "PROPERTY_TYPE")) or "string",
        "value": value,
        "display_value": display,
        "multiple": multiple,
        "enum_id": _first(raw, "enum_id", "ENUM_ID", "VALUE_ENUM_ID"),
        "sort": int(_number(_first(raw, "sort", "SORT")) or 500),
    }


def normalize_image(raw, base_url="", order=0, kind="gallery"):
    raw = raw if isinstance(raw, dict) else {"url": raw}
    source_url = _text(_first(raw, "original_url", "ORIGINAL_URL", "url", "URL", "src", "SRC"))
    return {
        "id": _text(_first(raw, "id", "ID", "file_id", "FILE_ID")),
        "kind": _text(_first(raw, "kind", "type", "TYPE")) or kind,
        "url": urljoin(base_url, source_url) if source_url else "",
        "original_url": urljoin(base_url, source_url) if source_url else "",
        "filename": _text(_first(raw, "filename", "FILE_NAME", "name")) or source_url.rsplit("/", 1)[-1],
        "order": int(_number(_first(raw, "order", "SORT")) or order),
        "mime_type": _text(_first(raw, "mime_type", "CONTENT_TYPE")),
        "width": int(_number(_first(raw, "width", "WIDTH")) or 0),
        "height": int(_number(_first(raw, "height", "HEIGHT")) or 0),
        "file_size": int(_number(_first(raw, "file_size", "FILE_SIZE")) or 0),
        "is_primary": _boolean(_first(raw, "is_primary", "IS_PRIMARY"), False),
    }


def normalize_price(raw):
    raw = raw if isinstance(raw, dict) else {"value": raw}
    name = _text(_first(raw, "type_name", "name", "NAME", "price_name", "CATALOG_GROUP_NAME"))
    code = _text(_first(raw, "type_code", "type", "code", "CODE", "price_type_id", "CATALOG_GROUP_ID"))
    role = _text(_first(raw, "role", "ROLE"))
    marker_text = " ".join((name, code, role)).casefold()
    is_purchase = _boolean(_first(raw, "is_purchase", "IS_PURCHASE")) or any(
        marker in marker_text for marker in PURCHASE_PRICE_MARKERS
    )
    return {
        "type_id": _text(_first(raw, "type_id", "ID", "CATALOG_GROUP_ID")),
        "type_code": code,
        "type_name": name,
        "role": role or ("purchase" if is_purchase else "base" if _boolean(raw.get("is_base")) else "sale"),
        "value": _number(_first(raw, "value", "amount", "price", "PRICE")),
        "base_value": _number(_first(raw, "base_value", "base_price", "BASE_PRICE")),
        "old_value": _number(_first(raw, "old_value", "old_amount", "old_price", "OLD_PRICE")),
        "old_value_source": _text(_first(raw, "old_value_source", "old_amount_source")),
        "discount": _number(_first(raw, "discount", "DISCOUNT")),
        "currency": _text(_first(raw, "currency", "CURRENCY")),
        "is_purchase": is_purchase,
    }


def select_sale_price(prices):
    """Select a sale price and never silently use a purchase/cost price."""
    sale_prices = [price for price in prices if not price.get("is_purchase")]
    for role in ("sale", "base", "retail"):
        for price in sale_prices:
            if _text(price.get("role")).casefold() == role and price.get("value") is not None:
                return price
    return next((price for price in sale_prices if price.get("value") is not None), None)


def _normalize_images(raw_product, base_url):
    images = []
    seen = set()
    sources = (
        ("preview", _first(raw_product, "preview_image", "PREVIEW_PICTURE")),
        ("detail", _first(raw_product, "detail_image", "DETAIL_PICTURE")),
    )
    for kind, value in sources:
        if value:
            image = normalize_image(value, base_url, len(images), kind)
            if image["url"] and image["url"] not in seen:
                seen.add(image["url"])
                images.append(image)
    gallery = _first(raw_product, "images", "IMAGES", "gallery", "MORE_PHOTO") or []
    if not isinstance(gallery, list):
        gallery = [gallery]
    for value in gallery:
        image = normalize_image(value, base_url, len(images), "gallery")
        if image["url"] and image["url"] not in seen:
            seen.add(image["url"])
            images.append(image)
    return images


def normalize_product(raw, base_url=""):
    raw = raw if isinstance(raw, dict) else {}
    properties_raw = _first(raw, "properties", "PROPERTIES") or []
    if isinstance(properties_raw, dict):
        properties_raw = [dict(value, code=key) if isinstance(value, dict) else {"code": key, "value": value}
                          for key, value in properties_raw.items()]
    properties = [normalize_property(item) for item in properties_raw if item is not None]
    prices_raw = _first(raw, "prices", "PRICES") or []
    if not isinstance(prices_raw, list):
        prices_raw = [prices_raw]
    prices = [normalize_price(item) for item in prices_raw]
    offers_raw = _first(raw, "offers", "OFFERS", "sku_offers") or []
    if not isinstance(offers_raw, list):
        offers_raw = [offers_raw]
    categories_raw = _first(raw, "categories", "CATEGORIES") or []
    if not isinstance(categories_raw, list):
        categories_raw = [categories_raw]
    categories = [normalize_category(item) for item in categories_raw if isinstance(item, dict)]
    primary_category_id = _text(_first(raw, "primary_category_id", "PRIMARY_CATEGORY_ID"))
    category = next(
        (item for item in categories if item["id"] == primary_category_id),
        categories[0] if categories else normalize_category(
            _first(raw, "category", "CATEGORY", "section", "SECTION") or {}
        ),
    )
    if primary_category_id:
        categories.sort(key=lambda item: 0 if item["id"] == primary_category_id else 1)
    brand = _text(_first(raw, "brand", "BRAND"))
    if not brand:
        for prop in properties:
            if prop["code"].casefold() in {"brand", "manufacturer", "filter_brand"}:
                brand = _text(prop["display_value"])
                break
    product = {
        "external_source": "bitrix",
        "external_product_id": _text(_first(raw, "id", "ID", "product_id", "PRODUCT_ID")),
        "external_offer_id": _text(_first(raw, "offer_id", "OFFER_ID")),
        "external_xml_id": _text(_first(raw, "xml_id", "XML_ID", "external_code")),
        "external_sku": _text(_first(raw, "sku", "SKU", "article", "ARTICLE")),
        "code": _text(_first(raw, "code", "CODE", "symbolic_code")),
        "url": urljoin(base_url, _text(_first(raw, "url", "source_url", "URL", "detail_page_url", "DETAIL_PAGE_URL"))),
        "name": _text(_first(raw, "name", "NAME")),
        "preview_text": _text(_first(raw, "preview_text", "PREVIEW_TEXT")),
        "detail_text": _text(_first(raw, "detail_text", "DETAIL_TEXT", "description")),
        "preview_text_type": _text(_first(raw, "preview_text_type", "PREVIEW_TEXT_TYPE")) or "text",
        "detail_text_type": _text(_first(raw, "detail_text_type", "description_type", "DETAIL_TEXT_TYPE")) or "text",
        "active": _boolean(_first(raw, "active", "ACTIVE"), True),
        "created_at": _first(raw, "created_at", "DATE_CREATE"),
        "updated_at": _first(raw, "updated_at", "TIMESTAMP_X", "DATE_UPDATE"),
        "sort": int(_number(_first(raw, "sort", "SORT")) or 500),
        "unit": _text(_first(raw, "unit", "MEASURE_NAME", "measure")),
        "category": category,
        "categories": categories,
        "primary_category_id": primary_category_id,
        "brand": brand,
        "properties": properties,
        "images": _normalize_images(raw, base_url),
        "prices": prices,
        "offers": [],
        "stock": _number(_first(raw, "stock", "quantity", "CATALOG_QUANTITY")),
        "available_quantity": _number(_first(raw, "available_quantity", "AVAILABLE_QUANTITY")),
        "reserve": _number(_first(raw, "reserve", "reserved_quantity", "RESERVE")),
        "warehouse_stocks": _first(raw, "warehouse_stocks", "STORES") or [],
        "seo": _first(raw, "seo", "SEO") or {},
    }
    for offer in offers_raw:
        if not isinstance(offer, dict):
            continue
        normalized_offer = normalize_product(
            dict(
                offer,
                category=offer.get("category") or category,
                brand=offer.get("brand") or brand,
            ),
            base_url,
        )
        normalized_offer["external_offer_id"] = normalized_offer["external_product_id"]
        product["offers"].append(normalized_offer)
    product["sale_price"] = select_sale_price(prices)
    return product


def normalize_key(value):
    return " ".join(_text(value).casefold().split())


def _unique_index(products, keys):
    result = {}
    for product in products:
        for key in keys:
            value = normalize_key(product.get(key))
            if value:
                result.setdefault(value, []).append(product)
    return result


def match_product(product, vechasu_products, confirmed_mappings=None):
    confirmed_mappings = confirmed_mappings if isinstance(confirmed_mappings, dict) else {}
    by_id = {str(row.get("id") or ""): row for row in vechasu_products}
    confirmed_key = f"bitrix:{product.get('external_product_id', '')}"
    confirmed = confirmed_mappings.get(confirmed_key) or confirmed_mappings.get(product.get("external_product_id", ""))
    if isinstance(confirmed, dict):
        candidate = by_id.get(str(confirmed.get("moysklad_product_id") or confirmed.get("vechasu_product_id") or ""))
        if candidate:
            return {"status": "matched", "method": "confirmed_mapping", "candidate_count": 1, "product": candidate}

    indexes = (
        (product.get("external_xml_id"), _unique_index(vechasu_products, ("externalCode", "xml_id")), "xml_id"),
        (product.get("external_sku"), _unique_index(vechasu_products, ("article", "sku")), "sku"),
        (product.get("name"), _unique_index(vechasu_products, ("name",)), "exact_name"),
    )
    for value, index, method in indexes:
        key = normalize_key(value)
        if not key:
            continue
        candidates = index.get(key, [])
        if len(candidates) == 1:
            return {"status": "matched", "method": method, "candidate_count": 1, "product": candidates[0]}
        if len(candidates) > 1:
            return {"status": "ambiguous", "method": method, "candidate_count": len(candidates), "product": None}
    return {"status": "new", "method": "not_found", "candidate_count": 0, "product": None}


class BitrixCatalogReadOnlyClient:
    """Read-only client for the protected tictactoy.ru catalog endpoint."""

    def __init__(self, export_url, token=None, timeout=(3.05, 20), max_retries=3,
                 session=None, logger=None):
        if not export_url:
            raise ValueError("BITRIX_CATALOG_URL is required")
        self.export_url = export_url
        self.base_url = f"{urlsplit(export_url).scheme}://{urlsplit(export_url).netloc}/"
        self.token = token
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.session = session or requests.Session()
        self.logger = logger or logging.getLogger(__name__)
        self.request_count = 0

    def _get_json(self, params):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(self.max_retries + 1):
            try:
                self.request_count += 1
                response = self.session.get(self.export_url, params=params, headers=headers,
                                            timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as error:
                if attempt >= self.max_retries:
                    self.logger.error("Bitrix catalog request failed for %s: %s",
                                      _safe_url(self.export_url), type(error).__name__)
                    raise BitrixCatalogReadOnlyError("Bitrix catalog request timed out") from error
                time.sleep(min(0.5 * (2 ** attempt), 4))
                continue
            if response.status_code in {401, 403}:
                raise BitrixCatalogReadOnlyError(f"Bitrix catalog access denied: HTTP {response.status_code}")
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt >= self.max_retries:
                    raise BitrixCatalogReadOnlyError(f"Bitrix catalog temporary error: HTTP {response.status_code}")
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 0.5 * (2 ** attempt)
                except (TypeError, ValueError):
                    delay = 0.5 * (2 ** attempt)
                time.sleep(max(0, min(delay, 10)))
                continue
            if response.status_code >= 400:
                raise BitrixCatalogReadOnlyError(f"Bitrix catalog request failed: HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as error:
                raise BitrixCatalogReadOnlyError("Bitrix catalog returned non-JSON data") from error
            if not isinstance(payload, dict):
                raise BitrixCatalogReadOnlyError("Bitrix catalog returned an unexpected JSON structure")
            return payload
        raise BitrixCatalogReadOnlyError("Bitrix catalog request failed")

    def get_meta(self):
        return self._get_json({"mode": "meta"})

    def get_products_page(self, page=1, limit=100, updated_from=None, include_inactive=False):
        params = {"page": max(1, int(page)), "limit": max(1, min(int(limit), 200))}
        if updated_from:
            params["updated_from"] = updated_from
        if include_inactive:
            params["include_inactive"] = 1
        payload = self._get_json(params)
        rows = payload.get("products") or payload.get("items") or []
        if not isinstance(rows, list):
            raise BitrixCatalogReadOnlyError("Bitrix catalog products is not an array")
        return {
            "products": [normalize_product(row, self.base_url) for row in rows if isinstance(row, dict)],
            "categories": [normalize_category(row) for row in payload.get("categories", []) if isinstance(row, dict)],
            "total": int(_number(payload.get("total")) or len(rows)),
            "page": int(_number(payload.get("page")) or params["page"]),
            "limit": int(_number(payload.get("limit")) or params["limit"]),
            "has_more": _boolean(payload.get("has_more"), False),
            "total_pages": int(_number(payload.get("total_pages")) or 0),
            "next_page": int(_number(payload.get("next_page")) or 0) or None,
            "api_version": _text(payload.get("api_version")),
        }

    def iter_products(self, limit=100, max_items=None, updated_from=None, include_inactive=False):
        page = 1
        yielded = 0
        while True:
            result = self.get_products_page(
                page=page,
                limit=limit,
                updated_from=updated_from,
                include_inactive=include_inactive,
            )
            for product in result["products"]:
                if max_items is not None and yielded >= max_items:
                    return
                yielded += 1
                yield product
            if not result["has_more"] or not result["products"]:
                return
            page += 1

    @staticmethod
    def get_categories(page_payload):
        return page_payload.get("categories", [])

    @staticmethod
    def get_properties(product):
        return product.get("properties", [])

    @staticmethod
    def get_offers(product):
        return product.get("offers", [])

    @staticmethod
    def get_prices(product):
        return product.get("prices", [])

    @staticmethod
    def get_image_links(product):
        return [image.get("original_url") for image in product.get("images", []) if image.get("original_url")]
