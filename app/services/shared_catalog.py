"""Stable shared brand/category/product references for ERP workflows."""

from datetime import datetime, timezone
import re
import sqlite3
import unicodedata

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal


class DuplicateCatalogValueError(ValueError):
    def __init__(self, message, existing):
        self.existing = dict(existing)
        super().__init__(message)


class CatalogReferenceError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def display_name(value):
    return str(value or "").strip()


def catalog_name(value):
    return " ".join(display_name(value).split())


def normalized_name(value):
    return catalog_name(value).casefold()


def catalog_search_key(value):
    """Normalize catalog text consistently for prefix search."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.casefold().replace("ё", "е")
    return " ".join(re.sub(r"\s+", " ", normalized).strip().split())


def catalog_prefix_pattern(value):
    normalized = catalog_search_key(value)
    escaped = (
        normalized.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return escaped + "%"


def catalog_contains_pattern(value):
    return "%" + catalog_prefix_pattern(value)


def register_catalog_search(connection):
    connection.create_function(
        "catalog_search_key",
        1,
        catalog_search_key,
    )


def normalized_stock_value(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return int(number) if number.is_integer() else number


def format_stock_value(value):
    number = float(normalized_stock_value(value))
    if number.is_integer():
        return "{:,}".format(int(number)).replace(",", " ")
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    integer, fraction = ("{:.12f}".format(absolute)).split(".")
    fraction = fraction.rstrip("0")
    integer = "{:,}".format(int(integer)).replace(",", " ")
    return sign + integer + "." + fraction


CYRILLIC_TRANSLITERATION = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})


def potential_alias_key(value):
    transliterated = normalized_name(value).translate(
        CYRILLIC_TRANSLITERATION
    )
    compact = "".join(
        character for character in transliterated if character.isalnum()
    )
    return compact.replace("c", "k")


def ensure_brand(connection, name=None, brand_id=None, create=True):
    if brand_id not in (None, ""):
        try:
            brand_id = int(brand_id)
        except (TypeError, ValueError):
            raise CatalogReferenceError("Бренд не найден.")
        row = connection.execute(
            "SELECT * FROM erp_brands WHERE id = ? AND active = 1",
            (brand_id,),
        ).fetchone()
        if row is None:
            raise CatalogReferenceError("Бренд не найден.")
        return row

    name = catalog_name(name)
    key = normalized_name(name)
    if not key:
        return None
    row = connection.execute(
        "SELECT * FROM erp_brands WHERE normalized_name = ?",
        (key,),
    ).fetchone()
    if row is not None:
        if not row["active"]:
            connection.execute(
                "UPDATE erp_brands SET active = 1, updated_at = ? WHERE id = ?",
                (utc_now(), row["id"]),
            )
            row = connection.execute(
                "SELECT * FROM erp_brands WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return row
    if not create:
        raise CatalogReferenceError("Бренд не найден.")
    now = utc_now()
    connection.execute(
        "INSERT INTO erp_brands "
        "(name, normalized_name, active, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?)",
        (name, key, now, now),
    )
    return connection.execute(
        "SELECT * FROM erp_brands WHERE normalized_name = ?",
        (key,),
    ).fetchone()


def ensure_category(
    connection,
    brand_id,
    name=None,
    category_id=None,
    create=True,
):
    if category_id not in (None, ""):
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            raise CatalogReferenceError("Категория не найдена.")
        if category_id == 0:
            return None
        row = connection.execute(
            "SELECT * FROM erp_categories WHERE id = ? AND active = 1",
            (category_id,),
        ).fetchone()
        if row is None:
            raise CatalogReferenceError("Категория не найдена.")
        canonical = connection.execute(
            "SELECT * FROM erp_categories "
            "WHERE normalized_name = ? AND active = 1 "
            "ORDER BY id LIMIT 1",
            (normalized_name(row["name"]),),
        ).fetchone()
        if canonical is not None:
            row = canonical
        if brand_id:
            connection.execute(
                "INSERT OR IGNORE INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, ?, ?)",
                (int(brand_id), int(row["id"]), utc_now()),
            )
        return row

    name = catalog_name(name)
    key = normalized_name(name)
    if not key:
        return None
    if not brand_id:
        raise CatalogReferenceError("Сначала выберите бренд.")
    row = connection.execute(
        "SELECT * FROM erp_categories "
        "WHERE normalized_name = ? ORDER BY active DESC, id LIMIT 1",
        (key,),
    ).fetchone()
    if row is not None:
        if not row["active"]:
            connection.execute(
                "UPDATE erp_categories SET active = 1, updated_at = ? WHERE id = ?",
                (utc_now(), row["id"]),
            )
            row = connection.execute(
                "SELECT * FROM erp_categories WHERE id = ?",
                (row["id"],),
            ).fetchone()
        connection.execute(
            "INSERT OR IGNORE INTO erp_brand_categories "
            "(brand_id, category_id, created_at) VALUES (?, ?, ?)",
            (int(brand_id), int(row["id"]), utc_now()),
        )
        return row
    if not create:
        raise CatalogReferenceError("Категория не найдена.")
    now = utc_now()
    connection.execute(
        "INSERT INTO erp_categories "
        "(brand_id, name, normalized_name, active, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, ?, ?)",
        (int(brand_id), name, key, now, now),
    )
    row = connection.execute(
        "SELECT * FROM erp_categories "
        "WHERE normalized_name = ? ORDER BY active DESC, id LIMIT 1",
        (key,),
    ).fetchone()
    connection.execute(
        "INSERT OR IGNORE INTO erp_brand_categories "
        "(brand_id, category_id, created_at) VALUES (?, ?, ?)",
        (int(brand_id), int(row["id"]), now),
    )
    return row


def assign_product_taxonomy(
    connection,
    product_id,
    brand=None,
    category=None,
    brand_id=None,
    category_id=None,
):
    brand_row = ensure_brand(
        connection,
        name=brand,
        brand_id=brand_id,
        create=True,
    )
    category_row = ensure_category(
        connection,
        brand_row["id"] if brand_row else None,
        name=category if brand_row else None,
        category_id=category_id,
        create=True,
    )
    connection.execute(
        "UPDATE catalog_excel_products SET "
        "brand_id = ?, category_id = ?, excel_brand = ?, excel_category = ? "
        "WHERE id = ?",
        (
            brand_row["id"] if brand_row else None,
            category_row["id"] if category_row else None,
            brand_row["name"] if brand_row else "",
            category_row["name"] if category_row else None,
            int(product_id),
        ),
    )
    return brand_row, category_row


class SharedCatalog:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase(cache_initialization=True)

    def list_brands(self, query="", limit=50, include_archived=False):
        self.database.initialize()
        where = []
        if not include_archived:
            where.append("b.active = 1")
        query = catalog_search_key(query)
        query_filter = (
            " WHERE catalog_search_key(normalized_name) "
            "LIKE ? ESCAPE '\\' "
            if query else ""
        )
        parameters = (
            (catalog_prefix_pattern(query), max(1, min(int(limit), 200)))
            if query else (max(1, min(int(limit), 200)),)
        )
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        with self.database.connect() as connection:
            if query:
                register_catalog_search(connection)
            rows = connection.execute(
                "SELECT id, name, active, product_count, stock_total FROM ("
                "SELECT b.id, b.name, b.normalized_name, b.active, "
                "COUNT(p.id) AS product_count, "
                "COALESCE(SUM(p.stock), 0) AS stock_total "
                "FROM erp_brands b LEFT JOIN catalog_excel_products p "
                "ON p.brand_id = b.id AND p.active = 1 "
                + where_sql
                + " GROUP BY b.id UNION ALL "
                "SELECT 0 AS id, 'Без бренда' AS name, "
                "'без бренда' AS normalized_name, 1 AS active, "
                "COUNT(p.id) AS product_count, "
                "COALESCE(SUM(p.stock), 0) AS stock_total "
                "FROM catalog_excel_products p "
                "WHERE p.active = 1 AND p.brand_id IS NULL) AS brand_options "
                + query_filter
                + "ORDER BY name COLLATE NOCASE LIMIT ?",
                parameters,
            ).fetchall()
        return [self._brand(row) for row in rows]

    def list_brand_overviews(self, query="", limit=200, brand_id=None):
        """Load brands and their category aggregates in two batch queries."""
        self.database.initialize()
        query = catalog_search_key(query)
        parameters = []
        where = "WHERE b.active = 1"
        if brand_id not in (None, ""):
            where += " AND b.id = ?"
            parameters.append(int(brand_id))
        if query:
            where += " AND catalog_search_key(b.name) LIKE ? ESCAPE '\\'"
            parameters.append(catalog_prefix_pattern(query))
        parameters.append(max(1, min(int(limit), 500)))
        with self.database.connect() as connection:
            if query:
                register_catalog_search(connection)
            brand_rows = connection.execute(
                "SELECT b.id, b.name, b.active, COUNT(p.id) AS product_count, "
                "COALESCE(SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END), 0) "
                "AS nonzero_count, COALESCE(SUM(p.stock), 0) AS stock_total "
                "FROM erp_brands b LEFT JOIN catalog_excel_products p "
                "ON p.brand_id = b.id AND p.active = 1 " + where +
                " GROUP BY b.id ORDER BY b.name COLLATE NOCASE LIMIT ?",
                parameters,
            ).fetchall()
            brand_ids = [int(row["id"]) for row in brand_rows]
            category_rows = []
            uncategorized_rows = []
            if brand_ids:
                placeholders = ", ".join("?" for _ in brand_ids)
                category_rows = connection.execute(
                    "SELECT bc.brand_id, c.id, c.name, c.normalized_name, "
                    "COUNT(p.id) AS product_count, "
                    "COALESCE(SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END), 0) "
                    "AS nonzero_count, COALESCE(SUM(p.stock), 0) AS stock_total, "
                    "(SELECT COUNT(DISTINCT all_bc.brand_id) "
                    "FROM erp_brand_categories all_bc "
                    "JOIN erp_categories all_c "
                    "ON all_c.id = all_bc.category_id "
                    "WHERE all_c.active = 1 "
                    "AND all_c.normalized_name = c.normalized_name) "
                    "AS brand_count, "
                    "(SELECT COUNT(all_p.id) "
                    "FROM catalog_excel_products all_p "
                    "JOIN erp_categories all_c "
                    "ON all_c.id = all_p.category_id "
                    "WHERE all_p.active = 1 "
                    "AND all_c.normalized_name = c.normalized_name) "
                    "AS global_product_count "
                    "FROM erp_brand_categories bc JOIN erp_categories c "
                    "ON c.id = bc.category_id AND c.active = 1 "
                    "LEFT JOIN catalog_excel_products p ON p.brand_id = bc.brand_id "
                    "AND p.category_id = c.id AND p.active = 1 "
                    "WHERE bc.brand_id IN ({}) GROUP BY bc.brand_id, c.id "
                    "ORDER BY c.name COLLATE NOCASE".format(placeholders),
                    brand_ids,
                ).fetchall()
                uncategorized_rows = connection.execute(
                    "SELECT p.brand_id, COUNT(p.id) AS product_count, "
                    "COALESCE(SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END), 0) "
                    "AS nonzero_count, COALESCE(SUM(p.stock), 0) AS stock_total "
                    "FROM catalog_excel_products p "
                    "WHERE p.active = 1 AND p.category_id IS NULL "
                    "AND p.brand_id IN ({}) GROUP BY p.brand_id".format(
                        placeholders
                    ),
                    brand_ids,
                ).fetchall()
        categories = {}
        for row in category_rows:
            brand_categories = categories.setdefault(int(row["brand_id"]), {})
            key = normalized_name(row["name"])
            aggregate = brand_categories.get(key)
            prepared = {
                "id": int(row["id"]),
                "name": row["name"],
                "product_count": int(row["product_count"]),
                "nonzero_count": int(row["nonzero_count"]),
                "stock_total": normalized_stock_value(row["stock_total"]),
                "stock_display": format_stock_value(row["stock_total"]),
                "brand_count": int(row["brand_count"]),
                "global_product_count": int(row["global_product_count"]),
            }
            if aggregate is None:
                brand_categories[key] = prepared
            else:
                if prepared["id"] < aggregate["id"]:
                    aggregate["id"] = prepared["id"]
                    aggregate["name"] = prepared["name"]
                aggregate["product_count"] += prepared["product_count"]
                aggregate["nonzero_count"] += prepared["nonzero_count"]
                aggregate["stock_total"] = normalized_stock_value(
                    aggregate["stock_total"] + prepared["stock_total"]
                )
                aggregate["stock_display"] = format_stock_value(
                    aggregate["stock_total"]
                )
                aggregate["brand_count"] = max(
                    aggregate["brand_count"], prepared["brand_count"]
                )
                aggregate["global_product_count"] = max(
                    aggregate["global_product_count"],
                    prepared["global_product_count"],
                )
        for row in uncategorized_rows:
            categories.setdefault(int(row["brand_id"]), {})[""] = {
                "id": 0,
                "name": "Без категории",
                "product_count": int(row["product_count"]),
                "nonzero_count": int(row["nonzero_count"]),
                "stock_total": normalized_stock_value(row["stock_total"]),
                "stock_display": format_stock_value(row["stock_total"]),
                "brand_count": 1,
                "global_product_count": int(row["product_count"]),
            }
        return [{
            **self._brand(row),
            "nonzero_count": int(row["nonzero_count"]),
            "categories": sorted(
                categories.get(int(row["id"]), {}).values(),
                key=lambda item: (
                    item["id"] == 0,
                    normalized_name(item["name"]),
                    item["id"],
                ),
            ),
        } for row in brand_rows]

    def get_brand_overview(self, brand_id):
        try:
            brand_id = int(brand_id)
        except (TypeError, ValueError):
            return None
        return next(
            (
                item
                for item in self.list_brand_overviews(
                    limit=1,
                    brand_id=brand_id,
                )
                if item["id"] == brand_id
            ),
            None,
        )

    def create_brand_category(self, brand_id, name, **actor):
        self.database.initialize()
        with self.database.transaction() as connection:
            brand = ensure_brand(connection, brand_id=brand_id, create=False)
            cleaned = catalog_name(name)
            if not cleaned:
                raise ValueError("Название категории обязательно.")
            existing = connection.execute(
                "SELECT c.* FROM erp_brand_categories bc "
                "JOIN erp_categories c ON c.id = bc.category_id "
                "WHERE bc.brand_id = ? AND c.active = 1 "
                "AND c.normalized_name = ? LIMIT 1",
                (int(brand_id), normalized_name(cleaned)),
            ).fetchone()
            if existing is not None:
                raise DuplicateCatalogValueError(
                    "Эта категория уже добавлена в бренд.", existing
                )
            global_category = connection.execute(
                "SELECT id FROM erp_categories WHERE normalized_name = ? "
                "ORDER BY id LIMIT 1",
                (normalized_name(cleaned),),
            ).fetchone()
            category = ensure_category(
                connection, brand["id"], name=cleaned, create=True
            )
            relation = connection.execute(
                "SELECT created_at FROM erp_brand_categories "
                "WHERE brand_id = ? AND category_id = ?",
                (int(brand_id), int(category["id"])),
            ).fetchone()
            AuditJournal(self.database).record(
                "category", category["id"], "created", category["name"],
                (
                    "Создана в бренде {}".format(brand["name"])
                    if global_category is None
                    else "Добавлена в бренд {}".format(brand["name"])
                ),
                metadata={
                    "brand_id": int(brand_id),
                    "brand_name_snapshot": brand["name"],
                    "global_category_created": global_category is None,
                    "relation_action": (
                        "created" if global_category is None else "linked"
                    ),
                },
                connection=connection, **actor
            )
            return {"id": int(category["id"]), "name": category["name"],
                    "created_at": relation["created_at"]}

    def list_categories(
        self,
        brand_id=None,
        query="",
        limit=50,
        include_archived=False,
    ):
        self.database.initialize()
        where = []
        parameters = []
        if not include_archived:
            where.append("c.active = 1")
        if brand_id not in (None, ""):
            where.append("c.brand_id = ?")
            parameters.append(int(brand_id))
        query = catalog_search_key(query)
        if query:
            where.append(
                "catalog_search_key(c.normalized_name) LIKE ? ESCAPE '\\'"
            )
            parameters.append(catalog_prefix_pattern(query))
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        parameters.append(max(1, min(int(limit), 200)))
        with self.database.connect() as connection:
            if query:
                register_catalog_search(connection)
            rows = connection.execute(
                "SELECT c.id, c.brand_id, c.name, c.active, "
                "b.name AS brand_name, COUNT(p.id) AS product_count, "
                "COALESCE(SUM(p.stock), 0) AS stock_total "
                "FROM erp_categories c "
                "JOIN erp_brands b ON b.id = c.brand_id "
                "LEFT JOIN catalog_excel_products p "
                "ON p.category_id = c.id AND p.active = 1"
                + where_sql
                + " GROUP BY c.id ORDER BY c.name COLLATE NOCASE LIMIT ?",
                parameters,
            ).fetchall()
        return [self._category(row) for row in rows]

    def list_category_overviews(
        self,
        query="",
        limit=50,
        offset=0,
        category_id=None,
        sort_by="name",
        sort_dir="asc",
    ):
        """Return the global category registry with product-derived metrics."""
        self.database.initialize()
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        query = catalog_search_key(query)
        parameters = []
        where = ["c.active = 1"]
        if category_id not in (None, ""):
            where.append("c.id = ?")
            parameters.append(int(category_id))
        if query:
            where.append(
                "catalog_search_key(c.name) LIKE ? ESCAPE '\\'"
            )
            parameters.append(catalog_contains_pattern(query))
        where_sql = " AND ".join(where)
        directions = {"asc": "ASC", "desc": "DESC"}
        direction = directions.get(str(sort_dir).lower(), "ASC")
        sort_expressions = {
            "name": "c.name COLLATE NOCASE",
            "brands": "COALESCE(MAX(category_relations.brand_count), 0)",
            "products": "COUNT(p.id)",
            "in_stock": "SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END)",
            "stock": "SUM(COALESCE(p.stock, 0))",
        }
        if sort_by in sort_expressions:
            order_sql = "{} {}, c.name COLLATE NOCASE ASC".format(
                sort_expressions[sort_by], direction
            )
        else:
            order_sql = "c.name COLLATE NOCASE ASC, c.id ASC"

        system_matches = (
            category_id in (None, "", 0, "0")
            and (not query or query in catalog_search_key("Без категории"))
        )
        with self.database.connect() as connection:
            if query:
                register_catalog_search(connection)
            total = connection.execute(
                "SELECT COUNT(*) FROM erp_categories c WHERE " + where_sql,
                parameters,
            ).fetchone()[0] + (1 if system_matches else 0)

            system_offset = 0
            rows = []
            if system_matches and offset == 0:
                system_row = connection.execute(
                    "SELECT 0 AS id, 0 AS brand_id, 'Без категории' AS name, "
                    "1 AS active, 1 AS duplicate_count, "
                    "COUNT(p.id) AS product_count, "
                    "COUNT(DISTINCT COALESCE(p.brand_id, 0)) AS brand_count, "
                    "COALESCE(SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END), 0) "
                    "AS nonzero_count, COALESCE(SUM(p.stock), 0) AS stock_total "
                    "FROM catalog_excel_products p "
                    "WHERE p.active = 1 AND p.category_id IS NULL"
                ).fetchone()
                rows.append(system_row)
                system_offset = 1

            normal_limit = max(0, limit - system_offset)
            normal_offset = max(0, offset - (1 if system_matches else 0))
            if normal_limit:
                rows.extend(connection.execute(
                    "SELECT c.id, c.brand_id, c.name, c.active, "
                    "COALESCE(MAX(category_duplicates.duplicate_count), 1) "
                    "AS duplicate_count, "
                    "COUNT(p.id) AS product_count, "
                    "COALESCE(MAX(category_relations.brand_count), 0) "
                    "AS brand_count, "
                    "COALESCE(SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END), 0) "
                    "AS nonzero_count, COALESCE(SUM(p.stock), 0) AS stock_total "
                    "FROM erp_categories c LEFT JOIN catalog_excel_products p "
                    "ON p.category_id = c.id AND p.active = 1 "
                    "LEFT JOIN (SELECT category_id, COUNT(*) AS brand_count "
                    "FROM (SELECT bc.category_id, bc.brand_id "
                    "FROM erp_brand_categories bc WHERE bc.category_id <> 0 "
                    "UNION SELECT p.category_id, COALESCE(p.brand_id, 0) "
                    "FROM catalog_excel_products p WHERE p.active = 1 "
                    "AND p.category_id IS NOT NULL) category_brands "
                    "GROUP BY category_id) category_relations "
                    "ON category_relations.category_id = c.id "
                    "LEFT JOIN (SELECT normalized_name, COUNT(*) "
                    "AS duplicate_count FROM erp_categories "
                    "WHERE active = 1 GROUP BY normalized_name "
                    "HAVING COUNT(*) > 1) category_duplicates "
                    "ON category_duplicates.normalized_name = c.normalized_name "
                    "WHERE " + where_sql + " GROUP BY c.id ORDER BY "
                    + order_sql + ", c.id ASC LIMIT ? OFFSET ?",
                    parameters + [normal_limit, normal_offset],
                ).fetchall())

            category_ids = [int(row["id"]) for row in rows if int(row["id"])]
            brand_rows = []
            if category_ids:
                placeholders = ", ".join("?" for _ in category_ids)
                brand_rows = connection.execute(
                    "SELECT pairs.category_id, pairs.brand_id AS id, "
                    "COALESCE(b.name, 'Без бренда') AS name, "
                    "COUNT(p.id) AS product_count, "
                    "COALESCE(SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END), 0) "
                    "AS nonzero_count, COALESCE(SUM(p.stock), 0) AS stock_total, "
                    "EXISTS (SELECT 1 FROM erp_brand_categories relation "
                    "WHERE relation.category_id = pairs.category_id "
                    "AND relation.brand_id = pairs.brand_id) AS explicit_relation "
                    "FROM (SELECT bc.category_id, bc.brand_id "
                    "FROM erp_brand_categories bc WHERE bc.category_id IN ({0}) "
                    "UNION SELECT p.category_id, COALESCE(p.brand_id, 0) "
                    "FROM catalog_excel_products p WHERE p.active = 1 "
                    "AND p.category_id IN ({0})) pairs "
                    "LEFT JOIN erp_brands b ON b.id = pairs.brand_id "
                    "LEFT JOIN catalog_excel_products p ON p.active = 1 "
                    "AND p.category_id = pairs.category_id "
                    "AND COALESCE(p.brand_id, 0) = pairs.brand_id "
                    "GROUP BY pairs.category_id, pairs.brand_id "
                    "ORDER BY name COLLATE NOCASE, pairs.brand_id".format(
                        placeholders
                    ),
                    category_ids + category_ids,
                ).fetchall()
            if any(int(row["id"]) == 0 for row in rows):
                brand_rows.extend(connection.execute(
                    "SELECT 0 AS category_id, COALESCE(b.id, 0) AS id, "
                    "COALESCE(b.name, 'Без бренда') AS name, COUNT(p.id) "
                    "AS product_count, COALESCE(SUM(CASE WHEN p.stock != 0 "
                    "THEN 1 ELSE 0 END), 0) AS nonzero_count, "
                    "COALESCE(SUM(p.stock), 0) AS stock_total, "
                    "0 AS explicit_relation "
                    "FROM catalog_excel_products p LEFT JOIN erp_brands b "
                    "ON b.id = p.brand_id WHERE p.active = 1 "
                    "AND p.category_id IS NULL GROUP BY COALESCE(b.id, 0) "
                    "ORDER BY name COLLATE NOCASE, id"
                ).fetchall())

        brands = {}
        for row in brand_rows:
            brands.setdefault(int(row["category_id"]), []).append({
                "id": int(row["id"]),
                "name": row["name"],
                "product_count": int(row["product_count"]),
                "nonzero_count": int(row["nonzero_count"]),
                "stock_total": normalized_stock_value(row["stock_total"]),
                "stock_display": format_stock_value(row["stock_total"]),
                "explicit_relation": bool(row["explicit_relation"]),
                "product_only": (
                    not bool(row["explicit_relation"])
                    and int(row["product_count"]) > 0
                    and int(row["category_id"]) != 0
                ),
            })
        items = []
        for row in rows:
            item = self._category(row)
            item.update({
                "system": int(row["id"]) == 0,
                "duplicate_category": (
                    int(row["id"]) != 0
                    and int(row["duplicate_count"]) > 1
                ),
                "status": (
                    "Системная" if int(row["id"]) == 0 else
                    "Используется" if int(row["product_count"]) else
                    "Не используется"
                ),
                "brand_count": int(row["brand_count"]),
                "nonzero_count": int(row["nonzero_count"]),
                "brands": brands.get(int(row["id"]), []),
            })
            item["detail_brand_count"] = len(item["brands"])
            item["product_only_brand_count"] = sum(
                1 for brand in item["brands"] if brand["product_only"]
            )
            items.append(item)
        return {"items": items, "total": int(total), "limit": limit,
                "offset": offset}

    def get_category_overview(self, category_id):
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            return None
        result = self.list_category_overviews(
            category_id=category_id, limit=1
        )
        return result["items"][0] if result["items"] else None

    def create_global_category(self, name, **actor):
        """Create a global category without claiming that a brand uses it."""
        name = catalog_name(name)
        if not name:
            raise ValueError("Название категории обязательно.")
        self.database.initialize()
        with self.database.transaction() as connection:
            duplicate = connection.execute(
                "SELECT * FROM erp_categories WHERE normalized_name = ? "
                "ORDER BY id LIMIT 1",
                (normalized_name(name),),
            ).fetchone()
            if duplicate is not None:
                raise DuplicateCatalogValueError(
                    "Категория «{}» уже существует.".format(
                        duplicate["name"]
                    ),
                    self._category(duplicate),
                )
            owner = connection.execute(
                "SELECT id FROM erp_brands WHERE active = 1 ORDER BY id LIMIT 1"
            ).fetchone()
            if owner is None:
                raise CatalogReferenceError(
                    "Сначала создайте хотя бы один бренд."
                )
            now = utc_now()
            connection.execute(
                "INSERT INTO erp_categories "
                "(brand_id, name, normalized_name, active, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (owner["id"], name, normalized_name(name), now, now),
            )
            row = connection.execute(
                "SELECT * FROM erp_categories WHERE normalized_name = ?",
                (normalized_name(name),),
            ).fetchone()
            AuditJournal(self.database).record(
                "category", row["id"], "created", row["name"],
                "Создана в центре управления категориями",
                metadata={"global_category_created": True,
                          "relation_action": "unlinked"},
                connection=connection, **actor
            )
            return self._category(row)

    def category_delete_plan(self, category_id, connection=None):
        if connection is None:
            self.database.initialize()
            with self.database.connect() as managed_connection:
                return self.category_delete_plan(
                    category_id, connection=managed_connection
                )
        category_id = int(category_id)
        if category_id == 0:
            raise CatalogReferenceError(
                "Системную категорию «Без категории» нельзя удалить."
            )
        category = connection.execute(
            "SELECT * FROM erp_categories WHERE id = ? AND active = 1",
            (category_id,),
        ).fetchone()
        if category is None:
            raise CatalogReferenceError("Категория не найдена.")
        references = {}
        for key, table in (
            ("products", "catalog_excel_products"),
            ("receipt_rows", "catalog_excel_receipt_rows"),
            ("sale_items", "erp_sale_items"),
            ("receipt_items", "erp_receipt_items"),
        ):
            references[key] = int(connection.execute(
                "SELECT COUNT(*) FROM {} WHERE category_id = ?".format(table),
                (category_id,),
            ).fetchone()[0])
        references["brand_relations"] = int(connection.execute(
            "SELECT COUNT(*) FROM erp_brand_categories WHERE category_id = ?",
            (category_id,),
        ).fetchone()[0])
        references["audit_events"] = int(connection.execute(
            "SELECT COUNT(*) FROM erp_audit_events "
            "WHERE entity_type = 'category' AND entity_id = ?",
            (str(category_id),),
        ).fetchone()[0])
        references["normalization_mappings"] = int(connection.execute(
            "SELECT COUNT(*) FROM erp_catalog_normalization_audit "
            "WHERE entity_type = 'category' AND canonical_id = ?",
            (str(category_id),),
        ).fetchone()[0])
        active_products = int(connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_products "
            "WHERE category_id = ? AND active = 1",
            (category_id,),
        ).fetchone()[0])
        return {
            "id": category_id,
            "name": category["name"],
            "active_product_count": active_products,
            "requires_transfer": active_products > 0,
            "delete_mode": "archive",
            "references": references,
        }

    def move_products_and_archive_category(
        self, category_id, target_category_id=None, **actor
    ):
        """Atomically reassign active products and archive their old category."""
        self.database.initialize()
        with self.database.transaction() as connection:
            plan = self.category_delete_plan(category_id, connection=connection)
            target = None
            if target_category_id not in (None, "", 0, "0"):
                target = connection.execute(
                    "SELECT * FROM erp_categories WHERE id = ? AND active = 1",
                    (int(target_category_id),),
                ).fetchone()
                if target is None:
                    raise CatalogReferenceError(
                        "Категория для переноса не найдена."
                    )
                if int(target["id"]) == int(category_id):
                    raise CatalogReferenceError(
                        "Выберите другую категорию для переноса."
                    )
            if plan["requires_transfer"] and target_category_id in (None, ""):
                raise CatalogReferenceError(
                    "Сначала выберите категорию для переноса товаров."
                )
            target_id = int(target["id"]) if target is not None else None
            target_name = target["name"] if target is not None else None
            if plan["requires_transfer"]:
                connection.execute(
                    "UPDATE catalog_excel_products SET category_id = ?, "
                    "excel_category = ?, updated_at = ? "
                    "WHERE category_id = ? AND active = 1",
                    (target_id, target_name, utc_now(), int(category_id)),
                )
                if target is not None:
                    connection.execute(
                        "INSERT OR IGNORE INTO erp_brand_categories "
                        "(brand_id, category_id, created_at) "
                        "SELECT DISTINCT brand_id, ?, ? "
                        "FROM catalog_excel_products WHERE active = 1 "
                        "AND category_id = ? AND brand_id IS NOT NULL",
                        (target_id, utc_now(), target_id),
                    )
            connection.execute(
                "DELETE FROM erp_brand_categories WHERE category_id = ?",
                (int(category_id),),
            )
            cursor = connection.execute(
                "UPDATE erp_categories SET active = 0, updated_at = ? "
                "WHERE id = ? AND active = 1",
                (utc_now(), int(category_id)),
            )
            if cursor.rowcount != 1:
                raise CatalogReferenceError("Категория не найдена.")
            AuditJournal(self.database).record(
                "category", category_id, "deleted", plan["name"],
                "Категория архивирована",
                metadata={
                    "products_moved": plan["active_product_count"],
                    "target_category_id": target_id,
                    "target_category_name": target_name or "Без категории",
                    "references_preserved": plan["references"],
                },
                connection=connection, **actor
            )
            return {**plan, "target_category_id": target_id,
                    "target_category_name": target_name or "Без категории"}

    def delete_empty_category(self, category_id, **actor):
        """Delete one category only when no product or history references it."""
        self.database.initialize()
        category_id = int(category_id)
        if category_id == 0:
            raise CatalogReferenceError(
                "Системную категорию «Без категории» нельзя удалить."
            )
        try:
            with self.database.transaction() as connection:
                category = connection.execute(
                    "SELECT * FROM erp_categories WHERE id = ? AND active = 1",
                    (category_id,),
                ).fetchone()
                if category is None:
                    raise CatalogReferenceError("Категория не найдена.")

                product_count = int(connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_products "
                    "WHERE category_id = ?",
                    (category_id,),
                ).fetchone()[0])
                if product_count:
                    raise CatalogReferenceError(
                        "Нельзя удалить категорию, пока в ней находятся товары. "
                        "Сначала перенесите товары в другую категорию или в "
                        "«Без категории»."
                    )

                historical_count = 0
                for table in (
                    "catalog_excel_receipt_rows",
                    "erp_sale_items",
                    "erp_receipt_items",
                ):
                    historical_count += int(connection.execute(
                        "SELECT COUNT(*) FROM {} WHERE category_id = ?".format(
                            table
                        ),
                        (category_id,),
                    ).fetchone()[0])
                if historical_count:
                    raise CatalogReferenceError(
                        "Нельзя удалить категорию, потому что она используется "
                        "в истории продаж или приходов."
                    )

                connection.execute(
                    "DELETE FROM erp_brand_categories WHERE category_id = ?",
                    (category_id,),
                )
                AuditJournal(self.database).record(
                    "category",
                    category_id,
                    "deleted",
                    category["name"],
                    "Пустая категория удалена",
                    metadata={"hard_deleted": True, "products_preserved": True},
                    connection=connection,
                    **actor
                )
                cursor = connection.execute(
                    "DELETE FROM erp_categories WHERE id = ? AND active = 1",
                    (category_id,),
                )
                if cursor.rowcount != 1:
                    raise CatalogReferenceError("Категория не найдена.")
                return {
                    "id": category_id,
                    "name": category["name"],
                    "deleted": True,
                    "product_count": 0,
                }
        except sqlite3.IntegrityError as error:
            raise CatalogReferenceError(
                "Нельзя удалить категорию: с ней связаны данные ERP."
            ) from error

    def delete_category_with_products(
        self, category_id, expected_product_count, failure_hook=None, **actor
    ):
        """Archive a category and its active product cards atomically."""
        self.database.initialize()
        category_id = int(category_id)
        if category_id == 0:
            raise CatalogReferenceError(
                "Системную категорию «Без категории» нельзя удалить."
            )
        try:
            expected_product_count = int(expected_product_count)
        except (TypeError, ValueError):
            raise CatalogReferenceError(
                "Обновите подтверждение удаления категории."
            )
        if expected_product_count < 0:
            raise CatalogReferenceError(
                "Обновите подтверждение удаления категории."
            )

        with self.database.transaction() as connection:
            plan = self.category_delete_plan(category_id, connection=connection)
            if plan["active_product_count"] != expected_product_count:
                raise CatalogReferenceError(
                    "Состав категории изменился. Обновите подтверждение удаления."
                )
            changed_at = utc_now()
            connection.execute(
                "UPDATE catalog_excel_products SET active = 0, "
                "deleted_at = COALESCE(deleted_at, ?), updated_at = ? "
                "WHERE category_id = ? AND active = 1",
                (changed_at, changed_at, category_id),
            )
            connection.execute(
                "DELETE FROM erp_brand_categories WHERE category_id = ?",
                (category_id,),
            )
            cursor = connection.execute(
                "UPDATE erp_categories SET active = 0, updated_at = ? "
                "WHERE id = ? AND active = 1",
                (changed_at, category_id),
            )
            if cursor.rowcount != 1:
                raise CatalogReferenceError("Категория не найдена.")
            if failure_hook:
                failure_hook(connection)
            AuditJournal(self.database).record(
                "category", category_id, "deleted", plan["name"],
                "Категория и товары удалены из активного каталога",
                metadata={
                    "products_archived": plan["active_product_count"],
                    "references_preserved": plan["references"],
                },
                connection=connection, **actor
            )
            return {
                **plan,
                "deleted": True,
                "product_count": plan["active_product_count"],
                "products_archived": plan["active_product_count"],
            }

    def list_category_options(
        self,
        brand_id=None,
        query="",
        limit=50,
        only_used_by_brand=False,
    ):
        """Return global categories, optionally limited to a brand's products."""
        self.database.initialize()
        where = ["c.active = 1"]
        parameters = []
        query = catalog_search_key(query)
        if query:
            where.append(
                "catalog_search_key(c.normalized_name) LIKE ? ESCAPE '\\'"
            )
            parameters.append(catalog_prefix_pattern(query))
        selected_brand_id = (
            int(brand_id) if brand_id not in (None, "") else None
        )
        with self.database.connect() as connection:
            if query:
                register_catalog_search(connection)
            rows = connection.execute(
                "SELECT * FROM ("
                "SELECT c.id, c.brand_id, c.name, c.normalized_name, "
                "c.active, b.name AS brand_name, "
                "COUNT(p.id) AS product_count, "
                "COALESCE(SUM(p.stock), 0) AS global_stock_total, "
                "COALESCE(SUM(CASE WHEN p.brand_id = ? "
                "OR (? = 0 AND p.brand_id IS NULL) "
                "THEN p.stock ELSE 0 END), 0) AS selected_stock_total, "
                "MAX(CASE WHEN p.brand_id = ? "
                "OR (? = 0 AND p.brand_id IS NULL) "
                "THEN 1 ELSE 0 END) OR EXISTS ("
                "SELECT 1 FROM erp_brand_categories bc "
                "WHERE bc.brand_id = ? AND bc.category_id = c.id"
                ") AS used_by_brand "
                "FROM erp_categories c "
                "JOIN erp_brands b ON b.id = c.brand_id "
                "LEFT JOIN catalog_excel_products p "
                "ON p.category_id = c.id AND p.active = 1 "
                "WHERE " + " AND ".join(where) + " "
                "GROUP BY c.id UNION ALL "
                "SELECT 0 AS id, 0 AS brand_id, "
                "'Без категории' AS name, 'без категории' AS normalized_name, "
                "1 AS active, 'Без бренда' AS brand_name, "
                "COUNT(p.id) AS product_count, "
                "COALESCE(SUM(p.stock), 0) AS global_stock_total, "
                "COALESCE(SUM(CASE WHEN p.brand_id = ? "
                "OR (? = 0 AND p.brand_id IS NULL) "
                "THEN p.stock ELSE 0 END), 0) AS selected_stock_total, "
                "MAX(CASE WHEN p.brand_id = ? "
                "OR (? = 0 AND p.brand_id IS NULL) "
                "THEN 1 ELSE 0 END) AS used_by_brand "
                "FROM catalog_excel_products p "
                "WHERE p.active = 1 AND p.category_id IS NULL "
                "GROUP BY p.category_id "
                "HAVING COUNT(p.id) > 0 "
                "AND (? = '' OR 'без категории' LIKE ? ESCAPE '\\')) "
                "AS category_rows "
                "ORDER BY used_by_brand DESC, "
                "name COLLATE NOCASE, id",
                [
                    selected_brand_id,
                    selected_brand_id,
                    selected_brand_id,
                    selected_brand_id,
                    selected_brand_id,
                ] + parameters + [
                    selected_brand_id,
                    selected_brand_id,
                    selected_brand_id,
                    selected_brand_id,
                    query,
                    catalog_prefix_pattern(query),
                ],
            ).fetchall()

        grouped = {}
        for row in rows:
            key = normalized_name(row["name"])
            current = grouped.get(key)
            if current is None:
                current = {
                    "canonical": row,
                    "category_ids": [],
                    "product_count": 0,
                    "global_stock_total": 0.0,
                    "selected_stock_total": 0.0,
                    "used_by_brand": False,
                }
                grouped[key] = current
            current["category_ids"].append(int(row["id"]))
            if int(row["id"]) < int(current["canonical"]["id"]):
                current["canonical"] = row
            current["product_count"] += int(row["product_count"])
            current["global_stock_total"] += float(
                row["global_stock_total"] or 0
            )
            current["selected_stock_total"] += float(
                row["selected_stock_total"] or 0
            )
            current["used_by_brand"] = (
                current["used_by_brand"] or bool(row["used_by_brand"])
            )

        available = grouped.values()
        if only_used_by_brand:
            available = (
                item for item in available if item["used_by_brand"]
            )
        ordered = sorted(
            available,
            key=lambda item: (
                not item["used_by_brand"],
                normalized_name(item["canonical"]["name"]),
                int(item["canonical"]["id"]),
            ),
        )
        result = []
        for item in ordered[:max(1, min(int(limit), 100))]:
            prepared = dict(item["canonical"])
            prepared["product_count"] = item["product_count"]
            prepared["stock_total"] = (
                item["selected_stock_total"]
                if selected_brand_id is not None
                else item["global_stock_total"]
            )
            result.append({
                **self._category(prepared),
                "category_ids": sorted(set(item["category_ids"])),
            })
        return result

    def category_compatibility_groups(self):
        """Return canonical IDs plus legacy aliases for each logical category."""
        self.database.initialize()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, normalized_name, active "
                "FROM erp_categories ORDER BY id"
            ).fetchall()

        grouped = {}
        for row in rows:
            key = normalized_name(row["name"])
            item = grouped.setdefault(key, {
                "id": None,
                "name": row["name"],
                "normalized_name": key,
                "category_ids": [],
            })
            category_id = int(row["id"])
            item["category_ids"].append(category_id)
            if bool(row["active"]) and (
                item["id"] is None or category_id < item["id"]
            ):
                item["id"] = category_id
                item["name"] = row["name"]

        result = []
        for item in grouped.values():
            if item["id"] is None:
                item["id"] = min(item["category_ids"])
            item["category_ids"] = sorted(set(item["category_ids"]))
            result.append(item)
        return sorted(
            result,
            key=lambda item: (item["normalized_name"], item["id"]),
        )

    @staticmethod
    def _product_filter_sql(
        query="",
        brand_id=None,
        category_id=None,
        include_archived=False,
        in_stock=False,
    ):
        where = []
        parameters = []
        if not include_archived:
            where.append("p.active = 1")
        if brand_id not in (None, ""):
            if int(brand_id) == 0:
                where.append("p.brand_id IS NULL")
            else:
                where.append("p.brand_id = ?")
                parameters.append(int(brand_id))
        if category_id not in (None, ""):
            if int(category_id) == 0:
                where.append("p.category_id IS NULL")
            else:
                where.append(
                    "c.normalized_name = ("
                    "SELECT selected.normalized_name FROM erp_categories selected "
                    "WHERE selected.id = ?)"
                )
                parameters.append(int(category_id))
        if in_stock:
            where.append("p.stock > 0")
        query = catalog_search_key(query)
        if query:
            where.append(
                "(catalog_search_key(p.excel_name_raw) LIKE ? ESCAPE '\\' OR "
                "catalog_search_key(p.excel_article) LIKE ? ESCAPE '\\' OR "
                "catalog_search_key(cp.barcode) LIKE ? ESCAPE '\\')"
            )
            pattern = catalog_prefix_pattern(query)
            parameters.extend([pattern, pattern, pattern])
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        return where_sql, parameters

    def count_products(
        self,
        query="",
        brand_id=None,
        category_id=None,
        include_archived=False,
        in_stock=False,
    ):
        self.database.initialize()
        where_sql, parameters = self._product_filter_sql(
            query=query,
            brand_id=brand_id,
            category_id=category_id,
            include_archived=include_archived,
            in_stock=in_stock,
        )
        with self.database.connect() as connection:
            if catalog_search_key(query):
                register_catalog_search(connection)
            return int(connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products p "
                "LEFT JOIN erp_categories c ON c.id = p.category_id "
                "LEFT JOIN catalog_products cp "
                "ON cp.id = p.bitrix_catalog_product_id"
                + where_sql,
                parameters,
            ).fetchone()[0])

    def list_products(
        self,
        query="",
        brand_id=None,
        category_id=None,
        limit=50,
        include_archived=False,
        in_stock=False,
    ):
        self.database.initialize()
        where_sql, parameters = self._product_filter_sql(
            query=query,
            brand_id=brand_id,
            category_id=category_id,
            include_archived=include_archived,
            in_stock=in_stock,
        )
        parameters.append(max(1, min(int(limit), 200)))
        with self.database.connect() as connection:
            if catalog_search_key(query):
                register_catalog_search(connection)
            rows = connection.execute(
                "SELECT p.id, p.excel_name_raw AS name, "
                "COALESCE(p.excel_article, '') AS article, "
                "COALESCE(cp.barcode, '') AS barcode, "
                "COALESCE(p.moysklad_product_id, mm.moysklad_product_id, '') "
                "AS moysklad_product_id, "
                "CASE WHEN COALESCE("
                "p.moysklad_product_id, mm.moysklad_product_id, '') = '' "
                "THEN 1 ELSE 0 END AS can_create_moysklad, "
                "p.brand_id, COALESCE(("
                "SELECT MIN(canonical.id) FROM erp_categories canonical "
                "WHERE canonical.active = 1 "
                "AND canonical.normalized_name = c.normalized_name"
                "), p.category_id) AS category_id, "
                "COALESCE(b.name, '') AS brand, "
                "COALESCE(c.name, '') AS category, "
                "COALESCE(p.cell, '') AS cell, p.stock, p.active, "
                "COALESCE(p.bitrix_thumbnail_url, "
                "p.bitrix_primary_image_url, '') AS bitrix_image_url "
                "FROM catalog_excel_products p "
                "LEFT JOIN erp_brands b ON b.id = p.brand_id "
                "LEFT JOIN erp_categories c ON c.id = p.category_id "
                "LEFT JOIN catalog_products cp "
                "ON cp.id = p.bitrix_catalog_product_id "
                "LEFT JOIN catalog_moysklad_mappings mm "
                "ON mm.product_id = cp.id AND mm.confirmed = 1"
                + where_sql
                + " ORDER BY p.excel_name_raw COLLATE NOCASE, p.id LIMIT ?",
                parameters,
            ).fetchall()
        return [self._product(row) for row in rows]

    def legacy_links(self, entity_type, entity_ids):
        if entity_type not in {"sale", "receipt"}:
            raise ValueError("Неизвестный тип исторической записи.")
        ids = sorted({
            str(value)
            for value in entity_ids
            if str(value or "").strip()
        })
        if not ids:
            return {}
        self.database.initialize()
        result = {}
        with self.database.connect() as connection:
            for offset in range(0, len(ids), 500):
                chunk = ids[offset:offset + 500]
                placeholders = ", ".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT entity_id, position_index, product_id "
                    "FROM erp_legacy_catalog_links "
                    "WHERE entity_type = ? AND entity_id IN ({})".format(
                        placeholders
                    ),
                    [entity_type] + chunk,
                ).fetchall()
                result.update({
                    (row["entity_id"], int(row["position_index"])): str(
                        row["product_id"]
                    )
                    for row in rows
                })
        return result

    def get_product(self, product_id, include_archived=False):
        self.database.initialize()
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return None
        where_active = "" if include_archived else " AND p.active = 1"
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT p.id, p.excel_name_raw AS name, "
                "COALESCE(p.excel_article, '') AS article, "
                "COALESCE(cp.barcode, '') AS barcode, "
                "COALESCE(p.moysklad_product_id, mm.moysklad_product_id, '') "
                "AS moysklad_product_id, "
                "CASE WHEN COALESCE("
                "p.moysklad_product_id, mm.moysklad_product_id, '') = '' "
                "THEN 1 ELSE 0 END AS can_create_moysklad, "
                "p.brand_id, COALESCE(("
                "SELECT MIN(canonical.id) FROM erp_categories canonical "
                "WHERE canonical.active = 1 "
                "AND canonical.normalized_name = c.normalized_name"
                "), p.category_id) AS category_id, "
                "COALESCE(b.name, '') AS brand, "
                "COALESCE(c.name, '') AS category, "
                "COALESCE(p.cell, '') AS cell, p.stock, p.active, "
                "COALESCE(p.bitrix_thumbnail_url, "
                "p.bitrix_primary_image_url, '') AS bitrix_image_url "
                "FROM catalog_excel_products p "
                "LEFT JOIN erp_brands b ON b.id = p.brand_id "
                "LEFT JOIN erp_categories c ON c.id = p.category_id "
                "LEFT JOIN catalog_products cp "
                "ON cp.id = p.bitrix_catalog_product_id "
                "LEFT JOIN catalog_moysklad_mappings mm "
                "ON mm.product_id = cp.id AND mm.confirmed = 1 "
                "WHERE p.id = ?" + where_active,
                (product_id,),
            ).fetchone()
        return self._product(row) if row else None

    def products_by_ids(self, product_ids, include_archived=True):
        ids = []
        for value in product_ids:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        ids = sorted(set(ids))
        if not ids:
            return {}
        self.database.initialize()
        active_sql = "" if include_archived else " AND p.active = 1"
        products = {}
        with self.database.connect() as connection:
            for offset in range(0, len(ids), 500):
                chunk = ids[offset:offset + 500]
                placeholders = ", ".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT p.id, p.excel_name_raw AS name, "
                    "COALESCE(p.excel_article, '') AS article, "
                    "COALESCE(cp.barcode, '') AS barcode, "
                    "COALESCE(p.moysklad_product_id, mm.moysklad_product_id, '') "
                    "AS moysklad_product_id, "
                    "CASE WHEN COALESCE("
                    "p.moysklad_product_id, mm.moysklad_product_id, '') = '' "
                    "THEN 1 ELSE 0 END AS can_create_moysklad, "
                    "p.brand_id, COALESCE(("
                    "SELECT MIN(canonical.id) FROM erp_categories canonical "
                    "WHERE canonical.active = 1 "
                    "AND canonical.normalized_name = c.normalized_name"
                    "), p.category_id) AS category_id, "
                    "COALESCE(b.name, '') AS brand, "
                    "COALESCE(c.name, '') AS category, "
                    "COALESCE(p.cell, '') AS cell, p.stock, p.active, "
                    "COALESCE(p.bitrix_thumbnail_url, "
                    "p.bitrix_primary_image_url, '') AS bitrix_image_url "
                    "FROM catalog_excel_products p "
                    "LEFT JOIN erp_brands b ON b.id = p.brand_id "
                    "LEFT JOIN erp_categories c ON c.id = p.category_id "
                    "LEFT JOIN catalog_products cp "
                    "ON cp.id = p.bitrix_catalog_product_id "
                    "LEFT JOIN catalog_moysklad_mappings mm "
                    "ON mm.product_id = cp.id AND mm.confirmed = 1 "
                    "WHERE p.id IN ({}){}".format(
                        placeholders,
                        active_sql,
                    ),
                    chunk,
                ).fetchall()
                products.update({
                    str(row["id"]): self._product(row)
                    for row in rows
                })
        return products

    def create_brand(self, name, **actor):
        name = catalog_name(name)
        if not name:
            raise ValueError("Название бренда обязательно.")
        self.database.initialize()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM erp_brands WHERE normalized_name = ?",
                (normalized_name(name),),
            ).fetchone()
            if existing is None:
                existing = next(
                    (
                        row
                        for row in connection.execute(
                            "SELECT * FROM erp_brands ORDER BY id"
                        ).fetchall()
                        if potential_alias_key(row["name"])
                        == potential_alias_key(name)
                    ),
                    None,
                )
            if existing is not None:
                raise DuplicateCatalogValueError(
                    "Такой бренд уже существует: {}.".format(existing["name"]),
                    self._brand(existing),
                )
            row = ensure_brand(connection, name=name)
            AuditJournal(self.database).record(
                "brand", row["id"], "created", row["name"],
                after={"name": row["name"]}, connection=connection, **actor
            )
            return self._brand(row)

    def set_moysklad_product_id(self, product_id, moysklad_product_id):
        product_id = int(product_id)
        moysklad_product_id = display_name(moysklad_product_id)
        if not moysklad_product_id:
            raise CatalogReferenceError("МойСклад не вернул ID товара.")
        self.database.initialize()
        with self.database.transaction() as connection:
            occupied = connection.execute(
                "SELECT id FROM catalog_excel_products "
                "WHERE active = 1 AND moysklad_product_id = ? AND id <> ?",
                (moysklad_product_id, product_id),
            ).fetchone()
            if occupied is not None:
                raise CatalogReferenceError(
                    "Товар МоегоСклада уже связан с другой карточкой."
                )
            cursor = connection.execute(
                "UPDATE catalog_excel_products "
                "SET moysklad_product_id = ?, moysklad_sync_status = 'linked', "
                "updated_at = ? WHERE id = ? AND active = 1",
                (moysklad_product_id, utc_now(), product_id),
            )
            if cursor.rowcount != 1:
                raise CatalogReferenceError("Товар не найден.")
        return self.get_product(product_id)

    def create_category(self, brand_id, name):
        name = catalog_name(name)
        if not name:
            raise ValueError("Название категории обязательно.")
        self.database.initialize()
        with self.database.transaction() as connection:
            brand = ensure_brand(
                connection,
                brand_id=brand_id,
                create=False,
            )
            if brand is None:
                raise CatalogReferenceError("Сначала выберите бренд.")
            existing = connection.execute(
                "SELECT c.*, b.name AS brand_name "
                "FROM erp_categories c "
                "JOIN erp_brands b ON b.id = c.brand_id "
                "WHERE c.normalized_name = ? ORDER BY c.id LIMIT 1",
                (normalized_name(name),),
            ).fetchone()
            if existing is None:
                existing = next(
                    (
                        row
                        for row in connection.execute(
                            "SELECT c.*, b.name AS brand_name "
                            "FROM erp_categories c "
                            "JOIN erp_brands b ON b.id = c.brand_id "
                            "ORDER BY c.id",
                        ).fetchall()
                        if potential_alias_key(row["name"])
                        == potential_alias_key(name)
                    ),
                    None,
                )
            if existing is not None:
                prepared = self._category(existing)
                raise DuplicateCatalogValueError(
                    "Такая категория уже существует: {}.".format(
                        existing["name"]
                    ),
                    prepared,
                )
            row = ensure_category(
                connection,
                brand["id"],
                name=name,
            )
            prepared = self._category(row)
            prepared["brand_name"] = brand["name"]
            return prepared

    def rename_brand(self, brand_id, name, **actor):
        name = catalog_name(name)
        if not name:
            raise ValueError("Название бренда обязательно.")
        self.database.initialize()
        with self.database.transaction() as connection:
            current = ensure_brand(
                connection,
                brand_id=brand_id,
                create=False,
            )
            duplicate = connection.execute(
                "SELECT * FROM erp_brands "
                "WHERE normalized_name = ? AND id <> ?",
                (normalized_name(name), current["id"]),
            ).fetchone()
            if duplicate is None:
                duplicate = next(
                    (
                        row
                        for row in connection.execute(
                            "SELECT * FROM erp_brands WHERE id <> ? ORDER BY id",
                            (current["id"],),
                        ).fetchall()
                        if potential_alias_key(row["name"])
                        == potential_alias_key(name)
                    ),
                    None,
                )
            if duplicate is not None:
                raise DuplicateCatalogValueError(
                    "Такой бренд уже существует: {}.".format(duplicate["name"]),
                    self._brand(duplicate),
                )
            old_name = current["name"]
            connection.execute(
                "UPDATE erp_brands SET name = ?, normalized_name = ?, "
                "updated_at = ? WHERE id = ?",
                (name, normalized_name(name), utc_now(), current["id"]),
            )
            connection.execute(
                "UPDATE catalog_excel_products SET excel_brand = ?, updated_at = ? "
                "WHERE brand_id = ? AND active = 1",
                (name, utc_now(), current["id"]),
            )
            AuditJournal(self.database).record(
                "brand", current["id"], "updated", name,
                before={"name": old_name}, after={"name": name},
                connection=connection, **actor
            )
            return self._brand(
                connection.execute(
                    "SELECT * FROM erp_brands WHERE id = ?",
                    (current["id"],),
                ).fetchone()
            )

    def rename_category(self, category_id, name, **actor):
        name = catalog_name(name)
        if not name:
            raise ValueError("Название категории обязательно.")
        self.database.initialize()
        with self.database.transaction() as connection:
            if int(category_id) == 0:
                raise CatalogReferenceError(
                    "Системную категорию «Без категории» нельзя переименовать."
                )
            current = connection.execute(
                "SELECT * FROM erp_categories WHERE id = ? AND active = 1",
                (int(category_id),),
            ).fetchone()
            if current is None:
                raise CatalogReferenceError("Категория не найдена.")
            duplicate = connection.execute(
                "SELECT * FROM erp_categories "
                "WHERE normalized_name = ? AND id <> ? ORDER BY id LIMIT 1",
                (normalized_name(name), current["id"]),
            ).fetchone()
            if duplicate is None:
                duplicate = next(
                    (
                        row
                        for row in connection.execute(
                            "SELECT * FROM erp_categories "
                            "WHERE id <> ? ORDER BY id",
                            (current["id"],),
                        ).fetchall()
                        if potential_alias_key(row["name"])
                        == potential_alias_key(name)
                    ),
                    None,
                )
            if duplicate is not None:
                raise DuplicateCatalogValueError(
                    "Такая категория уже существует: {}.".format(
                        duplicate["name"]
                    ),
                    self._category(duplicate),
                )
            brand_count = connection.execute(
                "SELECT COUNT(*) FROM erp_brand_categories WHERE category_id = ?",
                (current["id"],),
            ).fetchone()[0]
            product_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products "
                "WHERE category_id = ? AND active = 1",
                (current["id"],),
            ).fetchone()[0]
            old_name = current["name"]
            connection.execute(
                "UPDATE erp_categories SET name = ?, normalized_name = ?, "
                "updated_at = ? WHERE id = ?",
                (name, normalized_name(name), utc_now(), current["id"]),
            )
            connection.execute(
                "UPDATE catalog_excel_products SET excel_category = ?, updated_at = ? "
                "WHERE category_id = ? AND active = 1",
                (name, utc_now(), current["id"]),
            )
            AuditJournal(self.database).record(
                "category", current["id"], "updated", name,
                before={"name": old_name}, after={"name": name},
                metadata={
                    "affected_brands": int(brand_count),
                    "affected_products": int(product_count),
                },
                connection=connection, **actor
            )
            return self._category(
                connection.execute(
                    "SELECT * FROM erp_categories WHERE id = ?",
                    (current["id"],),
                ).fetchone()
            )

    def archive_brand(self, brand_id):
        self.database.initialize()
        with self.database.transaction() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products "
                "WHERE brand_id = ? AND active = 1",
                (int(brand_id),),
            ).fetchone()[0]
            if count:
                raise CatalogReferenceError(
                    "Бренд нельзя архивировать: он используется товарами."
                )
            cursor = connection.execute(
                "UPDATE erp_brands SET active = 0, updated_at = ? "
                "WHERE id = ? AND active = 1",
                (utc_now(), int(brand_id)),
            )
            if cursor.rowcount != 1:
                raise CatalogReferenceError("Бренд не найден.")

    def archive_category(self, category_id):
        self.database.initialize()
        with self.database.transaction() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products "
                "WHERE category_id = ? AND active = 1",
                (int(category_id),),
            ).fetchone()[0]
            if count:
                raise CatalogReferenceError(
                    "Категорию нельзя архивировать: она используется товарами."
                )
            cursor = connection.execute(
                "UPDATE erp_categories SET active = 0, updated_at = ? "
                "WHERE id = ? AND active = 1",
                (utc_now(), int(category_id)),
            )
            if cursor.rowcount != 1:
                raise CatalogReferenceError("Категория не найдена.")

    def duplicate_audit(self):
        self.database.initialize()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM erp_catalog_normalization_audit "
                "ORDER BY entity_type, normalized_name, id"
            ).fetchall()
            products = connection.execute(
                "SELECT normalized_name, COUNT(*) AS occurrence_count, "
                "GROUP_CONCAT(id) AS product_ids, "
                "GROUP_CONCAT(excel_name_raw, ' | ') AS variants "
                "FROM catalog_excel_products "
                "WHERE active = 1 AND trim(normalized_name) <> '' "
                "GROUP BY normalized_name HAVING COUNT(*) > 1 "
                "ORDER BY normalized_name"
            ).fetchall()
            brands = connection.execute(
                "SELECT id, name FROM erp_brands ORDER BY id"
            ).fetchall()
        alias_groups = {}
        for brand in brands:
            alias_groups.setdefault(
                potential_alias_key(brand["name"]),
                [],
            ).append({
                "id": int(brand["id"]),
                "name": brand["name"],
            })
        return {
            "linked_variants": [dict(row) for row in rows],
            "ambiguous_products": [dict(row) for row in products],
            "potential_brand_aliases": [
                {
                    "alias_key": key,
                    "items": items,
                    "resolution": "manual_review",
                }
                for key, items in sorted(alias_groups.items())
                if key and len(items) > 1
            ],
        }

    @staticmethod
    def _brand(row):
        stock_total = normalized_stock_value(
            row["stock_total"] if "stock_total" in row.keys() else 0
        )
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "active": bool(row["active"]),
            "product_count": int(
                row["product_count"]
                if "product_count" in row.keys()
                else 0
            ),
            "stock_total": stock_total,
            "stock_display": format_stock_value(stock_total),
        }

    @staticmethod
    def _category(row):
        stock_total = normalized_stock_value(
            row["stock_total"] if "stock_total" in row.keys() else 0
        )
        return {
            "id": int(row["id"]),
            "brand_id": int(row["brand_id"]),
            "name": row["name"],
            "active": bool(row["active"]),
            "brand_name": (
                row["brand_name"]
                if "brand_name" in row.keys()
                else ""
            ),
            "product_count": int(
                row["product_count"]
                if "product_count" in row.keys()
                else 0
            ),
            "stock_total": stock_total,
            "stock_display": format_stock_value(stock_total),
            "used_by_brand": bool(
                row["used_by_brand"]
                if "used_by_brand" in row.keys()
                else False
            ),
        }

    @staticmethod
    def _product(row):
        stock = float(row["stock"] or 0)
        return {
            "id": str(row["id"]),
            "product_id": str(row["id"]),
            "name": row["name"],
            "article": row["article"],
            "barcode": row["barcode"],
            "moysklad_product_id": row["moysklad_product_id"],
            "can_create_moysklad": bool(row["can_create_moysklad"]),
            "brand_id": (
                int(row["brand_id"]) if row["brand_id"] is not None else None
            ),
            "category_id": (
                int(row["category_id"])
                if row["category_id"] is not None
                else None
            ),
            "brand": row["brand"],
            "category": row["category"],
            "cell": row["cell"],
            "stock": stock,
            "stock_display": format_stock_value(stock),
            "image_url": (
                (
                    row["bitrix_image_url"]
                    if "bitrix_image_url" in row.keys()
                    else ""
                )
                or (
                    "/warehouse/product/{}/thumbnail".format(
                        row["moysklad_product_id"]
                    )
                    if row["moysklad_product_id"]
                    else ""
                )
            ),
            "active": bool(row["active"]),
        }
