"""Diagnose and safely repair numeric ERP product brands."""

import hashlib
import json
import uuid
from collections import defaultdict

from app.services.brand_values import (
    brand_from_properties,
    is_numeric_brand,
    normalize_brand,
)
from app.services.product_classification import CATEGORIES, utc_now
from app.services.product_reconciliation import normalize_text


NON_BRAND_SECTION_CODES = {
    normalize_text(value)
    for value in (
        "happybirthday",
        "sale",
        "straps",
        "wow-price",
    )
}
NON_BRAND_SECTION_NAMES = {
    normalize_text(value)
    for value in (
        "sale",
        "wow-цена",
        "бестселлеры",
        "большая распродажа",
        "майская распродажа",
        "новогодний ассортимент",
        "ремешки",
    )
}
PRODUCT_TYPE_KEYS = {
    normalize_text(value)
    for value in CATEGORIES
}


def _load_json(value, fallback=None):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _canonical_payload(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _category_path_names(category):
    path = category.get("path") or []
    names = []
    for item in path:
        value = item.get("name") if isinstance(item, dict) else item
        value = normalize_brand(value)
        if value:
            names.append(value)
    return names


def _is_bitrix_brand_section(category):
    name = normalize_brand(category.get("name"))
    code = normalize_text(category.get("code"))
    if (
        not name
        or not code
        or code.startswith("promo")
        or code in NON_BRAND_SECTION_CODES
        or normalize_text(name) in PRODUCT_TYPE_KEYS
        or normalize_text(name) in NON_BRAND_SECTION_NAMES
    ):
        return False
    path_names = _category_path_names(category)
    parent_keys = {
        normalize_text(value)
        for value in path_names
        if normalize_text(value) != normalize_text(name)
    }
    return bool(parent_keys & PRODUCT_TYPE_KEYS)


def bitrix_brand_section_candidates(categories):
    """Return unambiguous brand-like sections from Bitrix taxonomy."""
    candidates = {}
    for category in categories or []:
        name = normalize_brand(category.get("name"))
        if not _is_bitrix_brand_section(category):
            continue
        candidates.setdefault(normalize_text(name), name)
    return sorted(candidates.values(), key=str.casefold)


def resolve_catalog_brand(
        categories, properties, confirmed_section_brands=None):
    explicit_brand, _warning = brand_from_properties(properties)
    if explicit_brand:
        return explicit_brand, "bitrix_brand_property"
    confirmed_brands = {
        brand
        for category in categories or []
        if _is_bitrix_brand_section(category)
        for brand in (
            confirmed_section_brands or {}
        ).get(str(category.get("id") or ""), set())
        if normalize_brand(brand)
    }
    if len(confirmed_brands) == 1:
        return confirmed_brands.pop(), "bitrix_section_confirmed_brand"
    candidates = bitrix_brand_section_candidates(categories)
    if len(candidates) == 1:
        return candidates[0], "bitrix_brand_section"
    if len(candidates) > 1:
        return "", "multiple_bitrix_brand_sections"
    return "", "brand_not_confirmed_in_bitrix"


class NumericBrandRepair:
    """Repair invalid brand strings without changing product inventory."""

    def __init__(self, database):
        self.database = database

    @staticmethod
    def _catalog_rows(connection):
        return [
            dict(row)
            for row in connection.execute(
                "SELECT id, name, article, brand, external_product_id, "
                "external_xml_id, normalized_payload_json, payload_hash, "
                "created_at, updated_at FROM catalog_products ORDER BY id"
            )
            if is_numeric_brand(row["brand"])
        ]

    @staticmethod
    def _categories(connection):
        result = defaultdict(list)
        for row in connection.execute(
            "SELECT pc.product_id, c.external_category_id, c.code, c.name, "
            "c.path_json FROM catalog_product_categories pc "
            "JOIN catalog_categories c ON c.id = pc.category_id "
            "ORDER BY pc.product_id, pc.sort, c.id"
        ):
            result[row["product_id"]].append({
                "id": row["external_category_id"],
                "code": row["code"],
                "name": row["name"],
                "path": _load_json(row["path_json"], []),
            })
        return result

    @staticmethod
    def _properties(connection):
        result = defaultdict(list)
        for row in connection.execute(
            "SELECT pv.product_id, pr.code, pr.name, pv.value_json, "
            "pv.display_value_json FROM catalog_product_property_values pv "
            "JOIN catalog_properties pr ON pr.id = pv.property_id "
            "ORDER BY pv.product_id, pr.sort, pr.id"
        ):
            result[row["product_id"]].append({
                "code": row["code"],
                "name": row["name"],
                "value": _load_json(row["value_json"]),
                "display_value": _load_json(row["display_value_json"]),
            })
        return result

    @staticmethod
    def _confirmed_section_brands(connection):
        result = defaultdict(set)
        for row in connection.execute(
            "SELECT c.external_category_id, p.brand "
            "FROM catalog_product_categories pc "
            "JOIN catalog_categories c ON c.id = pc.category_id "
            "JOIN catalog_products p ON p.id = pc.product_id "
            "WHERE p.active = 1"
        ):
            brand = normalize_brand(row["brand"])
            if brand:
                result[str(row["external_category_id"] or "")].add(
                    brand
                )
        return result

    @staticmethod
    def _erp_rows(connection, affected_catalog_ids):
        rows = connection.execute(
            "SELECT p.id, p.excel_name_raw, p.excel_article, p.excel_brand, "
            "p.bitrix_brand, p.bitrix_catalog_product_id, "
            "p.bitrix_external_product_id, p.bitrix_xml_id, p.raw_excel_json, "
            "p.stock, p.stock_source, p.created_at, p.updated_at, "
            "p.excel_category, b.source_filename "
            "FROM catalog_excel_products p "
            "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
            "ORDER BY p.id"
        )
        return [
            dict(row)
            for row in rows
            if (
                is_numeric_brand(row["excel_brand"])
                or is_numeric_brand(row["bitrix_brand"])
                or row["bitrix_catalog_product_id"] in affected_catalog_ids
            )
        ]

    @staticmethod
    def _remaining_counts(connection):
        catalog_count = sum(
            is_numeric_brand(row[0])
            for row in connection.execute(
                "SELECT brand FROM catalog_products"
            )
        )
        erp_count = sum(
            is_numeric_brand(row["excel_brand"])
            or is_numeric_brand(row["bitrix_brand"])
            for row in connection.execute(
                "SELECT excel_brand, bitrix_brand "
                "FROM catalog_excel_products"
            )
        )
        return catalog_count, erp_count

    def run(self, apply=False, backup_root=None):
        if not self.database.exists():
            return {
                "mode": "apply" if apply else "dry_run",
                "affected_catalog_products": 0,
                "affected_erp_products": 0,
                "errors": 0,
            }
        backup_path = None
        if apply:
            from app.services.bitrix_erp_product_sync import (
                create_database_backup,
            )
            backup_path = create_database_backup(
                self.database,
                backup_root,
            )

        with self.database.connect() as connection:
            catalog_rows = self._catalog_rows(connection)
            categories = self._categories(connection)
            properties = self._properties(connection)
            confirmed_section_brands = (
                self._confirmed_section_brands(connection)
            )
            resolutions = {}
            for row in catalog_rows:
                brand, reason = resolve_catalog_brand(
                    categories.get(row["id"], []),
                    properties.get(row["id"], []),
                    confirmed_section_brands,
                )
                resolutions[row["id"]] = {
                    "brand": brand,
                    "reason": reason,
                    "sections": [
                        category["name"]
                        for category in categories.get(row["id"], [])
                    ],
                }
            affected_catalog_ids = set(resolutions)
            erp_rows = self._erp_rows(
                connection,
                affected_catalog_ids,
            )

        numeric_values = sorted(
            {
                value
                for row in catalog_rows
                for value in (str(row["brand"] or "").strip(),)
                if value
            }
            | {
                value
                for row in erp_rows
                for value in (
                    str(row["excel_brand"] or "").strip(),
                    str(row["bitrix_brand"] or "").strip(),
                )
                if is_numeric_brand(value)
            },
            key=lambda value: (int(value), value),
        )
        resolved_products = []
        unresolved_products = []
        resolved_by_brand = defaultdict(int)
        numeric_value_counts = defaultdict(
            lambda: {"products": 0, "stock": 0.0}
        )
        erp_changes = []
        for row in erp_rows:
            previous_numeric_brand = next(
                (
                    str(value).strip()
                    for value in (
                        row["excel_brand"],
                        row["bitrix_brand"],
                    )
                    if is_numeric_brand(value)
                ),
                "",
            )
            if previous_numeric_brand:
                numeric_value_counts[
                    previous_numeric_brand
                ]["products"] += 1
                numeric_value_counts[
                    previous_numeric_brand
                ]["stock"] += float(row["stock"] or 0)
            current_brand = normalize_brand(row["excel_brand"])
            resolution = resolutions.get(
                row["bitrix_catalog_product_id"],
                {},
            )
            source_row = _load_json(row["raw_excel_json"], {})
            source_brand = normalize_brand(
                source_row.get("excel_brand")
                if isinstance(source_row, dict)
                else ""
            )
            if current_brand:
                new_brand = current_brand
                reason = "existing_valid_erp_brand"
            elif resolution.get("brand"):
                new_brand = resolution["brand"]
                reason = resolution["reason"]
            elif source_brand:
                new_brand = source_brand
                reason = "source_excel_brand"
            else:
                new_brand = ""
                reason = resolution.get(
                    "reason",
                    "brand_not_confirmed_in_source",
                )
            item = {
                "product_id": row["id"],
                "name": row["excel_name_raw"],
                "article": row["excel_article"],
                "xml_id": row["bitrix_xml_id"],
                "bitrix_id": row["bitrix_external_product_id"],
                "previous_brand": row["excel_brand"],
                "new_brand": new_brand,
                "reason": reason,
                "stock": row["stock"],
                "stock_source": row["stock_source"],
                "source_filename": row["source_filename"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "sections": resolution.get("sections", []),
            }
            if new_brand:
                resolved_products.append(item)
                resolved_by_brand[new_brand] += 1
            else:
                unresolved_products.append(item)
            erp_changes.append((row, new_brand, reason))

        report = {
            "mode": "apply" if apply else "dry_run",
            "run_id": uuid.uuid4().hex,
            "backup_path": str(backup_path) if backup_path else None,
            "numeric_values": numeric_values,
            "affected_catalog_products": len(catalog_rows),
            "affected_erp_products": len(erp_rows),
            "resolved_products": len(resolved_products),
            "resolved_product_details": resolved_products,
            "unresolved_products": unresolved_products,
            "numeric_value_counts": {
                value: {
                    "products": numeric_value_counts[value]["products"],
                    "stock": (
                        int(numeric_value_counts[value]["stock"])
                        if numeric_value_counts[value]["stock"].is_integer()
                        else numeric_value_counts[value]["stock"]
                    ),
                }
                for value in numeric_values
            },
            "resolved_by_brand": dict(sorted(
                resolved_by_brand.items(),
                key=lambda item: item[0].casefold(),
            )),
            "catalog_rows_changed": 0,
            "erp_rows_changed": 0,
            "audit_rows_created": 0,
            "brand_registry_rows_deleted": 0,
            "inventory_operations": 0,
            "errors": 0,
            "projected_remaining_numeric_catalog": 0,
            "projected_remaining_numeric_erp": 0,
        }
        if not apply:
            return report

        now = utc_now()
        with self.database.transaction() as connection:
            for row in catalog_rows:
                resolution = resolutions[row["id"]]
                payload = _load_json(
                    row["normalized_payload_json"],
                    {},
                )
                if isinstance(payload, dict):
                    payload["brand"] = ""
                    payload["brand_validation_error"] = "brand_missing"
                    normalized_payload = _canonical_payload(payload)
                    payload_hash = hashlib.sha256(
                        normalized_payload.encode("utf-8")
                    ).hexdigest()
                else:
                    normalized_payload = row[
                        "normalized_payload_json"
                    ]
                    payload_hash = row["payload_hash"]
                connection.execute(
                    "UPDATE catalog_products SET brand = ?, "
                    "normalized_payload_json = ?, payload_hash = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        resolution["brand"],
                        normalized_payload,
                        payload_hash,
                        now,
                        row["id"],
                    ),
                )
                report["catalog_rows_changed"] += 1

            for row, new_brand, reason in erp_changes:
                connection.execute(
                    "UPDATE catalog_excel_products SET excel_brand = ?, "
                    "bitrix_brand = ?, updated_at = ? WHERE id = ?",
                    (new_brand, new_brand or None, now, row["id"]),
                )
                connection.execute(
                    "INSERT INTO catalog_product_classification_audit ("
                    "run_id, product_id, bitrix_catalog_product_id, status, "
                    "reason, previous_brand, new_brand, previous_category, "
                    "new_category, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        report["run_id"],
                        row["id"],
                        row["bitrix_catalog_product_id"],
                        "updated" if new_brand else "ambiguous",
                        reason,
                        row["excel_brand"],
                        new_brand,
                        row["excel_category"],
                        row["excel_category"],
                        now,
                    ),
                )
                report["erp_rows_changed"] += 1
                report["audit_rows_created"] += 1

            remaining_catalog, remaining_erp = self._remaining_counts(
                connection
            )
            if remaining_catalog or remaining_erp:
                raise RuntimeError(
                    "Numeric brand cleanup left invalid values"
                )
            report["remaining_numeric_catalog"] = remaining_catalog
            report["remaining_numeric_erp"] = remaining_erp
        return report
