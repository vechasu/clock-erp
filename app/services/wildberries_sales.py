"""Prepare saved WB orders for the shared transactional inventory service.

No external clients or order synchronization belong in this adapter.
"""

from app.services.sales_inventory import SalesInventoryError, sale_now_iso
from app.services.sale_pricing import decimal_money


class WildberriesSales:
    def __init__(self, inventory, resolve_products):
        self.inventory = inventory
        self.resolve_products = resolve_products

    def find_sale(self, order_id):
        if not self.inventory.exists():
            return None
        with self.inventory.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM erp_sales WHERE source = 'wildberries' "
                "AND external_order_id = ? ORDER BY inserted_at LIMIT 1",
                (str(order_id),),
            ).fetchone()
        return self.inventory.get_sale(row["id"]) if row else None

    def conduct(self, order, user_name="", audit_actor=None, replacement=None):
        order_id = str((order or {}).get("wb_order_id") or "").strip()
        if not order_id or order.get("source") != "wildberries":
            raise SalesInventoryError("Заказ Wildberries не найден")
        existing = self.find_sale(order_id)
        if existing:
            return existing
        # ERP stores prices in RUB; do not reinterpret a foreign currency.
        if str(order.get("currency_code") or "643") != "643":
            raise SalesInventoryError("Цена заказа Wildberries указана не в рублях")
        products = order.get("products") or []
        mappings = self.resolve_products(order)
        items = []
        for product, mapping in zip(products, mappings):
            catalog_product = mapping.get("product")
            if mapping.get("state") != "mapped" or not catalog_product:
                identity = product.get("article") or product.get("nm_id") or product.get("barcode") or "—"
                raise SalesInventoryError(
                    "Не удалось определить товар ERP для заказа Wildberries (SKU: {})".format(identity)
                )
            try:
                price = decimal_money(product.get("price"))
            except ValueError as error:
                raise SalesInventoryError(str(error)) from error
            if price is None:
                raise SalesInventoryError("В заказе Wildberries отсутствует цена")
            items.append({
                "product_id": catalog_product["id"],
                "quantity": product.get("quantity"),
                "unit_price": product["price"],
                "product_name": catalog_product.get("name") or "",
                "brand": catalog_product.get("brand") or "",
                "category": catalog_product.get("category") or "",
                "article": catalog_product.get("article") or "",
                "barcode": catalog_product.get("barcode") or "",
                "wb_order_item_id": product.get("order_item_id"),
                "wb_nm_id": product.get("nm_id"),
            })
        if len(items) != len(products) or not items:
            raise SalesInventoryError("Не удалось определить товар ERP для заказа Wildberries")
        payload = {
            "source": "wildberries", "sale_type": "automatic",
            "order_id": order_id, "external_order_id": order_id,
            "order_number": order_id,
            "order_created_at": order.get("created_at") or "",
            "performed_at": sale_now_iso(), "performed_by": user_name,
            "recipient_name": order.get("customer") or "Покупатель Wildberries",
            "order_total": order.get("order_total"),
            "commission": "", "order_status": "completed",
        }
        arguments = dict(user_name=user_name, audit_actor=audit_actor,
                         idempotency_key="wildberries-order:" + order_id,
                         enforce_external_unique=True)
        if replacement:
            return self.inventory.create_order_strap_replacement_sale(
                payload, items, replacement, **arguments
            )
        return self.inventory.create_sale_batch(payload, items, **arguments)
