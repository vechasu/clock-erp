"""Build a read-only WB-to-ERP product matching report."""

from __future__ import print_function

import sqlite3
from collections import defaultdict
from pathlib import Path


def _text(value):
    return str(value or "").strip()


def _key(value):
    return _text(value).casefold()


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def load_erp_product_index(database):
    """Read ERP identities without initializing or migrating the database."""
    path = Path(database).resolve()
    connection = sqlite3.connect(
        "file:{}?mode=ro".format(path), uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT p.id, p.excel_name_raw name, "
            "COALESCE(p.excel_article,'') article, "
            "COALESCE(cp.barcode,'') barcode "
            "FROM catalog_excel_products p "
            "LEFT JOIN catalog_products cp "
            "ON cp.id=p.bitrix_catalog_product_id "
            "WHERE p.deleted_at IS NULL AND p.active=1 ORDER BY p.id"
        ).fetchall()
    finally:
        connection.close()
    index = defaultdict(list)
    for row in rows:
        product = {
            "id": str(row["id"]),
            "name": _text(row["name"]),
            "article": _text(row["article"]),
            "barcode": _text(row["barcode"]),
        }
        for identity in (product["article"], product["barcode"]):
            if _key(identity):
                index[_key(identity)].append(product)
    return index


def build_matching_report(prices, statistics_stocks, fbs_orders, erp_index):
    """Combine WB read models and match exact vendorCode/article/barcode values."""
    products = {}

    def product_for(nm_id):
        key = _text(nm_id)
        if not key:
            return None
        return products.setdefault(key, {
            "nmID": nm_id,
            "vendorCode": "",
            "name": "",
            "price": None,
            "stock": None,
            "identities": set(),
        })

    for row in prices or ():
        item = product_for(row.get("nmID") or row.get("nmId"))
        if item is None:
            continue
        item["vendorCode"] = _text(row.get("vendorCode"))
        item["identities"].add(_key(item["vendorCode"]))
        sizes = row.get("sizes") if isinstance(row.get("sizes"), list) else []
        discounted = [
            _number(size.get("discountedPrice"))
            for size in sizes if isinstance(size, dict)
        ]
        prices_found = [value for value in discounted if value is not None]
        item["price"] = min(prices_found) if prices_found else None

    stock_totals = defaultdict(float)
    for row in statistics_stocks or ():
        item = product_for(row.get("nmId") or row.get("nmID"))
        if item is None:
            continue
        vendor_code = _text(row.get("supplierArticle") or row.get("vendorCode"))
        if vendor_code and not item["vendorCode"]:
            item["vendorCode"] = vendor_code
        for identity in (vendor_code, row.get("barcode")):
            if _key(identity):
                item["identities"].add(_key(identity))
        if not item["name"]:
            item["name"] = _text(row.get("name") or row.get("object"))
        amount = _number(row.get("quantity"))
        if amount is not None:
            stock_totals[_text(item["nmID"])] += float(amount)

    for row in fbs_orders or ():
        item = product_for(row.get("nmId") or row.get("nmID"))
        if item is None:
            continue
        article = _text(row.get("article"))
        if article and not item["vendorCode"]:
            item["vendorCode"] = article
        for identity in [article] + list(row.get("skus") or []):
            if _key(identity):
                item["identities"].add(_key(identity))

    report = []
    for key in sorted(products, key=lambda value: int(value) if value.isdigit() else value):
        item = products[key]
        if key in stock_totals:
            item["stock"] = _number(stock_totals[key])
        matches = {}
        for identity in item.pop("identities"):
            for product in erp_index.get(identity, ()):
                matches[product["id"]] = product
        if len(matches) == 1:
            status = "matched"
            erp_id = next(iter(matches))
        elif len(matches) > 1:
            status = "ambiguous"
            erp_id = None
        else:
            status = "not_found"
            erp_id = None
        item.update({"erp_product_id": erp_id, "status": status})
        report.append(item)
    return report
