"""Read-only category diagnostics and scoped canonical relation repair."""

import json
import sqlite3
from datetime import datetime, timezone

from app.services.shared_catalog import catalog_name, normalized_name


class CategoryIntegrityError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dicts(rows):
    return [dict(row) for row in rows]


def _stock(value):
    number = float(value or 0)
    return int(number) if number.is_integer() else number


def _table_has_column(connection, table, column):
    return any(
        row["name"] == column
        for row in connection.execute(
            "PRAGMA table_info({})".format(table)
        ).fetchall()
    )


def category_reference_counts(connection, category_ids, brand_id=None):
    if not category_ids:
        return []
    placeholders = ", ".join("?" for _ in category_ids)
    result = []
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    for table_row in tables:
        table = table_row["name"]
        if not _table_has_column(connection, table, "category_id"):
            continue
        row = connection.execute(
            "SELECT COUNT(*) AS row_count FROM {} "
            "WHERE category_id IN ({})".format(table, placeholders),
            category_ids,
        ).fetchone()
        item = {"table": table, "rows": int(row["row_count"])}
        if brand_id is not None and _table_has_column(
            connection, table, "brand_id"
        ):
            scoped = connection.execute(
                "SELECT COUNT(*) AS row_count FROM {} WHERE brand_id = ? "
                "AND category_id IN ({})".format(table, placeholders),
                [int(brand_id)] + category_ids,
            ).fetchone()
            item["target_brand_rows"] = int(scoped["row_count"])
        result.append(item)
    return result


def global_duplicate_audit(connection):
    categories = _dicts(connection.execute(
        "SELECT c.*, b.name AS owner_brand_name FROM erp_categories c "
        "JOIN erp_brands b ON b.id = c.brand_id "
        "WHERE c.active = 1 ORDER BY c.id"
    ).fetchall())
    grouped = {}
    for category in categories:
        grouped.setdefault(normalized_name(category["name"]), []).append(category)
    report = []
    for key in sorted(grouped):
        variants = grouped[key]
        if not key or len(variants) < 2:
            continue
        ids = [int(item["id"]) for item in variants]
        placeholders = ", ".join("?" for _ in ids)
        metrics = _dicts(connection.execute(
            "SELECT p.brand_id, b.name AS brand, p.category_id, "
            "COUNT(p.id) AS product_count, "
            "COALESCE(SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END), 0) "
            "AS nonzero_count, COALESCE(SUM(p.stock), 0) AS stock_total "
            "FROM catalog_excel_products p "
            "LEFT JOIN erp_brands b ON b.id = p.brand_id "
            "WHERE p.active = 1 AND p.category_id IN ({}) "
            "GROUP BY p.brand_id, p.category_id "
            "ORDER BY b.name COLLATE NOCASE, p.category_id".format(placeholders),
            ids,
        ).fetchall())
        links = _dicts(connection.execute(
            "SELECT bc.brand_id, b.name AS brand, bc.category_id "
            "FROM erp_brand_categories bc "
            "JOIN erp_brands b ON b.id = bc.brand_id "
            "WHERE bc.category_id IN ({}) "
            "ORDER BY b.name COLLATE NOCASE, bc.category_id".format(placeholders),
            ids,
        ).fetchall())
        report.append({
            "normalized_name": key,
            "canonical_candidate_id": min(ids),
            "categories": [{
                "id": int(item["id"]),
                "name": item["name"],
                "stored_normalized_name": item["normalized_name"],
                "owner_brand_id": int(item["brand_id"]),
                "owner_brand": item["owner_brand_name"],
            } for item in variants],
            "brand_metrics": [{
                **item,
                "brand_id": (
                    int(item["brand_id"])
                    if item["brand_id"] is not None else None
                ),
                "category_id": int(item["category_id"]),
                "product_count": int(item["product_count"]),
                "nonzero_count": int(item["nonzero_count"]),
                "stock_total": _stock(item["stock_total"]),
            } for item in metrics],
            "brand_relations": links,
        })
    return report


