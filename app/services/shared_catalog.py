"""Stable shared brand/category/product references for ERP workflows."""

from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase


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


def normalized_name(value):
    return display_name(value).casefold()


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

    name = display_name(name)
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
        row = connection.execute(
            "SELECT * FROM erp_categories WHERE id = ? AND active = 1",
            (category_id,),
        ).fetchone()
        if row is None:
            raise CatalogReferenceError("Категория не найдена.")
        if brand_id and int(row["brand_id"]) != int(brand_id):
            raise CatalogReferenceError(
                "Категория не относится к выбранному бренду."
            )
        return row

    name = display_name(name)
    key = normalized_name(name)
    if not key:
        return None
    if not brand_id:
        raise CatalogReferenceError("Сначала выберите бренд.")
    row = connection.execute(
        "SELECT * FROM erp_categories "
        "WHERE brand_id = ? AND normalized_name = ?",
        (int(brand_id), key),
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
    return connection.execute(
        "SELECT * FROM erp_categories "
        "WHERE brand_id = ? AND normalized_name = ?",
        (int(brand_id), key),
    ).fetchone()


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
        self.database = database or CatalogDatabase()

    def list_brands(self, query="", limit=50, include_archived=False):
        self.database.initialize()
        where = []
        parameters = []
        if not include_archived:
            where.append("b.active = 1")
        query = normalized_name(query)
        if query:
            where.append("b.normalized_name LIKE ?")
            parameters.append("%{}%".format(query))
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        parameters.append(max(1, min(int(limit), 200)))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT b.id, b.name, b.active, COUNT(p.id) AS product_count "
                "FROM erp_brands b "
                "LEFT JOIN catalog_excel_products p "
                "ON p.brand_id = b.id AND p.active = 1"
                + where_sql
                + " GROUP BY b.id ORDER BY b.name COLLATE NOCASE LIMIT ?",
                parameters,
            ).fetchall()
        return [self._brand(row) for row in rows]

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
        query = normalized_name(query)
        if query:
            where.append("c.normalized_name LIKE ?")
            parameters.append("%{}%".format(query))
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        parameters.append(max(1, min(int(limit), 200)))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.id, c.brand_id, c.name, c.active, "
                "b.name AS brand_name, COUNT(p.id) AS product_count "
                "FROM erp_categories c "
                "JOIN erp_brands b ON b.id = c.brand_id "
                "LEFT JOIN catalog_excel_products p "
                "ON p.category_id = c.id AND p.active = 1"
                + where_sql
                + " GROUP BY c.id ORDER BY c.name COLLATE NOCASE LIMIT ?",
                parameters,
            ).fetchall()
        return [self._category(row) for row in rows]

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
        where = []
        parameters = []
        if not include_archived:
            where.append("p.active = 1")
        if brand_id not in (None, ""):
            where.append("p.brand_id = ?")
            parameters.append(int(brand_id))
        if category_id not in (None, ""):
            where.append("p.category_id = ?")
            parameters.append(int(category_id))
        if in_stock:
            where.append("p.stock > 0")
        query = normalized_name(query)
        if query:
            where.append(
                "(p.normalized_name LIKE ? OR "
                "lower(COALESCE(p.excel_article, '')) LIKE ? OR "
                "lower(COALESCE(cp.barcode, '')) LIKE ?)"
            )
            pattern = "%{}%".format(query)
            parameters.extend([pattern, pattern, pattern])
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        parameters.append(max(1, min(int(limit), 100)))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT p.id, p.excel_name_raw AS name, "
                "COALESCE(p.excel_article, '') AS article, "
                "COALESCE(cp.barcode, '') AS barcode, "
                "COALESCE(p.moysklad_product_id, mm.moysklad_product_id, '') "
                "AS moysklad_product_id, "
                "CASE WHEN COALESCE("
                "p.moysklad_product_id, mm.moysklad_product_id, '') = '' "
                "THEN 1 ELSE 0 END AS can_create_moysklad, "
                "p.brand_id, p.category_id, "
                "COALESCE(b.name, '') AS brand, "
                "COALESCE(c.name, '') AS category, "
                "COALESCE(p.cell, '') AS cell, p.stock, p.active "
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
        placeholders = ", ".join("?" for _ in ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT entity_id, position_index, product_id "
                "FROM erp_legacy_catalog_links "
                "WHERE entity_type = ? AND entity_id IN ({})".format(
                    placeholders
                ),
                [entity_type] + ids,
            ).fetchall()
        return {
            (row["entity_id"], int(row["position_index"])): str(
                row["product_id"]
            )
            for row in rows
        }

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
                "p.brand_id, p.category_id, "
                "COALESCE(b.name, '') AS brand, "
                "COALESCE(c.name, '') AS category, "
                "COALESCE(p.cell, '') AS cell, p.stock, p.active "
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
        placeholders = ", ".join("?" for _ in ids)
        active_sql = "" if include_archived else " AND p.active = 1"
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT p.id, p.excel_name_raw AS name, "
                "COALESCE(p.excel_article, '') AS article, "
                "COALESCE(cp.barcode, '') AS barcode, "
                "COALESCE(p.moysklad_product_id, mm.moysklad_product_id, '') "
                "AS moysklad_product_id, "
                "CASE WHEN COALESCE("
                "p.moysklad_product_id, mm.moysklad_product_id, '') = '' "
                "THEN 1 ELSE 0 END AS can_create_moysklad, "
                "p.brand_id, p.category_id, "
                "COALESCE(b.name, '') AS brand, "
                "COALESCE(c.name, '') AS category, "
                "COALESCE(p.cell, '') AS cell, p.stock, p.active "
                "FROM catalog_excel_products p "
                "LEFT JOIN erp_brands b ON b.id = p.brand_id "
                "LEFT JOIN erp_categories c ON c.id = p.category_id "
                "LEFT JOIN catalog_products cp "
                "ON cp.id = p.bitrix_catalog_product_id "
                "LEFT JOIN catalog_moysklad_mappings mm "
                "ON mm.product_id = cp.id AND mm.confirmed = 1 "
                "WHERE p.id IN ({}){}".format(placeholders, active_sql),
                ids,
            ).fetchall()
        return {
            str(row["id"]): self._product(row)
            for row in rows
        }

    def create_brand(self, name):
        name = display_name(name)
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
                "WHERE moysklad_product_id = ? AND id <> ?",
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
        name = display_name(name)
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
                "SELECT * FROM erp_categories "
                "WHERE brand_id = ? AND normalized_name = ?",
                (brand["id"], normalized_name(name)),
            ).fetchone()
            if existing is None:
                existing = next(
                    (
                        row
                        for row in connection.execute(
                            "SELECT * FROM erp_categories "
                            "WHERE brand_id = ? ORDER BY id",
                            (brand["id"],),
                        ).fetchall()
                        if potential_alias_key(row["name"])
                        == potential_alias_key(name)
                    ),
                    None,
                )
            if existing is not None:
                prepared = {
                    **self._category(existing),
                    "brand_name": brand["name"],
                }
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

    def rename_brand(self, brand_id, name):
        name = display_name(name)
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
            connection.execute(
                "UPDATE erp_brands SET name = ?, normalized_name = ?, "
                "updated_at = ? WHERE id = ?",
                (name, normalized_name(name), utc_now(), current["id"]),
            )
            connection.execute(
                "UPDATE catalog_excel_products SET excel_brand = ?, updated_at = ? "
                "WHERE brand_id = ?",
                (name, utc_now(), current["id"]),
            )
            return self._brand(
                connection.execute(
                    "SELECT * FROM erp_brands WHERE id = ?",
                    (current["id"],),
                ).fetchone()
            )

    def rename_category(self, category_id, name):
        name = display_name(name)
        if not name:
            raise ValueError("Название категории обязательно.")
        self.database.initialize()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM erp_categories WHERE id = ? AND active = 1",
                (int(category_id),),
            ).fetchone()
            if current is None:
                raise CatalogReferenceError("Категория не найдена.")
            duplicate = connection.execute(
                "SELECT * FROM erp_categories "
                "WHERE brand_id = ? AND normalized_name = ? AND id <> ?",
                (
                    current["brand_id"],
                    normalized_name(name),
                    current["id"],
                ),
            ).fetchone()
            if duplicate is None:
                duplicate = next(
                    (
                        row
                        for row in connection.execute(
                            "SELECT * FROM erp_categories "
                            "WHERE brand_id = ? AND id <> ? ORDER BY id",
                            (current["brand_id"], current["id"]),
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
            connection.execute(
                "UPDATE erp_categories SET name = ?, normalized_name = ?, "
                "updated_at = ? WHERE id = ?",
                (name, normalized_name(name), utc_now(), current["id"]),
            )
            connection.execute(
                "UPDATE catalog_excel_products SET excel_category = ?, updated_at = ? "
                "WHERE category_id = ?",
                (name, utc_now(), current["id"]),
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
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "active": bool(row["active"]),
            "product_count": int(
                row["product_count"]
                if "product_count" in row.keys()
                else 0
            ),
        }

    @staticmethod
    def _category(row):
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
            "stock_display": str(int(stock)) if stock.is_integer() else "{:g}".format(stock),
            "active": bool(row["active"]),
        }
