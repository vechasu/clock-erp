"""Explicit local, single-level product compositions; physical stock stays physical."""
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.catalog_db import CatalogDatabase
from app.services.inventory_lock import assert_products_unlocked


class BundleError(ValueError):
    pass


def component_quantity(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BundleError("Количество компонента должно быть положительным целым числом.")
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise BundleError("Количество компонента должно быть положительным целым числом.")
    return int(number)


def compositions(connection, product_ids):
    ids = list(dict.fromkeys(int(value) for value in product_ids))
    result = {}
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        rows = connection.execute(
            "SELECT b.product_id,c.component_id,c.quantity,p.stock AS legacy_stock,ci.physical_stock AS stock,ci.initialized_at,p.active,"
            "p.excel_name_raw AS name,p.excel_article AS article "
            "FROM erp_product_bundles b LEFT JOIN erp_bundle_components c "
            "ON c.product_id=b.product_id LEFT JOIN catalog_excel_products p "
            "ON p.id=c.component_id LEFT JOIN erp_component_inventory ci ON ci.product_id=p.id WHERE b.product_id IN ({}) "
            "ORDER BY b.product_id,c.component_id".format(
                ",".join("?" for _ in chunk)), chunk,
        ).fetchall()
        for row in rows:
            result.setdefault(int(row["product_id"]), [])
            if row["component_id"] is not None:
                result[int(row["product_id"])].append(dict(row))
    return result


def physical_lines(connection, product_id, quantity):
    config = compositions(connection, [product_id])
    if product_id not in config:
        return [(product_id, quantity)]
    parts = config[product_id]
    if not parts or any(not part["active"] for part in parts):
        raise BundleError("Состав пуст или содержит архивированный компонент.")
    return [(part["component_id"], part["quantity"] * quantity) for part in parts]


class ProductBundles:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    def get_many(self, product_ids):
        with self.database.connect() as connection:
            configs = compositions(connection, product_ids)
        result = {
            product_id: {
                "is_bundle": True,
                "components": parts,
                "physical_inventory_initialized": bool(parts) and all(part["initialized_at"] is not None for part in parts),
                "available_to_assemble": max(0, min(
                    [int(math.floor(float(part["stock"] or 0) / part["quantity"]))
                     if part["active"] else 0 for part in parts] or [0]
                )),
            }
            for product_id, parts in configs.items()
        }

        ids = list(dict.fromkeys(int(value) for value in product_ids))
        with self.database.connect() as connection:
            for offset in range(0, len(ids), 400):
                chunk = ids[offset:offset + 400]
                for row in connection.execute("SELECT product_id,physical_stock,initialized_at FROM erp_component_inventory WHERE product_id IN ({})".format(",".join("?" for _ in chunk)), chunk):
                    result[int(row["product_id"])] = {"is_bundle": False, "is_physical_component": True,
                        "physical_stock": row["physical_stock"], "physical_inventory_initialized": row["initialized_at"] is not None,
                        "available_to_assemble": None, "components": []}
        return result

    def get(self, product_id):
        return self.get_many([product_id]).get(int(product_id), {
            "is_bundle": False, "components": [], "available_to_assemble": None,
        })

    def configure(self, product_id, components, enabled=True):
        product_id = int(product_id)
        if not isinstance(components, list):
            raise BundleError("Передайте список компонентов.")
        prepared = {}
        if enabled:
            if not components:
                raise BundleError("Сборный товар должен содержать компоненты.")
            for part in components:
                if not isinstance(part, dict):
                    raise BundleError("Некорректный компонент.")
                try:
                    component_id = int(part.get("component_id"))
                except (ValueError, TypeError):
                    raise BundleError("Компонент не найден.")
                if component_id == product_id:
                    raise BundleError("Товар не может содержать самого себя.")
                if component_id in prepared:
                    raise BundleError("Компонент повторяется; укажите суммарное количество в одной строке.")
                prepared[component_id] = component_quantity(part.get("quantity"))
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT stock FROM catalog_excel_products WHERE id=? AND active=1",
                (product_id,),
            ).fetchone()
            if product is None:
                raise BundleError("Товар не найден.")
            assert_products_unlocked(connection, [product_id] + list(prepared), BundleError)
            previous = connection.execute("SELECT 1 FROM erp_product_bundles WHERE product_id=?", (product_id,)).fetchone() is not None
            if previous and not enabled and float(product["stock"] or 0) != 0:
                raise BundleError("Нельзя вернуть legacy-остаток в физический учёт сменой режима.")
            if enabled:
                if connection.execute("SELECT 1 FROM erp_component_inventory WHERE product_id=?", (product_id,)).fetchone():
                    raise BundleError("У товара уже есть отдельный физический учёт компонента.")
                if connection.execute(
                    "SELECT 1 FROM erp_bundle_components WHERE component_id=? LIMIT 1",
                    (product_id,),
                ).fetchone():
                    raise BundleError("Товар уже используется физическим компонентом другого состава.")
                for component_id in prepared:
                    component = connection.execute(
                        "SELECT active FROM catalog_excel_products WHERE id=?", (component_id,),
                    ).fetchone()
                    if component is None or not component["active"]:
                        raise BundleError("Компонент отсутствует или архивирован.")
                    if connection.execute(
                        "SELECT 1 FROM erp_product_bundles WHERE product_id=?", (component_id,),
                    ).fetchone():
                        raise BundleError("Многоуровневые составы запрещены.")
                for component_id in prepared:
                    connection.execute("INSERT OR IGNORE INTO erp_component_inventory(product_id,updated_at) VALUES (?,?)",
                                       (component_id, datetime.now(timezone.utc).isoformat()))
                connection.execute(
                    "INSERT OR IGNORE INTO erp_product_bundles(product_id,updated_at) VALUES (?,?)",
                    (product_id, datetime.now(timezone.utc).isoformat()),
                )
            if previous != bool(enabled):
                connection.execute("INSERT INTO erp_bundle_transitions(product_id,enabled,legacy_stock,created_at) VALUES (?,?,?,?)",
                                   (product_id,int(bool(enabled)),product["stock"],datetime.now(timezone.utc).isoformat()))
            if enabled:
                connection.execute("UPDATE erp_product_bundles SET updated_at=? WHERE product_id=?",
                                   (datetime.now(timezone.utc).isoformat(), product_id))
            connection.execute("DELETE FROM erp_bundle_components WHERE product_id=?", (product_id,))
            if enabled:
                connection.executemany(
                    "INSERT INTO erp_bundle_components(product_id,component_id,quantity) VALUES (?,?,?)",
                    [(product_id, component_id, count) for component_id, count in prepared.items()],
                )
            else:
                connection.execute("DELETE FROM erp_product_bundles WHERE product_id=?", (product_id,))
        return self.get(product_id)
