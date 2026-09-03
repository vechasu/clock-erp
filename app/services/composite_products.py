"""Bill-of-material mappings for virtual storefront products."""

from __future__ import print_function

import math
import logging
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase


logger = logging.getLogger(__name__)


class CompositeProductError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _quantity(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CompositeProductError("Количество компонента должно быть числом.")
    if not math.isfinite(number) or number <= 0:
        raise CompositeProductError("Количество компонента должно быть больше нуля.")
    return number


class CompositeProducts:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    @staticmethod
    def _product(connection, product_id):
        return connection.execute(
            "SELECT p.id,p.active,p.stock,p.cell,p.excel_name_raw AS name,"
            "p.excel_article AS article,p.bitrix_external_product_id,"
            "p.bitrix_xml_id,p.local_image_path,p.bitrix_thumbnail_url,"
            "p.bitrix_primary_image_url FROM catalog_excel_products p WHERE p.id=?",
            (int(product_id),),
        ).fetchone()

    @classmethod
    def _mapping_from_row(cls, connection, row):
        components = connection.execute(
            "SELECT c.id,c.component_product_id AS product_id,c.component_type,"
            "c.quantity,p.excel_name_raw AS name,p.excel_article AS article,"
            "p.stock,p.cell,p.active,p.local_image_path,p.bitrix_thumbnail_url,"
            "p.bitrix_primary_image_url FROM composite_product_components c "
            "JOIN catalog_excel_products p ON p.id=c.component_product_id "
            "WHERE c.composite_product_id=? ORDER BY c.sort_order,c.id",
            (row["id"],),
        ).fetchall()
        data = dict(row)
        data["active"] = bool(data["active"])
        data["components"] = []
        availability = None
        for component in components:
            item = dict(component)
            item["quantity"] = float(item["quantity"])
            item["stock"] = float(item["stock"] or 0)
            item["available_quantity"] = item["stock"]
            item["image_url"] = (
                item.get("local_image_path") or item.get("bitrix_thumbnail_url")
                or item.get("bitrix_primary_image_url") or ""
            )
            possible = math.floor(item["stock"] / item["quantity"])
            availability = possible if availability is None else min(availability, possible)
            data["components"].append(item)
        data["available_quantity"] = float(availability or 0)
        data["is_composite"] = True
        return data

    def list_mappings(self, active=None):
        self.database.initialize()
        where = ""
        params = ()
        if active is not None:
            where = " WHERE cp.active=?"
            params = (1 if active else 0,)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT cp.*,p.excel_name_raw AS storefront_name,"
                "p.excel_article AS storefront_sku,p.bitrix_external_product_id "
                "AS storefront_external_id FROM composite_products cp "
                "JOIN catalog_excel_products p ON p.id=cp.storefront_product_id" +
                where + " ORDER BY p.excel_name_raw COLLATE NOCASE,cp.id",
                params,
            ).fetchall()
            return [self._mapping_from_row(connection, row) for row in rows]

    def get(self, mapping_id):
        self.database.initialize()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cp.*,p.excel_name_raw AS storefront_name,"
                "p.excel_article AS storefront_sku,p.bitrix_external_product_id "
                "AS storefront_external_id FROM composite_products cp "
                "JOIN catalog_excel_products p ON p.id=cp.storefront_product_id "
                "WHERE cp.id=?", (int(mapping_id),),
            ).fetchone()
            return self._mapping_from_row(connection, row) if row else None

    @classmethod
    def resolve_in_connection(cls, connection, storefront_product_id, active_only=True):
        row = connection.execute(
            "SELECT cp.*,p.excel_name_raw AS storefront_name,"
            "p.excel_article AS storefront_sku,p.bitrix_external_product_id "
            "AS storefront_external_id FROM composite_products cp "
            "JOIN catalog_excel_products p ON p.id=cp.storefront_product_id "
            "WHERE cp.storefront_product_id=?" + (" AND cp.active=1" if active_only else ""),
            (int(storefront_product_id),),
        ).fetchone()
        return cls._mapping_from_row(connection, row) if row else None

    def resolve(self, storefront_product_id, active_only=True):
        self.database.initialize()
        with self.database.connect() as connection:
            return self.resolve_in_connection(connection, storefront_product_id, active_only)

    def save(self, storefront_product_id, components, mapping_id=None, active=True):
        if not isinstance(components, list) or not components:
            raise CompositeProductError("Добавьте хотя бы один физический компонент.")
        prepared = []
        seen = set()
        for index, component in enumerate(components):
            try:
                product_id = int(component.get("product_id"))
            except (TypeError, ValueError):
                raise CompositeProductError("Выберите товар-компонент.")
            component_type = str(component.get("type") or component.get("component_type") or "component").strip().lower()
            if not component_type or len(component_type) > 40:
                raise CompositeProductError("Некорректный тип компонента.")
            key = (product_id, component_type)
            if key in seen:
                raise CompositeProductError("Один компонент указан повторно.")
            seen.add(key)
            prepared.append((product_id, component_type, _quantity(component.get("quantity", 1)), index))
        storefront_product_id = int(storefront_product_id)
        now = utc_now()
        self.database.initialize()
        with self.database.transaction() as connection:
            storefront = self._product(connection, storefront_product_id)
            if storefront is None or not storefront["active"]:
                raise CompositeProductError("Витринный товар не найден.")
            if any(product_id == storefront_product_id for product_id, _kind, _qty, _sort in prepared):
                raise CompositeProductError("Витринный товар не может входить в собственный состав.")
            for product_id, _kind, _qty, _sort in prepared:
                product = self._product(connection, product_id)
                if product is None or not product["active"]:
                    raise CompositeProductError("Один из компонентов не найден или архивирован.")
                if self.resolve_in_connection(connection, product_id, True):
                    raise CompositeProductError("Сборный товар нельзя использовать как физический компонент.")
            if mapping_id is None:
                try:
                    connection.execute(
                        "INSERT INTO composite_products(storefront_product_id,active,created_at,updated_at) "
                        "VALUES(?,?,?,?)", (storefront_product_id, 1 if active else 0, now, now),
                    )
                except Exception as error:
                    if "UNIQUE" in str(error).upper():
                        raise CompositeProductError("Для этого витринного товара правило уже существует.")
                    raise
                mapping_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                mapping_id = int(mapping_id)
                cursor = connection.execute(
                    "UPDATE composite_products SET storefront_product_id=?,active=?,updated_at=? WHERE id=?",
                    (storefront_product_id, 1 if active else 0, now, mapping_id),
                )
                if cursor.rowcount != 1:
                    raise CompositeProductError("Правило сборного товара не найдено.")
                connection.execute("DELETE FROM composite_product_components WHERE composite_product_id=?", (mapping_id,))
            for product_id, component_type, quantity, sort_order in prepared:
                connection.execute(
                    "INSERT INTO composite_product_components(composite_product_id,component_product_id,component_type,quantity,sort_order,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (mapping_id, product_id, component_type, quantity, sort_order, now),
                )
        return self.get(mapping_id)

    def set_active(self, mapping_id, active):
        self.database.initialize()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE composite_products SET active=?,updated_at=? WHERE id=?",
                (1 if active else 0, utc_now(), int(mapping_id)),
            )
            if cursor.rowcount != 1:
                raise CompositeProductError("Правило сборного товара не найдено.")
        return self.get(mapping_id)

    def delete(self, mapping_id):
        self.database.initialize()
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM composite_products WHERE id=?", (int(mapping_id),))
            if cursor.rowcount != 1:
                raise CompositeProductError("Правило сборного товара не найдено.")
        return True

    def snapshot_order_item(self, order_id, order_item_id, storefront_product_id, quantity):
        """Persist once; later mapping edits never rewrite an order's composition."""
        order_id = str(order_id)
        order_item_id = str(order_item_id)
        self.database.initialize()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM order_item_components WHERE order_id=? AND order_item_id=? LIMIT 1",
                (order_id, order_item_id),
            ).fetchone()
            if existing:
                return self.order_item_snapshot_in_connection(connection, order_id, order_item_id)
            mapping = self.resolve_in_connection(connection, storefront_product_id, True)
            if mapping is None:
                return []
            now = utc_now()
            for component in mapping["components"]:
                connection.execute(
                    "INSERT INTO order_item_components(order_id,order_item_id,storefront_product_id,composite_product_id,component_product_id,component_type,unit_quantity,required_quantity,component_name,component_article,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (order_id, order_item_id, int(storefront_product_id), mapping["id"], component["product_id"], component["component_type"], component["quantity"], component["quantity"] * float(quantity), component["name"], component.get("article"), now),
                )
            logger.info(
                "Composite order resolved order_id=%s item_id=%s storefront_product_id=%s components=%s",
                order_id, order_item_id, storefront_product_id,
                [(item["component_type"], item["product_id"], item["quantity"])
                 for item in mapping["components"]],
            )
            return self.order_item_snapshot_in_connection(connection, order_id, order_item_id)

    @staticmethod
    def order_item_snapshot_in_connection(connection, order_id, order_item_id):
        rows = connection.execute(
            "SELECT s.*,p.stock,p.cell,p.local_image_path,p.bitrix_thumbnail_url,p.bitrix_primary_image_url "
            "FROM order_item_components s JOIN catalog_excel_products p ON p.id=s.component_product_id "
            "WHERE s.order_id=? AND s.order_item_id=? ORDER BY s.id",
            (str(order_id), str(order_item_id)),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["stock"] = float(item["stock"] or 0)
            item["image_url"] = item.get("local_image_path") or item.get("bitrix_thumbnail_url") or item.get("bitrix_primary_image_url") or ""
            result.append(item)
        return result

    def order_item_snapshot(self, order_id, order_item_id):
        self.database.initialize()
        with self.database.connect() as connection:
            return self.order_item_snapshot_in_connection(connection, order_id, order_item_id)

    def quantities_for_site(self):
        """Return derived quantities for the existing Bitrix export pipeline."""
        return {
            str(item["storefront_external_id"]): item["available_quantity"]
            for item in self.list_mappings(active=True)
            if item.get("storefront_external_id")
        }
