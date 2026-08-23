"""Canonical availability rules for products in an active brand inventory."""


LOCKED_PRODUCT_MESSAGE = (
    "Товар сейчас находится на инвентаризации. "
    "Сначала подтвердите его фактический остаток."
)
ACTIVE_BRAND_MESSAGE = "Для бренда {} уже проводится инвентаризация."


def locked_products(connection, product_ids):
    """Return active inventory locks for a product set in one batch query."""
    identifiers = sorted({int(value) for value in product_ids})
    if not identifiers:
        return {}
    locks = {}
    # Production SQLite 3.7 is limited to 999 bound variables. Brand-level
    # operations can legitimately contain several thousand products.
    for start in range(0, len(identifiers), 500):
        chunk = identifiers[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            "SELECT i.product_id, i.session_id, i.status, s.brand_id, "
            "b.name AS brand_name FROM erp_inventory_items i "
            "JOIN erp_inventory_sessions s ON s.id = i.session_id "
            "JOIN erp_brands b ON b.id = s.brand_id "
            "WHERE s.status = 'active' AND i.product_id IN ({})".format(
                placeholders
            ),
            chunk,
        ).fetchall()
        locks.update({int(row["product_id"]): dict(row) for row in rows})
    return locks


def assert_products_unlocked(connection, product_ids, error_type=ValueError):
    locks = locked_products(connection, product_ids)
    if locks:
        raise error_type(LOCKED_PRODUCT_MESSAGE)


def assert_product_references_unlocked(
        connection, product_references, error_type=ValueError):
    """Resolve local or MoySklad identifiers, then apply the canonical guard."""
    references = sorted({
        str(value or "").strip()
        for value in product_references
        if str(value or "").strip()
    })
    if not references:
        return
    local_ids = {int(value) for value in references if value.isdigit()}
    placeholders = ",".join("?" for _ in references)
    rows = connection.execute(
        "SELECT id FROM catalog_excel_products "
        "WHERE CAST(id AS TEXT) IN ({}) "
        "OR moysklad_product_id IN ({})".format(
            placeholders, placeholders
        ),
        references + references,
    ).fetchall()
    local_ids.update(int(row["id"]) for row in rows)
    assert_products_unlocked(connection, local_ids, error_type)


def active_brand_inventory(connection, brand_id):
    return connection.execute(
        "SELECT s.id, s.brand_id, b.name AS brand_name "
        "FROM erp_inventory_sessions s "
        "JOIN erp_brands b ON b.id = s.brand_id "
        "WHERE s.status = 'active' AND s.brand_id = ? LIMIT 1",
        (int(brand_id),),
    ).fetchone()


def assert_brand_without_active_inventory(
        connection, brand_id, error_type=ValueError):
    if brand_id in (None, ""):
        return
    inventory = active_brand_inventory(connection, brand_id)
    if inventory is not None:
        raise error_type(ACTIVE_BRAND_MESSAGE.format(inventory["brand_name"]))


def active_brand_inventory_by_name(connection, brand_name):
    name = str(brand_name or "").strip()
    if not name:
        return None
    return connection.execute(
        "SELECT s.id, s.brand_id, b.name AS brand_name "
        "FROM erp_inventory_sessions s "
        "JOIN erp_brands b ON b.id = s.brand_id "
        "WHERE s.status = 'active' "
        "AND lower(trim(b.name)) = lower(trim(?)) LIMIT 1",
        (name,),
    ).fetchone()


def assert_named_brand_without_active_inventory(
        connection, brand_name, error_type=ValueError):
    inventory = active_brand_inventory_by_name(connection, brand_name)
    if inventory is not None:
        raise error_type(ACTIVE_BRAND_MESSAGE.format(inventory["brand_name"]))


def assert_product_can_join_brand(
        connection, product_id, brand_id, error_type=ValueError):
    """Snapshot membership is immutable; only the product itself can be locked."""
    assert_products_unlocked(connection, [product_id], error_type)


def assert_no_active_inventory(connection, error_type=ValueError):
    inventory = connection.execute(
        "SELECT b.name AS brand_name FROM erp_inventory_sessions s "
        "JOIN erp_brands b ON b.id = s.brand_id "
        "WHERE s.status = 'active' LIMIT 1"
    ).fetchone()
    if inventory is not None:
        raise error_type(ACTIVE_BRAND_MESSAGE.format(inventory["brand_name"]))


def unlocked_product_sql(product_alias="p"):
    """SQL predicate used by product selectors without per-row queries."""
    return (
        "NOT EXISTS (SELECT 1 FROM erp_inventory_items inventory_lock_item "
        "JOIN erp_inventory_sessions inventory_lock_session "
        "ON inventory_lock_session.id = inventory_lock_item.session_id "
        "WHERE inventory_lock_item.product_id = {}.id "
        "AND inventory_lock_session.status = 'active' "
        ")"
    ).format(product_alias)
