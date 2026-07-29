"""Conservative Bitrix-to-ERP brand and product-type classification."""

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from app.services.product_reconciliation import normalize_text


CATEGORIES = (
    "Наручные часы",
    "Будильники",
    "Настенные часы",
    "Украшения",
    "Ремни",
    "Очки",
    "Аксессуары",
)
CATEGORY_BY_KEY = {normalize_text(value): value for value in CATEGORIES}
# Kept for compatibility with already-tested non-production fixtures. The live
# Bitrix taxonomy is normalized to the Russian canonical labels above.
CATEGORY_BY_KEY.update({
    "watches": "Watches",
})
BRAND_PROPERTY_CODES = {
    "brand",
    "brand model",
    "manufacturer",
    "filter brand",
}
JEWELRY_PROPERTY_CODES = {
    "type of accessory",
    "material of accessory",
    "accessory color",
    "size of ring",
}
GLASSES_PROPERTY_CODES = {
    "material glasses",
    "prop linses color",
    "prop color of frame",
}
WATCH_PROPERTY_CODES = {
    "watch glass",
    "prop water",
    "prop mechanism",
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value):
    return str(value or "").strip()


def _useful_property_value(value):
    if isinstance(value, list):
        return any(_useful_property_value(item) for item in value)
    if isinstance(value, dict):
        return any(_useful_property_value(item) for item in value.values())
    if value in (None, "", False, 0):
        return False
    return normalize_text(value) not in {"нет", "не выбрано", "false", "0", "-"}


def _property_value(prop):
    value = prop.get("display_value")
    if value in (None, "", []):
        value = prop.get("value")
    return value


def _category_names(product):
    names = []
    for category in product.get("categories") or []:
        for part in category.get("path") or []:
            value = part.get("name") if isinstance(part, dict) else part
            if _text(value):
                names.append(_text(value))
        if _text(category.get("name")):
            names.append(_text(category.get("name")))
    category = product.get("category") or {}
    if _text(category.get("name")):
        names.append(_text(category.get("name")))
    return list(dict.fromkeys(names))


def _section_category_signals(product):
    signals = set()
    for raw_name in _category_names(product):
        name = normalize_text(raw_name)
        if name == "watches":
            signals.add("Watches")
        if "ремеш" in name or name == "ремни":
            signals.add("Ремни")
        if "будильник" in name:
            signals.add("Будильники")
        if "настенн" in name and "час" in name:
            signals.add("Настенные часы")
        if name in {"очки", "солнцезащитные очки"} or name.startswith("очки "):
            signals.add("Очки")
        if any(marker in name for marker in (
            "украшени", "бижутер", "серьг", "колье", "кольца",
            "браслет", "клипс", "кафф", "брош",
        )):
            signals.add("Украшения")
        if name in {"аксессуар", "аксессуары"}:
            signals.add("Аксессуары")
    return signals


def classify_product(product, existing_brand="", existing_category="",
                     known_brands=None):
    """Return deterministic brand/category values without fuzzy guessing."""
    properties = product.get("properties") or []
    explicit_brand = ""
    useful_codes = set()
    for prop in properties:
        value = _property_value(prop)
        if not _useful_property_value(value):
            continue
        code = normalize_text(prop.get("code"))
        name = normalize_text(prop.get("name"))
        useful_codes.add(code)
        if not explicit_brand and (
            code in BRAND_PROPERTY_CODES
            or name in {"бренд", "марка часов", "производитель"}
        ):
            explicit_brand = _text(value)

    brand = explicit_brand or _text(product.get("brand")) or _text(existing_brand)
    brand_reason = (
        "bitrix_brand_property" if explicit_brand
        else "bitrix_brand" if _text(product.get("brand"))
        else "existing_erp_brand" if _text(existing_brand)
        else ""
    )
    if not brand and known_brands:
        matches = {
            known_brands[normalize_text(name)]
            for name in _category_names(product)
            if normalize_text(name) in known_brands
        }
        if len(matches) == 1:
            brand = matches.pop()
            brand_reason = "known_brand_section"
    if brand and known_brands:
        brand = known_brands.get(normalize_text(brand), brand)

    existing_category = CATEGORY_BY_KEY.get(
        normalize_text(existing_category),
        "",
    )
    signals = _section_category_signals(product)
    if useful_codes & JEWELRY_PROPERTY_CODES:
        signals.add("Украшения")
    if useful_codes & GLASSES_PROPERTY_CODES:
        signals.add("Очки")

    if len(signals) == 1:
        category = next(iter(signals))
        category_reason = "bitrix_type_section_or_property"
        ambiguous = False
    elif len(signals) > 1:
        category = existing_category
        category_reason = "conflicting_type_signals"
        ambiguous = not bool(category)
    elif useful_codes & WATCH_PROPERTY_CODES:
        category = "Наручные часы"
        category_reason = "bitrix_watch_properties"
        ambiguous = False
    elif existing_category:
        category = existing_category
        category_reason = "existing_confirmed_category"
        ambiguous = False
    else:
        category = ""
        category_reason = "insufficient_type_evidence"
        ambiguous = True

    return {
        "brand": brand,
        "category": category,
        "brand_reason": brand_reason or "brand_missing",
        "category_reason": category_reason,
        "ambiguous": ambiguous,
    }


def _load_json(value):
    try:
        return json.loads(value or "null")
    except (TypeError, ValueError):
        return value


