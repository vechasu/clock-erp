"""Product collections independent from brand, category and price data."""

from __future__ import print_function

import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase


SYSTEM_COLLECTION_RULES = (
    {
        "system_key": "wow-price", "name": "WOW-цена", "slug": "wow-price",
        "source_type": "bitrix_section", "section_ids": {"271"},
    },
    {
        "system_key": "bestsellers", "name": "Бестселлеры",
        "slug": "bestsellers", "source_type": "bitrix_section",
        "section_ids": {"260"},
    },
    {
        "system_key": "new", "name": "Новинки", "slug": "new",
        "source_type": "bitrix_property", "property_code": "NEW",
    },
    {
        "system_key": "preorder", "name": "Предзаказ", "slug": "preorder",
        "source_type": "bitrix_property", "property_code": "ON_PREORDER",
    },
    {
        "system_key": "sale", "name": "Распродажа", "slug": "sale",
        "source_type": "bitrix_sections", "section_ids": {"65", "140", "252"},
    },
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_name(value):
    return " ".join(str(value or "").split()).casefold()


def slugify(value):
    value = normalize_name(value)
    value = re.sub(r"[^a-z0-9а-я]+", "-", value, flags=re.IGNORECASE)
    return value.strip("-") or "collection"


def _truthy_property_value(value):
    if isinstance(value, (list, tuple)):
        return any(_truthy_property_value(item) for item in value)
    return value not in (
        None, "", False, 0, "0", "N", "n", "Нет", "нет", "false", "False",
    )


def _has_property(product, code):
    for item in product.get("properties") or []:
        if str(item.get("code") or "").strip().casefold() != code.casefold():
            continue
        value = item.get("display_value")
        if value in (None, "", []):
            value = item.get("value")
        return _truthy_property_value(value)
    return False


def collection_keys_for_bitrix_product(product):
    section_ids = {
        str(category.get("id") or "").strip()
        for category in product.get("categories") or []
        if str(category.get("id") or "").strip()
    }
    keys = set()
    for rule in SYSTEM_COLLECTION_RULES:
        if rule.get("section_ids") and section_ids.intersection(rule["section_ids"]):
            keys.add(rule["system_key"])
        if rule.get("property_code") and _has_property(
            product, rule["property_code"]
        ):
            keys.add(rule["system_key"])
    return keys


class ProductCollections(object):
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    @staticmethod
    def _collection_dict(row):
        item = dict(row)
        item["active"] = bool(item.get("active"))
        item["on_site"] = bool(item.get("on_site"))
        try:
            item["source_config"] = json.loads(
                item.get("source_config_json") or "{}"
            )
        except (TypeError, ValueError):
            item["source_config"] = {}
        return item

    def list_collections(self, include_archived=False):
        self.database.initialize()
        where = "" if include_archived else "WHERE c.active = 1"
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*, COUNT(CASE WHEN p.active=1 THEN pc.product_id END) "
                "AS product_count FROM erp_collections c "
                "LEFT JOIN product_collections pc ON pc.collection_id=c.id "
                "LEFT JOIN catalog_excel_products p ON p.id=pc.product_id "
                + where + " GROUP BY c.id ORDER BY c.system_key IS NULL, c.id"
            ).fetchall()
        return [self._collection_dict(row) for row in rows]

    def get_collection(self, collection_id=None, slug=None, include_archived=False):
        if collection_id is None and not slug:
            return None
        field = "c.id" if collection_id is not None else "c.slug"
        value = int(collection_id) if collection_id is not None else str(slug)
        active = "" if include_archived else " AND c.active=1"
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT c.*, COUNT(CASE WHEN p.active=1 THEN pc.product_id END) "
                "AS product_count FROM erp_collections c "
                "LEFT JOIN product_collections pc ON pc.collection_id=c.id "
                "LEFT JOIN catalog_excel_products p ON p.id=pc.product_id "
                "WHERE {}=?{} GROUP BY c.id".format(field, active),
                (value,),
            ).fetchone()
        return self._collection_dict(row) if row else None

    def create_collection(self, name, on_site=True):
        name = " ".join(str(name or "").split())
        if not name:
            raise ValueError("Название подборки обязательно.")
        normalized = normalize_name(name)
        base_slug = slugify(name)
        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM erp_collections WHERE normalized_name=?",
                (normalized,),
            ).fetchone()
            if existing:
                raise ValueError("Подборка с таким названием уже существует.")
            slug = base_slug
            suffix = 2
            while connection.execute(
                "SELECT 1 FROM erp_collections WHERE slug=?", (slug,)
            ).fetchone():
                slug = "{}-{}".format(base_slug, suffix)
                suffix += 1
            cursor = connection.execute(
                "INSERT INTO erp_collections "
                "(name,normalized_name,slug,active,on_site,system_key,source_type,"
                "source_config_json,created_at,updated_at) "
                "VALUES(?,?,?,1,?,NULL,'manual','{}',?,?)",
                (name, normalized, slug, 1 if on_site else 0, now, now),
            )
            collection_id = cursor.lastrowid
        return self.get_collection(collection_id)

    def update_collection(self, collection_id, name=None, on_site=None):
        current = self.get_collection(collection_id, include_archived=True)
        if current is None:
            raise ValueError("Подборка не найдена.")
        new_name = current["name"] if name is None else " ".join(str(name).split())
        if not new_name:
            raise ValueError("Название подборки обязательно.")
        if current.get("system_key") and normalize_name(new_name) != normalize_name(current["name"]):
            raise ValueError("Название системной подборки изменять нельзя.")
        with self.database.transaction() as connection:
            duplicate = connection.execute(
                "SELECT id FROM erp_collections WHERE normalized_name=? AND id<>?",
                (normalize_name(new_name), int(collection_id)),
            ).fetchone()
            if duplicate:
                raise ValueError("Подборка с таким названием уже существует.")
            connection.execute(
                "UPDATE erp_collections SET name=?,normalized_name=?,on_site=?,"
                "updated_at=? WHERE id=?",
                (
                    new_name, normalize_name(new_name),
                    current["on_site"] if on_site is None else (1 if on_site else 0),
                    utc_now(), int(collection_id),
                ),
            )
        return self.get_collection(collection_id, include_archived=True)

    def archive_collection(self, collection_id):
        current = self.get_collection(collection_id, include_archived=True)
        if current is None:
            raise ValueError("Подборка не найдена.")
        if current.get("system_key"):
            raise ValueError("Системную подборку архивировать нельзя.")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE erp_collections SET active=0,on_site=0,updated_at=? "
                "WHERE id=?", (utc_now(), int(collection_id)),
            )
        return True

    def product_collection_ids(self, product_id, include_archived=False):
        active = "" if include_archived else " AND c.active=1"
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.id FROM product_collections pc JOIN erp_collections c "
                "ON c.id=pc.collection_id WHERE pc.product_id=?{} "
                "ORDER BY c.name".format(active), (int(product_id),),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def product_collections(self, product_ids):
        ids = sorted({int(value) for value in product_ids})
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT pc.product_id,c.id,c.name,c.slug,c.on_site,c.system_key "
                "FROM product_collections pc JOIN erp_collections c "
                "ON c.id=pc.collection_id WHERE c.active=1 AND pc.product_id IN (" +
                placeholders + ") ORDER BY c.name", ids,
            ).fetchall()
        result = defaultdict(list)
        for row in rows:
            result[int(row["product_id"])].append(dict(row))
        return dict(result)

    def set_product_collections(self, product_id, collection_ids):
        requested = sorted({int(value) for value in (collection_ids or [])})
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT id FROM catalog_excel_products WHERE id=? AND active=1",
                (int(product_id),),
            ).fetchone()
            if product is None:
                raise ValueError("Товар не найден.")
            if requested:
                placeholders = ",".join("?" for _ in requested)
                available = {
                    int(row[0]) for row in connection.execute(
                        "SELECT id FROM erp_collections WHERE active=1 AND id IN (" +
                        placeholders + ")", requested,
                    ).fetchall()
                }
                if available != set(requested):
                    raise ValueError("Одна из подборок не найдена.")
            connection.execute(
                "DELETE FROM product_collections WHERE product_id=?",
                (int(product_id),),
            )
            now = utc_now()
            for collection_id in requested:
                connection.execute(
                    "INSERT INTO product_collections "
                    "(product_id,collection_id,source,created_at) "
                    "VALUES(?,?,'manual',?)",
                    (int(product_id), collection_id, now),
                )
        return self.product_collection_ids(product_id)

    def add_products(self, collection_id, product_ids):
        collection = self.get_collection(collection_id)
        if collection is None:
            raise ValueError("Подборка не найдена.")
        ids = sorted({int(value) for value in (product_ids or [])})
        if not ids:
            raise ValueError("Выберите хотя бы один товар.")
        with self.database.transaction() as connection:
            placeholders = ",".join("?" for _ in ids)
            found = {
                int(row[0]) for row in connection.execute(
                    "SELECT id FROM catalog_excel_products WHERE active=1 AND id IN (" +
                    placeholders + ")", ids,
                ).fetchall()
            }
            if found != set(ids):
                raise ValueError("Один из товаров не найден или архивирован.")
            now = utc_now()
            for product_id in ids:
                connection.execute(
                    "INSERT OR IGNORE INTO product_collections "
                    "(product_id,collection_id,source,created_at) "
                    "VALUES(?,?,'manual',?)",
                    (product_id, int(collection_id), now),
                )
        return len(ids)

    def remove_products(self, collection_id, product_ids):
        ids = sorted({int(value) for value in (product_ids or [])})
        if not ids:
            raise ValueError("Выберите хотя бы один товар.")
        with self.database.transaction() as connection:
            placeholders = ",".join("?" for _ in ids)
            cursor = connection.execute(
                "DELETE FROM product_collections WHERE collection_id=? "
                "AND product_id IN (" + placeholders + ")",
                [int(collection_id)] + ids,
            )
        return int(cursor.rowcount)

    def list_products(
        self, collection_id=None, query="", brand_id=None, category_id=None,
        active="1", exclude_collection_id=None, limit=500,
    ):
        where = []
        parameters = []
        join = ""
        if collection_id is not None:
            join += " JOIN product_collections pc ON pc.product_id=p.id "
            where.append("pc.collection_id=?")
            parameters.append(int(collection_id))
        if exclude_collection_id is not None:
            where.append(
                "NOT EXISTS (SELECT 1 FROM product_collections excluded "
                "WHERE excluded.product_id=p.id AND excluded.collection_id=?)"
            )
            parameters.append(int(exclude_collection_id))
        if str(active) in {"0", "1"}:
            where.append("p.active=?")
            parameters.append(int(active))
        if brand_id not in (None, ""):
            where.append("p.brand_id=?")
            parameters.append(int(brand_id))
        if category_id not in (None, ""):
            where.append("p.category_id=?")
            parameters.append(int(category_id))
        query = " ".join(str(query or "").split())
        if query:
            where.append(
                "(p.excel_name_raw LIKE ? COLLATE NOCASE OR "
                "COALESCE(p.excel_article,'') LIKE ? COLLATE NOCASE OR "
                "COALESCE(b.name,p.excel_brand,'') LIKE ? COLLATE NOCASE)"
            )
            parameters.extend(["%{}%".format(query)] * 3)
        statement = (
            "SELECT p.id,p.excel_name_raw AS name,p.excel_article AS article,"
            "p.active,p.stock,p.brand_id,p.category_id,"
            "COALESCE(b.name,p.excel_brand,'') AS brand,"
            "COALESCE(c.name,p.excel_category,'') AS category "
            "FROM catalog_excel_products p " + join +
            "LEFT JOIN erp_brands b ON b.id=p.brand_id "
            "LEFT JOIN erp_categories c ON c.id=p.category_id "
            + (" WHERE " + " AND ".join(where) if where else "") +
            " ORDER BY p.excel_name_raw COLLATE NOCASE,p.id LIMIT ?"
        )
        parameters.append(max(1, min(int(limit), 1000)))
        with self.database.connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["active"] = bool(item["active"])
            result.append(item)
        return result

    def _erp_external_index(self, connection):
        rows = connection.execute(
            "SELECT p.id,p.bitrix_external_product_id,cp.external_product_id "
            "FROM catalog_excel_products p LEFT JOIN catalog_products cp "
            "ON cp.id=p.bitrix_catalog_product_id"
        ).fetchall()
        result = defaultdict(set)
        for row in rows:
            for value in (row[1], row[2]):
                value = str(value or "").strip()
                if value:
                    result[value].add(int(row[0]))
        return result

    def dry_run(self, bitrix_products):
        with self.database.connect() as connection:
            erp_index = self._erp_external_index(connection)
        collection_counts = {rule["system_key"]: 0 for rule in SYSTEM_COLLECTION_RULES}
        found_counts = {rule["system_key"]: 0 for rule in SYSTEM_COLLECTION_RULES}
        links = set()
        missing = []
        product_keys = defaultdict(set)
        for product in bitrix_products:
            keys = collection_keys_for_bitrix_product(product)
            if not keys:
                continue
            external_id = str(product.get("external_product_id") or "").strip()
            erp_ids = erp_index.get(external_id, set())
            for key in keys:
                collection_counts[key] += 1
            if not erp_ids:
                missing.append({
                    "external_product_id": external_id,
                    "name": product.get("name") or "",
                    "collections": sorted(keys),
                })
                continue
            for key in keys:
                found_counts[key] += len(erp_ids)
            for product_id in erp_ids:
                for key in keys:
                    links.add((product_id, key))
                    product_keys[product_id].add(key)
        overlaps = {
            "multiple_collection_products": sum(
                1 for keys in product_keys.values() if len(keys) > 1
            ),
            "pairs": defaultdict(int),
        }
        for keys in product_keys.values():
            ordered = sorted(keys)
            for index, left in enumerate(ordered):
                for right in ordered[index + 1:]:
                    overlaps["pairs"]["{} + {}".format(left, right)] += 1
        overlaps["pairs"] = dict(sorted(overlaps["pairs"].items()))
        return {
            "bitrix_products": len(bitrix_products),
            "collection_members": collection_counts,
            "erp_found_members": found_counts,
            "missing_count": len(missing),
            "missing_products": missing,
            "expected_links": len(links),
            "overlaps": overlaps,
            "links": links,
        }

    def import_bitrix_memberships(self, bitrix_products):
        report = self.dry_run(bitrix_products)
        with self.database.transaction() as connection:
            collection_ids = {
                str(row["system_key"]): int(row["id"])
                for row in connection.execute(
                    "SELECT id,system_key FROM erp_collections "
                    "WHERE system_key IS NOT NULL"
                ).fetchall()
            }
            missing_system = {
                rule["system_key"] for rule in SYSTEM_COLLECTION_RULES
            } - set(collection_ids)
            if missing_system:
                raise ValueError(
                    "Не созданы системные подборки: {}".format(
                        ", ".join(sorted(missing_system))
                    )
                )
            system_ids = list(collection_ids.values())
            placeholders = ",".join("?" for _ in system_ids)
            connection.execute(
                "DELETE FROM product_collections WHERE source='bitrix_import' "
                "AND collection_id IN (" + placeholders + ")", system_ids,
            )
            now = utc_now()
            for product_id, system_key in sorted(report["links"]):
                connection.execute(
                    "INSERT OR IGNORE INTO product_collections "
                    "(product_id,collection_id,source,created_at) "
                    "VALUES(?,?,'bitrix_import',?)",
                    (product_id, collection_ids[system_key], now),
                )
        report = dict(report)
        report.pop("links", None)
        report["imported_links"] = report["expected_links"]
        return report
