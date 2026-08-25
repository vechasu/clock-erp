"""Deterministic local-only fixture server for Stage 2 visual comparison."""

import os
import sys
import tempfile
import base64
import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
PREVIEW_TEMP = tempfile.TemporaryDirectory(prefix="vechasu-stage2-preview-")
PREVIEW_ROOT = Path(PREVIEW_TEMP.name)
os.environ["CATALOG_DATABASE_PATH"] = str(PREVIEW_ROOT / "catalog.db")

from app.schema_migrations import apply_migrations  # noqa: E402

apply_migrations(PREVIEW_ROOT / "catalog.db", app_commit="stage2-preview")

from app import web  # noqa: E402
from app.catalog_db import CatalogDatabase  # noqa: E402
from app.services.excel_product_catalog import ExcelProductBatchService  # noqa: E402
from app.services.audit_journal import AuditJournal  # noqa: E402


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
        product_result(
            row,
            "Тестовый товар {:03d}".format(row),
            "PAGE-{:03d}".format(row),
            "Pagination",
            "Тест",
            row % 9,
            "P-{:03d}".format(row),
        )
        for row in range(6, 122)
    ] + [
        # Keep the named browser-test fixtures on the first (newest-first)
        # page when the catalog grows beyond the server page-size limit.
        product_result(5, "Футляр для часов", "CASE-01", "Vechasu", "Аксессуары", 0, "C-03"),
        product_result(4, "Ремешок Cordura Black", "STRAP-CB", "Vechasu", "Ремешки", 12, "B-12"),
        product_result(3, "Tissot PRX Powermatic 80", "T137.407", "Tissot", "Часы", 3, "A-05"),
        product_result(2, "Casio G-Shock GA-2100", "GA-2100-1A1", "Casio", "Часы", 7, "A-01"),
    ],
    "d" * 64,
    "stage2-preview.xlsx",
)
projected_products = web.get_excel_warehouse_items()

fixture_image_one = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)
fixture_image_two = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAIAAAAD/AP///ywAAAAAAQABAAACAUwAOw=="
)
fixture_local_image = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
fixture_local_digest = hashlib.sha256(fixture_local_image).hexdigest()
fixture_local_name = "product-{}.png".format(fixture_local_digest)
fixture_product_root = PREVIEW_ROOT / "product_images"
fixture_product_root.mkdir(parents=True, exist_ok=True)
(fixture_product_root / fixture_local_name).write_bytes(fixture_local_image)

real_product_image_store = web.ProductImageStore
web.ProductImageStore = lambda database=None: real_product_image_store(
    database, fixture_product_root
)
with CatalogDatabase(PREVIEW_ROOT / "catalog.db").transaction() as connection:
    product_ids = [
        int(item["id"]) for item in projected_products
        if float(item.get("stock") or 0) > 0
    ][:3]
    first_id = product_ids[0]
    connection.execute(
        "UPDATE catalog_excel_products SET bitrix_external_product_id = ?, "
        "bitrix_primary_image_url = ?, bitrix_thumbnail_url = ?, "
        "local_image_path = ?, local_image_source = 'bitrix', "
        "local_image_sha256 = ?, "
        "bitrix_gallery_json = ?, created_at = ? WHERE id = ?",
        (
            "204699", fixture_image_one, fixture_image_one,
            fixture_local_name, fixture_local_digest,
            '[{"original_url":"' + fixture_image_one + '"},'
            '{"original_url":"' + fixture_image_two + '"}]',
            "2099-01-01T00:00:00+00:00",
            first_id,
        ),
    )
    connection.execute(
        "UPDATE catalog_excel_products SET bitrix_thumbnail_url = ? "
        "WHERE id = ?",
        ("/static/missing-product-photo.jpg", product_ids[1]),
    )


def fixture_live_bitrix_product(product, force=False):
    return {
        "external_product_id": "204699",
        "images": [
            {"id": "10", "original_url": fixture_image_one, "kind": "preview"},
            {"id": "11", "original_url": fixture_image_two, "kind": "gallery"},
        ],
    }


web._live_bitrix_product = fixture_live_bitrix_product
web.persist_live_bitrix_gallery = lambda product, live: product

warehouse_items = [
    {
        **item,
        "code": item.get("article") or "",
        "has_images": False,
    }
    for item in projected_products
]
warehouse_items[0]["local_image_url"] = "/product-images/{}".format(
    fixture_local_name
)
warehouse_items[0]["thumbnail_url"] = warehouse_items[0]["local_image_url"]

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
    return web.decorate_sale_status({
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
        "article": str(product.get("article") or ""),
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
        "order_status_label": "Завершён",
        "is_cancelled": False,
        "cancelled_at": "",
        "cancellation_reason": "",
        "cancellation_comment": "",
        "cancellation_quantity": quantity,
        "cancellation_safe": True,
        "cancellation_has_movements": True,
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
    })


long_article_product = {
    **warehouse_items[0],
    "article": "КИРИЛЛИЧЕСКИЙ-АРТИКУЛ-ОЧЕНЬ-ДЛИННЫЙ-2026",
}
zero_article_product = {
    **warehouse_items[2],
    "article": "0",
    "code": "WB-BARCODE-8831",
}
empty_article_product = {
    **warehouse_items[1],
    "article": "",
}

preview_sales = [
    sale_record("preview-sale-1", "ORDER-1042", long_article_product, "Tictactoy", "2026-07-30T10:30:45", 1, 14990),
    sale_record("preview-sale-2", "WB-8831", zero_article_product, "Wildberries", "2026-07-29T11:40:50", 2, 1490),
    sale_record("preview-sale-3", "AMZ-2201", warehouse_items[1], "Amazon", "2026-07-28T12:50:55", 1, 24990),
    sale_record("preview-sale-4", "ORDER-EMPTY", empty_article_product, "Tictactoy", "2026-07-27T13:05:59", 1, 5000),
]

