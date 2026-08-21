"""Hard safety boundary for brands excluded from automated catalog changes."""

import hashlib
import json

from app.services.product_reconciliation import normalize_text


PROTECTED_BRANDS = {
    "ZIIIRO": {
        "aliases": ("ZIIIRO", "Ziiiro", "Ziiro"),
        "source_brand_ids": ("97",),
    },
    "VOID": {
        "aliases": ("VOID",),
        "source_brand_ids": ("95",),
    },
    "Oblivio": {
        "aliases": ("Oblivio", "OBLVLO"),
        "source_brand_ids": ("465",),
    },
    "Projects": {
        "aliases": ("Projects",),
        "source_brand_ids": ("83",),
    },
    "AARK": {
        "aliases": ("AARK",),
        "source_brand_ids": ("53",),
    },
    "A.B. Art": {
        "aliases": ("A.B. Art", "AB Art", "A B Art"),
        "source_brand_ids": ("457",),
    },
    "TRIWA": {
        "aliases": ("TRIWA",),
        "source_brand_ids": ("92",),
    },
}


def _compact(value):
    return "".join(character for character in normalize_text(value) if character.isalnum())


PROTECTED_ALIAS_KEYS = {
    _compact(alias): canonical
    for canonical, config in PROTECTED_BRANDS.items()
    for alias in config["aliases"]
}
PROTECTED_SOURCE_IDS = {
    source_id: canonical
    for canonical, config in PROTECTED_BRANDS.items()
    for source_id in config["source_brand_ids"]
}


def canonical_protected_brand(value):
    return PROTECTED_ALIAS_KEYS.get(_compact(value))


def protected_product_brand(product):
    """Return the protected canonical brand, erring on the side of exclusion."""
    direct = canonical_protected_brand(product.get("brand"))
    if direct:
        return direct
    for prop in product.get("properties") or []:
        source_id = str(prop.get("enum_id") or "").strip()
        if source_id in PROTECTED_SOURCE_IDS:
            return PROTECTED_SOURCE_IDS[source_id]
        value = prop.get("display_value")
        if value in (None, "", []):
            value = prop.get("value")
        candidate = canonical_protected_brand(value)
        if candidate:
            return candidate
    # A missing/garbled brand on a clearly branded card is ambiguous and must
    # be excluded rather than allowed through automation.
    normalized_name = normalize_text(product.get("name"))
    for alias_key, canonical in PROTECTED_ALIAS_KEYS.items():
        if normalized_name.replace(" ", "").startswith(alias_key):
            return canonical
    return None


def protected_brand_rows(connection):
    rows = connection.execute(
        "SELECT id, name, normalized_name, active FROM erp_brands ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows if canonical_protected_brand(row["name"])]


def protected_state(connection):
    """Serialize every protected ERP card and its inventory-related records."""
    brands = protected_brand_rows(connection)
    brand_ids = [int(row["id"]) for row in brands]
    product_rows = connection.execute(
        "SELECT * FROM catalog_excel_products ORDER BY id"
    ).fetchall()
    products = [
        dict(row) for row in product_rows
        if (
            row["brand_id"] in brand_ids
            or canonical_protected_brand(row["excel_brand"])
            or canonical_protected_brand(row["bitrix_brand"])
        )
    ]
    product_ids = [int(row["id"]) for row in products]
    source_rows = connection.execute(
        "SELECT * FROM catalog_products ORDER BY id"
    ).fetchall()
    source_products = [
        dict(row) for row in source_rows
        if canonical_protected_brand(row["brand"])
    ]
    source_product_ids = [int(row["id"]) for row in source_products]

    def related(table, column, identifiers):
        if not identifiers:
            return []
        placeholders = ",".join("?" for _ in identifiers)
        return [
            dict(row) for row in connection.execute(
                "SELECT * FROM {} WHERE {} IN ({}) ORDER BY 1".format(
                    table, column, placeholders
                ),
                identifiers,
            ).fetchall()
        ]

    state = {
        "brands": brands,
        "products": products,
        "source_products": source_products,
        "source_product_categories": related(
            "catalog_product_categories", "product_id", source_product_ids
        ),
        "source_property_values": related(
            "catalog_product_property_values", "product_id", source_product_ids
        ),
        "source_images": related("catalog_images", "product_id", source_product_ids),
        "source_prices": related("catalog_prices", "product_id", source_product_ids),
        "source_offers": related("catalog_offers", "product_id", source_product_ids),
        "categories": related("erp_categories", "brand_id", brand_ids),
        "brand_categories": related("erp_brand_categories", "brand_id", brand_ids),
        "stock_movements": related("catalog_stock_movements", "product_id", product_ids),
        "manual_stock_operations": related(
            "catalog_excel_manual_stock_operations", "product_id", product_ids
        ),
        "inventory_sessions": related("erp_inventory_sessions", "brand_id", brand_ids),
        "inventory_items": [],
    }
    session_ids = [row["id"] for row in state["inventory_sessions"]]
    state["inventory_items"] = related(
        "erp_inventory_items", "session_id", session_ids
    )
    return state


def protected_state_digest(connection):
    payload = json.dumps(
        protected_state(connection), ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