class ProductClassificationRepair:
    """Preview and repair ERP classification without inventory side effects."""

    def __init__(self, database):
        self.database = database

    def _products(self, connection):
        rows = [
            dict(row) for row in connection.execute(
                "SELECT e.id, e.excel_brand, e.excel_category, "
                "e.bitrix_brand, e.bitrix_category, "
                "e.bitrix_catalog_product_id, p.name, p.brand "
                "FROM catalog_excel_products e "
                "LEFT JOIN catalog_products p "
                "ON p.id = e.bitrix_catalog_product_id "
                "WHERE e.active = 1 ORDER BY e.id"
            )
        ]
        categories = defaultdict(list)
        for row in connection.execute(
            "SELECT pc.product_id, c.name, c.path_json "
            "FROM catalog_product_categories pc "
            "JOIN catalog_categories c ON c.id = pc.category_id "
            "ORDER BY pc.product_id, pc.sort, c.id"
        ):
            path = _load_json(row["path_json"])
            categories[row["product_id"]].append({
                "name": row["name"],
                "path": path if isinstance(path, list) else [],
            })
        properties = defaultdict(list)
        for row in connection.execute(
            "SELECT pv.product_id, pr.code, pr.name, pv.value_json, "
            "pv.display_value_json "
            "FROM catalog_product_property_values pv "
            "JOIN catalog_properties pr ON pr.id = pv.property_id "
            "ORDER BY pv.product_id, pr.sort, pr.id"
        ):
            properties[row["product_id"]].append({
                "code": row["code"],
                "name": row["name"],
                "value": _load_json(row["value_json"]),
                "display_value": _load_json(row["display_value_json"]),
            })
        known_brands = {}
        for row in rows:
            for value in (row["brand"], row["excel_brand"]):
                key = normalize_text(value)
                if key and key not in known_brands:
                    known_brands[key] = _text(value)

        result = []
        for row in rows:
            catalog_product_id = row["bitrix_catalog_product_id"]
            product = {
                "name": row["name"],
                "brand": row["brand"],
                "categories": categories.get(catalog_product_id, []),
                "properties": properties.get(catalog_product_id, []),
            }
            decision = classify_product(
                product,
                existing_brand=row["excel_brand"],
                existing_category=row["excel_category"],
                known_brands=known_brands,
            )
            result.append((row, decision))
        return result

    def run(self, apply=False, backup_root=None, example_limit=20):
        self.database.initialize()
        backup_path = None
        if apply:
            from app.services.bitrix_erp_product_sync import create_database_backup
            backup_path = create_database_backup(self.database, backup_root)
        run_id = uuid.uuid4().hex
        report = {
            "mode": "apply" if apply else "dry_run",
            "run_id": run_id,
            "backup_path": str(backup_path) if backup_path else None,
            "checked": 0,
            "brands_changed": 0,
            "categories_changed": 0,
            "without_brand": 0,
            "without_category": 0,
            "ambiguous": 0,
            "errors": 0,
            "updated": 0,
            "review_records_created": 0,
            "examples": [],
            "inventory_operations": 0,
        }
        with self.database.connect() as connection:
            candidates = self._products(connection)
            for row, decision in candidates:
                report["checked"] += 1
                new_brand = decision["brand"] or ""
                new_category = decision["category"] or None
                brand_changed = _text(row["excel_brand"]) != new_brand
                category_changed = _text(row["excel_category"]) != _text(new_category)
                if brand_changed:
                    report["brands_changed"] += 1
                if category_changed:
                    report["categories_changed"] += 1
                if not new_brand:
                    report["without_brand"] += 1
                if not new_category:
                    report["without_category"] += 1
                if decision["ambiguous"]:
                    report["ambiguous"] += 1
                if brand_changed or category_changed:
                    report["updated"] += 1
                    if len(report["examples"]) < example_limit:
                        report["examples"].append({
                            "product_id": row["id"],
                            "brand": {
                                "before": row["excel_brand"],
                                "after": new_brand,
                            },
                            "category": {
                                "before": row["excel_category"],
                                "after": new_category,
                            },
                            "reason": decision["category_reason"],
                        })
                if not apply:
                    continue
                audit_ambiguous = False
                if decision["ambiguous"]:
                    audit_ambiguous = connection.execute(
                        "SELECT 1 FROM catalog_product_classification_audit "
                        "WHERE product_id = ? AND status = 'ambiguous' LIMIT 1",
                        (row["id"],),
                    ).fetchone() is None
                if not (brand_changed or category_changed or audit_ambiguous):
                    continue
                savepoint = "classification_{}".format(row["id"])
                connection.execute("SAVEPOINT " + savepoint)
                try:
                    if brand_changed or category_changed:
                        connection.execute(
                            "UPDATE catalog_excel_products SET "
                            "excel_brand = ?, bitrix_brand = ?, "
                            "excel_category = ?, bitrix_category = ?, "
                            "updated_at = ? WHERE id = ?",
                            (
                                new_brand,
                                new_brand or None,
                                new_category,
                                new_category,
                                utc_now(),
                                row["id"],
                            ),
                        )
                    connection.execute(
                        "INSERT INTO catalog_product_classification_audit ("
                        "run_id, product_id, bitrix_catalog_product_id, status, "
                        "reason, previous_brand, new_brand, previous_category, "
                        "new_category, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            run_id,
                            row["id"],
                            row["bitrix_catalog_product_id"],
                            "ambiguous" if decision["ambiguous"] else "updated",
                            decision["category_reason"],
                            row["excel_brand"],
                            new_brand,
                            row["excel_category"],
                            new_category,
                            utc_now(),
                        ),
                    )
                    if audit_ambiguous:
                        report["review_records_created"] += 1
                    connection.execute("RELEASE SAVEPOINT " + savepoint)
                except Exception:
                    connection.execute("ROLLBACK TO SAVEPOINT " + savepoint)
                    connection.execute("RELEASE SAVEPOINT " + savepoint)
                    report["errors"] += 1
            if apply:
                connection.commit()
        return report
