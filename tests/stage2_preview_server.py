"""Deterministic local-only fixture server for Stage 2 visual comparison."""

import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
PREVIEW_TEMP = tempfile.TemporaryDirectory(prefix="vechasu-stage2-preview-")
PREVIEW_ROOT = Path(PREVIEW_TEMP.name)
os.environ["CATALOG_DATABASE_PATH"] = str(PREVIEW_ROOT / "catalog.db")

from app import web  # noqa: E402
from app.catalog_db import CatalogDatabase  # noqa: E402
from app.services.excel_product_catalog import ExcelProductBatchService  # noqa: E402


def product_result(row, name, article, brand, category, stock, cell):
    return {
        "excel_row": row,
        "excel_name": name,
        "excel_brand": brand,
        "excel_article": article,
        "article_quality": "code_like",
        "category": category,
        "stock": float(stock),
        "stock_valid": True,
        "cell": cell,
        "product_id": None,
        "match_status": "not_found",
        "match_method": "preview-fixture",
        "confidence": 0,
        "alternatives": [],
    }


ExcelProductBatchService(CatalogDatabase(PREVIEW_ROOT / "catalog.db")).apply(
    [
        product_result(2, "Casio G-Shock GA-2100", "GA-2100-1A1", "Casio", "Часы", 7, "A-01"),
        product_result(3, "Tissot PRX Powermatic 80", "T137.407", "Tissot", "Часы", 3, "A-05"),
        product_result(4, "Ремешок Cordura Black", "STRAP-CB", "Vechasu", "Ремешки", 12, "B-12"),
        product_result(5, "Футляр для часов", "CASE-01", "Vechasu", "Аксессуары", 0, "C-03"),
    ],
    "d" * 64,
    "stage2-preview.xlsx",
)

projected_products = web.get_excel_warehouse_items()
warehouse_items = [
    {
        **item,
        "code": item.get("article") or "",
        "has_images": False,
    }
    for item in projected_products
]
warehouse_items[0]["thumbnail_url"] = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)

receipt_positions = [
    {
        "product_id": str(warehouse_items[0]["id"]),
        "product_name": warehouse_items[0]["name"],
        "article": warehouse_items[0]["article"],
        "code": warehouse_items[0]["code"],
        "brand": warehouse_items[0]["brand"],
        "category": warehouse_items[0]["category"],
        "cell": warehouse_items[0]["cell"],
        "quantity": 2,
        "purchase_price": 9500,
        "line_total": 19000,
        "stock_before": 5,
        "stock_after": 7,
    },
]
preview_receipts = [
    {
        "id": "preview-receipt-1",
        "number": "PR-2026-0042",
        "created_at": "2026-07-30 10:30",
        "receipt_date": "2026-07-30",
        "brand": "Casio",
        "category": "Часы",
        "product_id": str(warehouse_items[0]["id"]),
        "product_name": warehouse_items[0]["name"],
        "quantity": 2,
        "purchase_price": 9500,
        "note": "Поставка основного склада",
        "status": "posted",
        "positions": receipt_positions,
        "positions_count": 1,
        "total_quantity": 2,
        "total_amount": 19000,
        "moysklad_document_id": "preview-enter-1",
        "moysklad_document_name": "ОП-0042",
        "moysklad_document_url": "",
    },
]


def sale_record(identifier, order_number, product, source, date, quantity, unit_price):
    total = quantity * unit_price
    return {
        "id": identifier,
        "sale_type": "manual",
        "sale_type_label": "Ручная",
        "is_manual": True,
        "inventory_managed": True,
        "created_at": date,
        "source": source,
        "source_key": web.normalize_sales_source_key(source),
        "order_number": order_number,
        "product_id": str(product["id"]),
        "product_name": product["name"],
        "barcode": product.get("code") or "",
        "brand": product["brand"],
        "category": product["category"],
        "quantity_value": quantity,
        "quantity_display": str(quantity),
        "net_quantity_value": quantity,
        "returned_quantity": 0,
        "return_available_quantity": quantity,
        "returned_at": "",
        "return_reason": "",
        "unit_price": unit_price,
        "unit_price_display": "{} ₽".format(unit_price),
        "total_amount": total,
        "gross_total_amount": total,
        "total_amount_display": "{} ₽".format(total),
        "returned_amount": 0,
        "order_status": "completed",
        "order_status_label": "Выполнен",
        "is_cancelled": False,
        "cancelled_at": "",
        "track_number": "TRACK-{}".format(order_number[-2:]),
        "delivery_method": "СДЭК",
        "delivery_cost": 450,
        "delivery_cost_display": "450 ₽",
        "region": "Москва",
        "city": "Москва",
        "note": "Оплачено",
        "recipient": "+7 900 000-00-00",
        "recipient_name": "Покупатель",
        "payment_method": "Карта",
        "commission": "",
        "commission_amount": 0,
        "commission_display": "0 ₽",
        "country": "Россия",
        "delivery_address": "Москва",
        "platform": "",
        "invoice_number": "",
        "sticker_number": "",
    }


preview_sales = [
    sale_record("preview-sale-1", "ORDER-1042", warehouse_items[0], "Tictactoy", "2026-07-30", 1, 14990),
    sale_record("preview-sale-2", "WB-8831", warehouse_items[2], "Wildberries", "2026-07-29", 2, 1490),
]

web.CATALOG_TAXONOMY_PATH = PREVIEW_ROOT / "catalog_taxonomy.json"
web.get_warehouse_items = lambda *args, **kwargs: [dict(item) for item in warehouse_items]
web.get_excel_warehouse_items = lambda *args, **kwargs: [
    dict(item) for item in warehouse_items
]
web.load_receipts = lambda: [dict(receipt) for receipt in preview_receipts]
web.build_sales_report_records = lambda warehouse_items=None: [
    dict(sale) for sale in preview_sales
]
web.app.config.update(TESTING=True, AUTH_TESTING=False)


if __name__ == "__main__":
    web.app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PREVIEW_PORT", "5050")),
        debug=False,
        use_reloader=False,
    )