class CategoryIntegrityRepair:
    def __init__(self, connection, brand_name, category_name):
        self.connection = connection
        self.brand_name = catalog_name(brand_name)
        self.category_name = catalog_name(category_name)

    def _brand(self):
        matches = [
            row for row in self.connection.execute(
                "SELECT * FROM erp_brands WHERE active = 1 ORDER BY id"
            ).fetchall()
            if normalized_name(row["name"]) == normalized_name(self.brand_name)
        ]
        if len(matches) != 1:
            raise CategoryIntegrityError(
                "Expected one active brand, found {}.".format(len(matches))
            )
        return matches[0]

    def _categories(self):
        matches = [
            row for row in self.connection.execute(
                "SELECT * FROM erp_categories WHERE active = 1 ORDER BY id"
            ).fetchall()
            if normalized_name(row["name"]) == normalized_name(self.category_name)
        ]
        if len(matches) < 2:
            raise CategoryIntegrityError(
                "Expected duplicate category records, found {}.".format(
                    len(matches)
                )
            )
        return matches

    def _snapshot(self, brand_id):
        products = _dicts(self.connection.execute(
            "SELECT id, brand_id, category_id, stock, active "
            "FROM catalog_excel_products WHERE brand_id = ? ORDER BY id",
            (int(brand_id),),
        ).fetchall())
        return {
            "products": products,
            "product_ids": [int(item["id"]) for item in products],
            "product_count": len(products),
            "active_product_count": sum(int(item["active"]) for item in products),
            "nonzero_count": sum(
                int(item["active"] and float(item["stock"] or 0) != 0)
                for item in products
            ),
            "stock_total": _stock(sum(
                float(item["stock"] or 0)
                for item in products if item["active"]
            )),
        }

    def diagnose(self, include_global_audit=True):
        brand = self._brand()
        categories = self._categories()
        category_ids = [int(row["id"]) for row in categories]
        canonical = categories[0]
        aliases = categories[1:]
        placeholders = ", ".join("?" for _ in category_ids)
        category_metrics = _dicts(self.connection.execute(
            "SELECT p.category_id, COUNT(p.id) AS product_count, "
            "COALESCE(SUM(CASE WHEN p.stock != 0 THEN 1 ELSE 0 END), 0) "
            "AS nonzero_count, COALESCE(SUM(p.stock), 0) AS stock_total "
            "FROM catalog_excel_products p WHERE p.active = 1 "
            "AND p.brand_id = ? AND p.category_id IN ({}) "
            "GROUP BY p.category_id ORDER BY p.category_id".format(placeholders),
            [int(brand["id"])] + category_ids,
        ).fetchall())
        uncategorized = dict(self.connection.execute(
            "SELECT COUNT(id) AS product_count, "
            "COALESCE(SUM(CASE WHEN stock != 0 THEN 1 ELSE 0 END), 0) "
            "AS nonzero_count, COALESCE(SUM(stock), 0) AS stock_total "
            "FROM catalog_excel_products WHERE active = 1 AND brand_id = ? "
            "AND category_id IS NULL",
            (int(brand["id"]),),
        ).fetchone())
        snapshot = self._snapshot(brand["id"])
        alias_ids = [int(row["id"]) for row in aliases]
        alias_placeholders = ", ".join("?" for _ in alias_ids)
        products_to_move = self.connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_products WHERE brand_id = ? "
            "AND category_id IN ({})".format(alias_placeholders),
            [int(brand["id"])] + alias_ids,
        ).fetchone()[0]
        links_to_delete = self.connection.execute(
            "SELECT COUNT(*) FROM erp_brand_categories WHERE brand_id = ? "
            "AND category_id IN ({})".format(alias_placeholders),
            [int(brand["id"])] + alias_ids,
        ).fetchone()[0]
        canonical_link = self.connection.execute(
            "SELECT COUNT(*) FROM erp_brand_categories "
            "WHERE brand_id = ? AND category_id = ?",
            (int(brand["id"]), int(canonical["id"])),
        ).fetchone()[0]
        return {
            "generated_at": utc_now(),
            "brand": {"id": int(brand["id"]), "name": brand["name"]},
            "normalized_category_name": normalized_name(self.category_name),
            "categories": [{
                "id": int(row["id"]), "brand_id": int(row["brand_id"]),
                "name": row["name"],
                "stored_normalized_name": row["normalized_name"],
                "created_at": row["created_at"],
            } for row in categories],
            "canonical": {
                "id": int(canonical["id"]),
                "reason": (
                    "The oldest global ID is the application canonical ID; "
                    "it minimizes relation changes and preserves existing references."
                ),
            },
            "category_metrics": [{
                "category_id": int(item["category_id"]),
                "product_count": int(item["product_count"]),
                "nonzero_count": int(item["nonzero_count"]),
                "stock_total": _stock(item["stock_total"]),
            } for item in category_metrics],
            "uncategorized": {
                "product_count": int(uncategorized["product_count"]),
                "nonzero_count": int(uncategorized["nonzero_count"]),
                "stock_total": _stock(uncategorized["stock_total"]),
            },
            "before": snapshot,
            "references": category_reference_counts(
                self.connection, category_ids, brand_id=brand["id"]
            ),
            "dry_run": {
                "catalog_excel_products_updated": int(products_to_move),
                "erp_brand_categories_inserted": int(not canonical_link),
                "erp_brand_categories_deleted": int(links_to_delete),
                "history_rows_updated": 0,
                "category_records_deleted_or_archived": 0,
            },
            "global_audit": (
                global_duplicate_audit(self.connection)
                if include_global_audit else []
            ),
        }

    def apply(self):
        report = self.diagnose(include_global_audit=True)
        brand_id = report["brand"]["id"]
        canonical_id = report["canonical"]["id"]
        alias_ids = [
            item["id"] for item in report["categories"]
            if item["id"] != canonical_id
        ]
        placeholders = ", ".join("?" for _ in alias_ids)
        canonical_name = next(
            item["name"] for item in report["categories"]
            if item["id"] == canonical_id
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            product_cursor = self.connection.execute(
                "UPDATE catalog_excel_products "
                "SET category_id = ?, excel_category = ? "
                "WHERE brand_id = ? AND category_id IN ({})".format(
                    placeholders
                ),
                [canonical_id, canonical_name, brand_id] + alias_ids,
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO erp_brand_categories "
                "(brand_id, category_id, created_at) VALUES (?, ?, ?)",
                (brand_id, canonical_id, utc_now()),
            )
            delete_cursor = self.connection.execute(
                "DELETE FROM erp_brand_categories WHERE brand_id = ? "
                "AND category_id IN ({})".format(placeholders),
                [brand_id] + alias_ids,
            )
            after = self._snapshot(brand_id)
            before = report["before"]
            if before["product_ids"] != after["product_ids"]:
                raise CategoryIntegrityError("Product IDs changed during repair.")
            if before["product_count"] != after["product_count"]:
                raise CategoryIntegrityError("Product count changed during repair.")
            if before["stock_total"] != after["stock_total"]:
                raise CategoryIntegrityError("Stock changed during repair.")
            if any(item["brand_id"] != brand_id for item in after["products"]):
                raise CategoryIntegrityError("Product brand changed during repair.")
            remaining = self.connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products WHERE brand_id = ? "
                "AND category_id IN ({})".format(placeholders),
                [brand_id] + alias_ids,
            ).fetchone()[0]
            if remaining:
                raise CategoryIntegrityError("Legacy category relations remain.")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        report["applied_at"] = utc_now()
        report["applied"] = {
            "catalog_excel_products_updated": int(product_cursor.rowcount),
            "erp_brand_categories_deleted": int(delete_cursor.rowcount),
        }
        report["after"] = after
        return report


def connect_database(path, read_only=True):
    database_path = str(path)
    if read_only:
        connection = sqlite3.connect(
            "file:{}?mode=ro".format(database_path), uri=True
        )
    else:
        connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def report_json(report):
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