for index, source in enumerate(("Tictactoy", "Wildberries", "Amazon"), start=1):
    cancelled = sale_record(
        "preview-cancelled-{}".format(index),
        "CANCELLED-{}".format(index),
        warehouse_items[index - 1],
        source,
        "2026-08-0{}T12:00:00".format(index),
        1,
        1000,
    )
    cancelled.update({
        "order_status": "cancelled",
        "order_status_label": "Отменён",
        "is_cancelled": True,
        "cancelled_at": "2026-08-04T12:00:00",
        "cancellation_reason": "Дубль",
        "cancellation_quantity": 0,
        "return_status": "returned",
    })
    web.decorate_sale_status(cancelled)
    preview_sales.append(cancelled)

refusal = sale_record(
    "preview-refusal",
    "REFUSAL-1",
    warehouse_items[0],
    "Tictactoy",
    "2026-07-31T13:00:00",
    1,
    1200,
)
refusal.update({
    "order_status": "cancelled",
    "is_cancelled": True,
    "cancelled_at": "2026-07-31T13:05:00",
    "cancellation_reason": "Клиент отказался",
    "return_status": "returned",
})
web.decorate_sale_status(refusal)
preview_sales.append(refusal)

preview_sales.extend([
    sale_record(
        "preview-sale-{}".format(index),
        "PAGE-{:04d}".format(index),
        warehouse_items[index % len(warehouse_items)],
        ("Tictactoy", "Wildberries", "Amazon")[index % 3],
        "2026-06-{:02d}T09:00:00".format((index % 28) + 1),
        1,
        1000 + index,
    )
    for index in range(5, 125)
])

preview_receipts.extend([
    {
        **preview_receipts[0],
        "id": "preview-receipt-{}".format(index),
        "number": "PAGE-{:04d}".format(index),
        "created_at": "2026-06-{:02d}T09:00:00".format((index % 28) + 1),
        "receipt_date": "2026-06-{:02d}".format((index % 28) + 1),
        "note": "Страница {:03d}".format(index),
    }
    for index in range(2, 122)
])
preview_receipts[1].update({
    "receipt_date": "2026-07-29",
    "created_at": "2026-07-29T09:00:00",
    "product_id": str(warehouse_items[1]["id"]),
    "product_name": warehouse_items[1]["name"],
})
preview_receipts[2].update({
    "receipt_date": "2026-07-28",
    "created_at": "2026-07-28T09:00:00",
    "product_id": str(warehouse_items[2]["id"]),
    "product_name": warehouse_items[2]["name"],
})

web.CATALOG_TAXONOMY_PATH = PREVIEW_ROOT / "catalog_taxonomy.json"
web.get_warehouse_items = lambda *args, **kwargs: [dict(item) for item in warehouse_items]


def fixture_excel_warehouse_items(*args, **kwargs):
    catalog = kwargs.get("catalog")
    if not catalog:
        return [dict(item) for item in warehouse_items]
    by_id = {str(item["id"]): item for item in warehouse_items}
    return [
        dict(by_id[str(item["id"])])
        for item in catalog.get("items", [])
        if str(item.get("id")) in by_id
    ]


web.get_excel_warehouse_items = fixture_excel_warehouse_items
web.load_receipts = lambda: [dict(receipt) for receipt in preview_receipts]
web.api_receipt_records = lambda: tuple(
    dict(receipt) for receipt in preview_receipts
)
web.build_sales_report_records = lambda warehouse_items=None: [
    dict(sale) for sale in preview_sales
]
web.app.config.update(TESTING=True, AUTH_TESTING=False)

preview_journal = AuditJournal(CatalogDatabase(PREVIEW_ROOT / "catalog.db"))
for index in range(42):
    entity_type = ("product", "sale", "receipt")[index % 3]
    source = ("Tictactoy", "Wildberries", "Amazon")[index % 3]
    entity_id = (
        str(warehouse_items[index % len(warehouse_items)]["id"])
        if entity_type == "product"
        else "preview-sale-{}".format(index)
        if entity_type == "sale"
        else "preview-receipt-{}".format(index)
    )
    label = (
        warehouse_items[index % len(warehouse_items)]["name"]
        if entity_type == "product"
        else "Продажа #PAGE-{:04d}".format(index)
        if entity_type == "sale"
        else "Приход #PAGE-{:04d}".format(index)
    )
    field = "price" if entity_type == "product" else "status" if entity_type == "sale" else "quantity"
    preview_journal.record(
        entity_type, entity_id,
        "created" if index % 7 == 0 else "updated",
        label,
        warehouse_items[index % len(warehouse_items)].get("article", "")
        if entity_type == "product" else source if entity_type == "sale" else "",
        before={field: index}, after={field: index + 1},
        metadata={
            "article": "KLOK-01" if entity_type == "product" else "",
            "number": "PAGE-{:04d}".format(index),
            "text_snapshot": "Тестовый комментарий {}".format(index),
        },
        actor_id="preview-user-{}".format(index % 2),
        actor_name="Максим" if index % 2 == 0 else "Анна",
        actor_type="user",
        occurred_at="2026-08-{:02d}T{:02d}:14:{:02d}+00:00".format(
            11 - (index // 18), 21 - (index % 12), index % 60
        ),
        status="completed" if entity_type == "sale" else "",
        source=source if entity_type == "sale" else "",
    )


if __name__ == "__main__":
    web.app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PREVIEW_PORT", "5050")),
        debug=False,
        use_reloader=False,
    )
