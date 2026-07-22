"""Internal Excel-authoritative product catalog and stock adjustment batches.

This module writes only to the local Vechasu catalog database. It never calls
Bitrix or MoySklad clients and therefore cannot change either external system.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.services.product_reconciliation import (
    AUTOMATIC_STATUSES,
    article_quality,
    batch_id_for,
    normalize_text,
    text,
)


MATCH_COLUMNS = (
    "match_status", "match_method", "match_confidence", "match_decision",
    "bitrix_link_cardinality", "shared_bitrix_row_count",
    "bitrix_catalog_product_id", "bitrix_external_product_id", "bitrix_xml_id",
    "bitrix_name", "bitrix_brand", "bitrix_category", "bitrix_source_url",
    "bitrix_primary_image_url", "bitrix_thumbnail_url", "bitrix_gallery_json",
    "bitrix_price_amount", "bitrix_price_currency", "bitrix_description",
    "bitrix_properties_json", "bitrix_active",
)

PRODUCT_MUTABLE_COLUMNS = (
    "current_batch_id", "active", "raw_excel_json", "excel_row",
    "excel_name_raw", "normalized_name", "excel_article", "article_quality",
    "excel_brand", "excel_category", "stock", "cell", "stock_source",
    "file_sha256", "match_status", "match_method", "match_confidence",
    "match_decision", "candidates_json", "bitrix_link_cardinality",
    "shared_bitrix_row_count", "bitrix_catalog_product_id",
    "bitrix_external_product_id", "bitrix_xml_id", "bitrix_name",
    "bitrix_brand", "bitrix_category", "bitrix_source_url",
    "bitrix_primary_image_url", "bitrix_thumbnail_url", "bitrix_gallery_json",
    "bitrix_price_amount", "bitrix_price_currency", "bitrix_description",
    "bitrix_properties_json", "bitrix_active", "moysklad_sync_status",
    "updated_at",
)


class BatchBlockedError(ValueError):
    def __init__(self, message, blocked_rows=None):
        ValueError.__init__(self, message)
        self.blocked_rows = list(blocked_rows or [])


class ProductDeleteBlockedError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def source_key_for(row):
    excel_row = int(row.get("excel_row") or 0)
    if excel_row < 2:
        raise BatchBlockedError(
            "Excel row number is required for stable product identity",
            [excel_row],
        )
    return "excel-row:{:08d}".format(excel_row)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed


def _display_property(row):
    value = _load_json(row["display_value_json"], None)
    if value in (None, "", []):
        value = _load_json(row["value_json"], "")
    return value


def load_bitrix_enrichment(connection, catalog_product_id):
    product = connection.execute(
        "SELECT p.*, c.name AS category_name FROM catalog_products p "
        "LEFT JOIN catalog_categories c ON c.id = p.primary_category_id "
        "WHERE p.id = ?",
        (int(catalog_product_id),),
    ).fetchone()
    if product is None:
        raise ValueError("Bitrix catalog product does not exist")

    images = [dict(row) for row in connection.execute(
        "SELECT * FROM catalog_images WHERE product_id = ? "
        "ORDER BY is_primary DESC, sort, id",
        (int(catalog_product_id),),
    ).fetchall()]
    primary = next((item for item in images if item.get("is_primary")), None)
    if primary is None and images:
        primary = images[0]
    previews = [item for item in images if item.get("image_type") == "preview"]
    thumbnail_pool = previews or images
    thumbnail = min(
        thumbnail_pool,
        key=lambda item: (
            int(item.get("file_size") or 10 ** 15),
            int(item.get("width") or 10 ** 9) * int(item.get("height") or 10 ** 9),
            int(item.get("sort") or 500),
            int(item.get("id") or 0),
        ),
    ) if thumbnail_pool else None
    price = connection.execute(
        "SELECT amount, currency FROM catalog_prices WHERE product_id = ? "
        "ORDER BY is_base DESC, id LIMIT 1",
        (int(catalog_product_id),),
    ).fetchone()
    properties = []
    for row in connection.execute(
        "SELECT pr.name, pr.code, pv.value_json, pv.display_value_json "
        "FROM catalog_product_property_values pv "
        "JOIN catalog_properties pr ON pr.id = pv.property_id "
        "WHERE pv.product_id = ? ORDER BY pr.sort, pr.name",
        (int(catalog_product_id),),
    ).fetchall():
        value = _display_property(row)
        if value in (None, "", []):
            continue
        properties.append({"name": row["name"], "code": row["code"], "value": value})
    description_parts = [
        text(product["preview_text"]), text(product["detail_text"]),
    ]
    return {
        "bitrix_catalog_product_id": product["id"],
        "bitrix_external_product_id": product["external_product_id"],
        "bitrix_xml_id": product["external_xml_id"],
        "bitrix_name": product["name"],
        "bitrix_brand": product["brand"],
        "bitrix_category": product["category_name"],
        "bitrix_source_url": product["source_url"],
        "bitrix_primary_image_url": primary.get("original_url") if primary else None,
        "bitrix_thumbnail_url": thumbnail.get("original_url") if thumbnail else None,
        "bitrix_gallery_json": _json(images),
        "bitrix_price_amount": price["amount"] if price else None,
        "bitrix_price_currency": price["currency"] if price else None,
        "bitrix_description": "\n\n".join(part for part in description_parts if part),
        "bitrix_properties_json": _json(properties),
        "bitrix_active": int(bool(product["active"])),
    }


def _empty_enrichment():
    return {
        "bitrix_catalog_product_id": None,
        "bitrix_external_product_id": None,
        "bitrix_xml_id": None,
        "bitrix_name": None,
        "bitrix_brand": None,
        "bitrix_category": None,
        "bitrix_source_url": None,
        "bitrix_primary_image_url": None,
        "bitrix_thumbnail_url": None,
        "bitrix_gallery_json": "[]",
        "bitrix_price_amount": None,
        "bitrix_price_currency": None,
        "bitrix_description": None,
        "bitrix_properties_json": "[]",
        "bitrix_active": None,
    }


def _snapshot(row):
    if row is None:
        return None
    values = dict(row)
    return {column: values.get(column) for column in PRODUCT_MUTABLE_COLUMNS}


def _matching_snapshot(row):
    values = dict(row)
    return {column: values.get(column) for column in MATCH_COLUMNS}


def _restore_columns(connection, product_id, state, columns):
    assignments = ", ".join("{} = ?".format(column) for column in columns)
    connection.execute(
        "UPDATE catalog_excel_products SET {} WHERE id = ?".format(assignments),
        [state.get(column) for column in columns] + [int(product_id)],
    )


def _refresh_link_cardinality(connection, catalog_product_ids=None):
    if catalog_product_ids is None:
        rows = connection.execute(
            "SELECT DISTINCT bitrix_catalog_product_id FROM catalog_excel_products "
            "WHERE active = 1 AND bitrix_catalog_product_id IS NOT NULL"
        ).fetchall()
        catalog_product_ids = [row[0] for row in rows]
    for catalog_product_id in set(filter(None, catalog_product_ids)):
        count = connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_products WHERE active = 1 "
            "AND bitrix_catalog_product_id = ?",
            (int(catalog_product_id),),
        ).fetchone()[0]
        connection.execute(
            "UPDATE catalog_excel_products SET bitrix_link_cardinality = ?, "
            "shared_bitrix_row_count = ? WHERE active = 1 "
            "AND bitrix_catalog_product_id = ?",
            ("many_to_one" if count > 1 else "one_to_one", count, int(catalog_product_id)),
        )


class ExcelProductBatchService:
    """Apply and exactly roll back local initial-balance batches."""

    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    def apply(self, results, file_sha256, source_filename, sheet_name="Импорт"):
        results = [dict(result) for result in results]
        linked_rows = {}
        for result in results:
            if result.get("match_status") not in AUTOMATIC_STATUSES:
                continue
            product_id = result.get("product_id")
            if product_id is not None:
                linked_rows[product_id] = linked_rows.get(product_id, 0) + 1
        for result in results:
            product_id = result.get("product_id")
            if result.get("match_status") in AUTOMATIC_STATUSES and product_id is not None:
                count = linked_rows[product_id]
                result["bitrix_link_cardinality"] = (
                    "many_to_one" if count > 1 else "one_to_one"
                )
                result["shared_bitrix_row_count"] = count
        blocked = [
            result for result in results
            if result.get("match_status") == "invalid"
        ]
        if blocked:
            raise BatchBlockedError(
                "Excel batch is blocked by invalid rows",
                [result.get("excel_row") for result in blocked],
            )
        source_keys = [source_key_for(result) for result in results]
        if len(source_keys) != len(set(source_keys)):
            raise BatchBlockedError("Excel batch contains repeated Excel row numbers")
        for result in results:
            try:
                stock = float(result.get("stock") or 0)
            except (TypeError, ValueError):
                raise BatchBlockedError("Excel batch contains an invalid stock value")
            if stock < 0 or not result.get("stock_valid", True):
                raise BatchBlockedError("Excel batch contains an invalid stock value")

        batch_id = batch_id_for(file_sha256)
        self.database.initialize()
        with self.database.transaction() as connection:
            existing_batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE file_sha256 = ?",
                (file_sha256,),
            ).fetchone()
            if existing_batch is not None:
                return self._batch_result(connection, existing_batch, already_applied=True)

            now = utc_now()
            previous_batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE status = 'active' "
                "ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
            total_stock = sum(float(result.get("stock") or 0) for result in results)
            positive_rows = sum(float(result.get("stock") or 0) > 0 for result in results)
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, sheet_name, row_count, total_stock, "
                "positive_rows, zero_rows, status, previous_batch_id, created_at, applied_at, "
                "details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (
                    batch_id, file_sha256, source_filename, sheet_name, len(results),
                    total_stock, positive_rows, len(results) - positive_rows,
                    previous_batch["id"] if previous_batch else None, now, now,
                    _json({
                        "writes": "internal_catalog_only",
                        "external_writes": 0,
                        "product_identity": "excel_row",
                        "duplicate_names": "separate_cards",
                        "stocks": "kept_separate",
                    }),
                ),
            )
            if previous_batch:
                connection.execute(
                    "UPDATE catalog_excel_batches SET status = 'superseded' WHERE id = ?",
                    (previous_batch["id"],),
                )

            incoming_keys = set(source_keys)
            active_products = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE active = 1"
            ).fetchall()
            for product in active_products:
                if product["source_key"] in incoming_keys:
                    continue
                before = _snapshot(product)
                after = dict(before)
                after.update({
                    "active": 0, "current_batch_id": batch_id, "stock": 0.0,
                    "updated_at": now,
                })
                _restore_columns(connection, product["id"], after, PRODUCT_MUTABLE_COLUMNS)
                self._record_change(
                    connection, batch_id, product["id"], product["source_key"], None,
                    "deactivated", False, before, after, product["stock"], 0.0,
                    after["match_status"], now,
                )

            for result, source_key in zip(results, source_keys):
                product = connection.execute(
                    "SELECT * FROM catalog_excel_products WHERE source_key = ?",
                    (source_key,),
                ).fetchone()
                before = _snapshot(product)
                state = self._state_for_result(
                    connection, result, batch_id, file_sha256, now, product,
                )
                if product is None:
                    columns = ("source_key", "created_batch_id", "created_at") + PRODUCT_MUTABLE_COLUMNS
                    values = [source_key, batch_id, now] + [state[column] for column in PRODUCT_MUTABLE_COLUMNS]
                    placeholders = ", ".join("?" for _ in columns)
                    connection.execute(
                        "INSERT INTO catalog_excel_products ({}) VALUES ({})".format(
                            ", ".join(columns), placeholders
                        ),
                        values,
                    )
                    product_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                    created_product = True
                    stock_before = 0.0
                else:
                    product_id = product["id"]
                    created_product = False
                    stock_before = float(product["stock"])
                    _restore_columns(connection, product_id, state, PRODUCT_MUTABLE_COLUMNS)
                self._record_change(
                    connection, batch_id, product_id, source_key, result["excel_row"],
                    "excel_row", created_product, before, state, stock_before,
                    float(result.get("stock") or 0), state["match_status"], now,
                )

            _refresh_link_cardinality(connection)

            batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            return self._batch_result(connection, batch, already_applied=False)

    def rollback(self, batch_id):
        self.database.initialize()
        with self.database.transaction() as connection:
            batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise ValueError("Excel batch does not exist")
            if batch["status"] == "rolled_back":
                return self._batch_result(connection, batch, already_applied=True)
            if batch["status"] != "active":
                raise ValueError("Only the active Excel batch can be rolled back")
            now = utc_now()
            changes = connection.execute(
                "SELECT * FROM catalog_excel_batch_rows WHERE batch_id = ? ORDER BY id DESC",
                (batch_id,),
            ).fetchall()
            for change in changes:
                product = connection.execute(
                    "SELECT * FROM catalog_excel_products WHERE source_key = ?",
                    (change["source_key"],),
                ).fetchone()
                if product is None:
                    continue
                stock_before = float(product["stock"])
                previous = _load_json(change["previous_state_json"], None)
                original_operation = connection.execute(
                    "SELECT id FROM catalog_excel_stock_operations "
                    "WHERE batch_id = ? AND product_id = ? "
                    "AND operation_type = 'initial_excel_adjustment' ORDER BY created_at LIMIT 1",
                    (batch_id, product["id"]),
                ).fetchone()
                stock_after = float(previous.get("stock") or 0) if previous else 0.0
                if stock_before != stock_after:
                    self._record_operation(
                        connection, batch_id, product["id"], "excel_batch_rollback",
                        stock_before, stock_after, now,
                        original_operation["id"] if original_operation else None,
                        {"source_key": change["source_key"]},
                    )
                if change["created_product"] and self._can_delete_created_product(
                    connection, product["id"], batch_id
                ):
                    connection.execute(
                        "DELETE FROM catalog_excel_products WHERE id = ?", (product["id"],)
                    )
                elif change["created_product"]:
                    retained = _snapshot(product)
                    retained.update({"active": 0, "stock": 0.0, "updated_at": now})
                    _restore_columns(
                        connection, product["id"], retained, PRODUCT_MUTABLE_COLUMNS
                    )
                elif previous is not None:
                    _restore_columns(
                        connection, product["id"], previous, PRODUCT_MUTABLE_COLUMNS
                    )
            connection.execute(
                "UPDATE catalog_excel_batches SET status = 'rolled_back', rolled_back_at = ? "
                "WHERE id = ?", (now, batch_id),
            )
            if batch["previous_batch_id"]:
                connection.execute(
                    "UPDATE catalog_excel_batches SET status = 'active' WHERE id = ?",
                    (batch["previous_batch_id"],),
                )
            batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            return self._batch_result(connection, batch, already_applied=False)

    def _state_for_result(self, connection, result, batch_id, file_sha256, now, product):
        automatic = result.get("match_status") in AUTOMATIC_STATUSES
        enrichment = _empty_enrichment()
        if automatic:
            enrichment = load_bitrix_enrichment(connection, result["product_id"])
        decision = "automatic" if automatic else (
            "pending" if result.get("match_status") == "ambiguous" else "unmatched"
        )
        cardinality = result.get("bitrix_link_cardinality") or (
            "one_to_one" if automatic else "unlinked"
        )
        shared_row_count = int(result.get("shared_bitrix_row_count") or (1 if automatic else 0))
        if (
            not automatic and product is not None
            and product["match_decision"] in {"manual", "manual_not_in_bitrix"}
        ):
            enrichment = {column: product[column] for column in MATCH_COLUMNS if column.startswith("bitrix_")}
            result = dict(result)
            result["match_status"] = product["match_status"]
            result["match_method"] = product["match_method"]
            result["confidence"] = product["match_confidence"]
            decision = product["match_decision"]
            cardinality = product["bitrix_link_cardinality"]
            shared_row_count = product["shared_bitrix_row_count"]
        raw_excel = {
            "excel_row": result.get("excel_row"),
            "excel_name": result.get("excel_name"),
            "excel_name_raw": result.get("excel_name_raw"),
            "excel_name_number_format": result.get("excel_name_number_format"),
            "excel_name_normalization": result.get("excel_name_normalization"),
            "excel_article": result.get("excel_article"),
            "excel_brand": result.get("excel_brand"),
            "category": result.get("category"),
            "stock": result.get("stock"),
            "cell": result.get("cell"),
        }
        state = {
            "current_batch_id": batch_id,
            "active": 1,
            "raw_excel_json": _json(raw_excel),
            "excel_row": int(result.get("excel_row") or 0),
            "excel_name_raw": text(result.get("excel_name")),
            "normalized_name": normalize_text(result.get("excel_name")),
            "excel_article": text(result.get("excel_article")) or None,
            "article_quality": result.get("article_quality") or article_quality(result.get("excel_article")),
            "excel_brand": text(result.get("excel_brand")),
            "excel_category": text(result.get("category")) or None,
            "stock": float(result.get("stock") or 0),
            "cell": text(result.get("cell")) or None,
            "stock_source": "excel",
            "file_sha256": file_sha256,
            "match_status": result.get("match_status") or "not_found",
            "match_method": result.get("match_method") or "none",
            "match_confidence": float(result.get("confidence") or 0),
            "match_decision": decision,
            "candidates_json": _json(result.get("alternatives") or []),
            "bitrix_link_cardinality": cardinality,
            "shared_bitrix_row_count": shared_row_count,
            "moysklad_sync_status": "not_linked",
            "updated_at": now,
        }
        state.update(enrichment)
        return state

    def _record_change(self, connection, batch_id, product_id, source_key, excel_row,
                       row_kind, created_product, before, after, stock_before,
                       stock_after, match_status, now):
        difference = float(stock_after) - float(stock_before)
        connection.execute(
            "INSERT INTO catalog_excel_batch_rows ("
            "batch_id, product_id, source_key, excel_row, row_kind, created_product, "
            "previous_state_json, applied_state_json, stock_before, stock_after, "
            "stock_difference, match_status, bitrix_link_cardinality, "
            "shared_bitrix_row_count, bitrix_xml_id, operation_result, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_id, product_id, source_key, excel_row, row_kind,
                int(bool(created_product)), _json(before) if before is not None else None,
                _json(after), float(stock_before), float(stock_after), difference,
                match_status, after.get("bitrix_link_cardinality"),
                int(after.get("shared_bitrix_row_count") or 0), after.get("bitrix_xml_id"),
                "adjusted" if difference else "already_at_target", now,
            ),
        )
        if difference:
            self._record_operation(
                connection, batch_id, product_id, "initial_excel_adjustment",
                stock_before, stock_after, now, None,
                {
                    "source_key": source_key, "excel_row": excel_row,
                    "row_kind": row_kind, "bitrix_xml_id": after.get("bitrix_xml_id"),
                },
            )

    @staticmethod
    def _can_delete_created_product(connection, product_id, batch_id):
        manual_uses = connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_match_audit WHERE product_id = ?",
            (int(product_id),),
        ).fetchone()[0]
        other_batch_rows = connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_batch_rows "
            "WHERE product_id = ? AND batch_id <> ?",
            (int(product_id), batch_id),
        ).fetchone()[0]
        other_operations = connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_stock_operations "
            "WHERE product_id = ? AND batch_id <> ?",
            (int(product_id), batch_id),
        ).fetchone()[0]
        return not (manual_uses or other_batch_rows or other_operations)

    @staticmethod
    def _record_operation(connection, batch_id, product_id, operation_type,
                          stock_before, stock_after, now, reversal_of, details):
        connection.execute(
            "INSERT INTO catalog_excel_stock_operations ("
            "id, batch_id, product_id, operation_type, stock_before, stock_after, "
            "stock_difference, reversal_of, created_at, details_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), batch_id, product_id, operation_type,
                float(stock_before), float(stock_after),
                float(stock_after) - float(stock_before), reversal_of, now, _json(details),
            ),
        )

    @staticmethod
    def _batch_result(connection, batch, already_applied):
        batch = dict(batch)
        batch["already_applied"] = bool(already_applied)
        batch["active_cards"] = connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_products WHERE active = 1"
        ).fetchone()[0]
        batch["active_stock"] = connection.execute(
            "SELECT COALESCE(SUM(stock), 0) FROM catalog_excel_products WHERE active = 1"
        ).fetchone()[0]
        batch["operation_rows"] = connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_stock_operations WHERE batch_id = ?",
            (batch["id"],),
        ).fetchone()[0]
        return batch


class ExcelProductCatalog:
    """Read the active Excel assortment and manage local Bitrix links."""

    def __init__(self, database=None):
        self.database = database or CatalogDatabase()

    def list_products(self, query="", brand="", category="", cell="",
                      match_status="all", hide_zero=False, sort_by="name",
                      sort_dir="asc", page=1, per_page=50):
        self.database.initialize()
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 5000))
        allowed_sort_fields = {
            "name": "p.excel_name_raw",
            "article": "COALESCE(p.excel_article, '')",
            "brand": "COALESCE(p.excel_brand, '')",
            "category": "COALESCE(p.excel_category, '')",
            "stock": "p.stock",
            "cell": "COALESCE(p.cell, '')",
            "created_at": "p.created_at",
            "price": "CAST(COALESCE(NULLIF(p.bitrix_price_amount, ''), '0') AS REAL)",
            "match_status": "p.match_status",
        }
        sort_by = sort_by if sort_by in allowed_sort_fields else "name"
        sort_dir = sort_dir if sort_dir in {"asc", "desc"} else "asc"
        where = ["p.active = 1", "b.status = 'active'", "p.current_batch_id = b.id"]
        parameters = []
        if query:
            escaped_query = text(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = "%{}%".format(escaped_query)
            where.append(
                "(p.excel_name_raw LIKE ? ESCAPE '\\' OR p.bitrix_name LIKE ? ESCAPE '\\' "
                "OR p.excel_article LIKE ? ESCAPE '\\' OR p.bitrix_xml_id LIKE ? ESCAPE '\\' "
                "OR p.excel_brand LIKE ? ESCAPE '\\' OR p.bitrix_brand LIKE ? ESCAPE '\\' "
                "OR p.cell LIKE ? ESCAPE '\\')"
            )
            parameters.extend([pattern] * 7)
        if brand:
            where.append("COALESCE(p.excel_brand, '') = ?")
            parameters.append(brand)
        if category:
            where.append(
                "(COALESCE(p.excel_category, '') = ? OR "
                "substr(COALESCE(p.excel_category, ''), "
                "1, length(?) + 1) = ? || '/')"
            )
            parameters.extend([category, category, category])
        if cell:
            if cell == "Без ячейки":
                where.append("trim(COALESCE(p.cell, '')) = ''")
            else:
                where.append("trim(COALESCE(p.cell, '')) = ?")
                parameters.append(cell)
        if hide_zero:
            where.append("p.stock > 0")
        if match_status == "requires_mapping":
            where.append("p.match_status = 'ambiguous'")
        elif match_status != "all":
            where.append("p.match_status = ?")
            parameters.append(match_status)
        where_sql = " WHERE " + " AND ".join(where)
        select_sql = (
            "SELECT p.*, b.source_filename, b.applied_at, b.row_count AS batch_row_count "
            "FROM catalog_excel_products p JOIN catalog_excel_batches b "
            "ON b.id = p.current_batch_id"
        )
        with self.database.connect() as connection:
            active_batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE status = 'active' "
                "ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
            total = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products p "
                "JOIN catalog_excel_batches b ON b.id = p.current_batch_id" + where_sql,
                parameters,
            ).fetchone()[0]
            stats = dict(connection.execute(
                "SELECT COUNT(*) AS positions, COALESCE(SUM(p.stock), 0) AS total_stock, "
                "SUM(CASE WHEN p.stock > 0 THEN 1 ELSE 0 END) AS positive_positions, "
                "SUM(CASE WHEN p.stock <= 0 THEN 1 ELSE 0 END) AS zero_positions, "
                "SUM(CASE WHEN p.bitrix_catalog_product_id IS NOT NULL THEN 1 ELSE 0 END) "
                "AS matched_positions FROM catalog_excel_products p "
                "JOIN catalog_excel_batches b ON b.id = p.current_batch_id" + where_sql,
                parameters,
            ).fetchone())
            order_sql = " ORDER BY {} {}".format(
                allowed_sort_fields[sort_by], sort_dir.upper()
            )
            rows = connection.execute(
                select_sql + where_sql + order_sql + ", p.excel_row ASC, p.id ASC LIMIT ? OFFSET ?",
                parameters + [per_page, (page - 1) * per_page],
            ).fetchall()
            brands = [row[0] for row in connection.execute(
                "SELECT DISTINCT COALESCE(p.excel_brand, '') AS value "
                "FROM catalog_excel_products p JOIN catalog_excel_batches b "
                "ON b.id = p.current_batch_id WHERE p.active = 1 AND b.status = 'active' "
                "AND trim(COALESCE(p.excel_brand, '')) <> '' "
                "ORDER BY value"
            ).fetchall()]
            categories = [row[0] for row in connection.execute(
                "SELECT DISTINCT COALESCE(p.excel_category, '') AS value "
                "FROM catalog_excel_products p JOIN catalog_excel_batches b "
                "ON b.id = p.current_batch_id WHERE p.active = 1 AND b.status = 'active' "
                "AND trim(COALESCE(p.excel_category, '')) <> '' "
                "ORDER BY value"
            ).fetchall()]
            category_groups = [dict(row) for row in connection.execute(
                "SELECT COALESCE(p.excel_category, '') AS name, "
                "COUNT(*) AS count FROM catalog_excel_products p "
                "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                "WHERE p.active = 1 AND b.status = 'active' "
                "AND trim(COALESCE(p.excel_category, '')) <> '' "
                "GROUP BY name ORDER BY name"
            ).fetchall()]
            cell_groups = [dict(row) for row in connection.execute(
                "SELECT CASE WHEN trim(COALESCE(p.cell, '')) = '' THEN 'Без ячейки' "
                "ELSE trim(p.cell) END AS cell, COUNT(*) AS count, "
                "COALESCE(SUM(p.stock), 0) AS stock FROM catalog_excel_products p "
                "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                "WHERE p.active = 1 AND b.status = 'active' "
                "GROUP BY CASE WHEN trim(COALESCE(p.cell, '')) = '' "
                "THEN 'Без ячейки' ELSE trim(p.cell) END "
                "ORDER BY CASE WHEN trim(COALESCE(p.cell, '')) = '' THEN 1 ELSE 0 END, cell"
            ).fetchall()]
            status_counts = {
                row["match_status"]: row["count"] for row in connection.execute(
                    "SELECT p.match_status, COUNT(*) AS count FROM catalog_excel_products p "
                    "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                    "WHERE p.active = 1 AND b.status = 'active' GROUP BY p.match_status"
                ).fetchall()
            }
        items = [self._prepare_product(dict(row)) for row in rows]
        return {
            "items": items, "total": total, "page": page, "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
            "brands": brands, "categories": categories,
            "category_groups": category_groups, "cell_groups": cell_groups,
            "stats": stats, "sort_by": sort_by, "sort_dir": sort_dir,
            "status_counts": status_counts,
            "active_batch": dict(active_batch) if active_batch else None,
        }

    def get_product(self, product_id):
        self.database.initialize()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT p.*, b.source_filename, b.applied_at FROM catalog_excel_products p "
                "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                "WHERE p.id = ? AND p.active = 1 AND b.status = 'active'",
                (int(product_id),),
            ).fetchone()
        return self._prepare_product(dict(row)) if row else None

    def create_product(self, name, article="", brand="", category="", cell=""):
        name = text(name)
        if not name:
            raise ValueError("Название товара обязательно.")
        article = text(article)
        brand = text(brand)
        category = text(category)
        cell = text(cell)
        self.database.initialize()
        with self.database.transaction() as connection:
            batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE status = 'active' "
                "ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
            if batch is None:
                raise ValueError("Сначала оформите приход из Excel.")
            now = utc_now()
            excel_row = connection.execute(
                "SELECT COALESCE(MAX(excel_row), 1) + 1 FROM catalog_excel_products"
            ).fetchone()[0]
            source_key = "manual:{}".format(uuid.uuid4())
            enrichment = _empty_enrichment()
            columns = (
                "source_key", "created_batch_id", "current_batch_id", "active",
                "raw_excel_json", "excel_row", "excel_name_raw", "normalized_name",
                "excel_article", "article_quality", "excel_brand", "excel_category",
                "stock", "cell", "stock_source", "file_sha256", "match_status",
                "match_method", "match_confidence", "match_decision", "candidates_json",
                "bitrix_link_cardinality", "shared_bitrix_row_count",
            ) + tuple(enrichment) + ("moysklad_sync_status", "created_at", "updated_at")
            values = (
                source_key, batch["id"], batch["id"], 1,
                _json({"source": "manual", "name": name, "article": article,
                       "brand": brand, "category": category, "cell": cell}),
                excel_row, name, normalize_text(name), article or None,
                article_quality(article), brand, category or None, 0.0, cell or None,
                "manual", batch["file_sha256"], "not_found", "manual_create", 0.0,
                "unmatched", "[]", "unlinked", 0,
            ) + tuple(enrichment.values()) + ("not_linked", now, now)
            connection.execute(
                "INSERT INTO catalog_excel_products ({}) VALUES ({})".format(
                    ", ".join(columns), ", ".join("?" for _ in columns)
                ),
                values,
            )
            product_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        return self.get_product(product_id)

    def update_product(self, product_id, name=None, article=None, brand=None,
                       category=None, cell=None):
        self.database.initialize()
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ? AND active = 1",
                (int(product_id),),
            ).fetchone()
            if product is None:
                raise ValueError("Товар не найден.")
            values = dict(product)
            if name is not None:
                values["excel_name_raw"] = text(name)
                if not values["excel_name_raw"]:
                    raise ValueError("Название товара обязательно.")
                values["normalized_name"] = normalize_text(values["excel_name_raw"])
            if article is not None:
                values["excel_article"] = text(article) or None
                values["article_quality"] = article_quality(values["excel_article"])
            if brand is not None:
                values["excel_brand"] = text(brand)
            if category is not None:
                values["excel_category"] = text(category) or None
            if cell is not None:
                values["cell"] = text(cell) or None
            raw_excel = _load_json(values.get("raw_excel_json"), {})
            raw_excel.update({
                "excel_name": values["excel_name_raw"],
                "excel_article": values["excel_article"],
                "excel_brand": values["excel_brand"],
                "category": values["excel_category"],
                "cell": values["cell"],
            })
            values["raw_excel_json"] = _json(raw_excel)
            values["updated_at"] = utc_now()
            _restore_columns(connection, product_id, values, PRODUCT_MUTABLE_COLUMNS)
        return self.get_product(product_id)

    def archive_product(self, product_id):
        self.database.initialize()
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ? AND active = 1",
                (int(product_id),),
            ).fetchone()
            if product is None:
                raise ValueError("Товар не найден.")
            if float(product["stock"] or 0) != 0:
                raise ProductDeleteBlockedError(
                    "Товар с ненулевым остатком нельзя удалить."
                )
            connection.execute(
                "UPDATE catalog_excel_products SET active = 0, updated_at = ? WHERE id = ?",
                (utc_now(), int(product_id)),
            )

    def delete_product(self, product_id, external_references=None):
        """Delete only an unreferenced zero-stock card.

        Existing batch, receipt, stock and match records are audit data. They
        must never be cascaded or detached just to remove a card from the UI.
        """
        external_references = list(external_references or [])
        self.database.initialize()
        try:
            with self.database.transaction() as connection:
                product = connection.execute(
                    "SELECT * FROM catalog_excel_products WHERE id = ? AND active = 1",
                    (int(product_id),),
                ).fetchone()
                if product is None:
                    raise ValueError("Товар не найден.")

                references = list(external_references)
                reference_checks = (
                    ("приход", "catalog_excel_receipt_rows"),
                    ("операция прихода", "catalog_excel_receipt_operations"),
                    ("строка batch-аудита", "catalog_excel_batch_rows"),
                    ("складская операция", "catalog_excel_stock_operations"),
                    ("история сопоставления", "catalog_excel_match_audit"),
                )
                for label, table in reference_checks:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM {} WHERE product_id = ?".format(table),
                        (int(product_id),),
                    ).fetchone()[0]
                    if count:
                        references.append(label)

                if float(product["stock"] or 0) != 0:
                    references.append("ненулевой остаток")
                if references:
                    raise ProductDeleteBlockedError(
                        "Товар нельзя удалить: сохранены связанные данные ({0}).".format(
                            ", ".join(sorted(set(references)))
                        )
                    )

                connection.execute(
                    "DELETE FROM catalog_excel_products WHERE id = ?", (int(product_id),)
                )
        except sqlite3.IntegrityError:
            raise ProductDeleteBlockedError(
                "Товар нельзя удалить: он используется в связанных документах или аудите."
            )

    def confirm_match(self, product_id, catalog_product_id):
        return self._change_match(product_id, "confirm_bitrix", catalog_product_id)

    def mark_not_in_bitrix(self, product_id):
        return self._change_match(product_id, "not_in_bitrix")

    def unlink(self, product_id):
        return self._change_match(product_id, "unlink")

    def undo_last_match_change(self, product_id):
        self.database.initialize()
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ? AND active = 1",
                (int(product_id),),
            ).fetchone()
            if product is None:
                raise ValueError("Excel product does not exist")
            audit = connection.execute(
                "SELECT a.* FROM catalog_excel_match_audit a "
                "WHERE a.product_id = ? AND a.action <> 'undo' "
                "AND NOT EXISTS (SELECT 1 FROM catalog_excel_match_audit u "
                "WHERE u.reverses_audit_id = a.id) ORDER BY a.id DESC LIMIT 1",
                (int(product_id),),
            ).fetchone()
            if audit is None:
                raise ValueError("There is no manual match change to undo")
            before = _matching_snapshot(product)
            restored = _load_json(audit["previous_state_json"], None)
            if restored is None:
                raise ValueError("Manual match audit is incomplete")
            _restore_columns(connection, product_id, restored, MATCH_COLUMNS)
            _refresh_link_cardinality(connection, [
                product["bitrix_catalog_product_id"],
                restored.get("bitrix_catalog_product_id"),
            ])
            connection.execute(
                "UPDATE catalog_excel_products SET updated_at = ? WHERE id = ?",
                (utc_now(), int(product_id)),
            )
            restored = _matching_snapshot(connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ?", (int(product_id),)
            ).fetchone())
            connection.execute(
                "INSERT INTO catalog_excel_match_audit ("
                "product_id, batch_id, action, previous_state_json, new_state_json, "
                "reverses_audit_id, created_at) VALUES (?, ?, 'undo', ?, ?, ?, ?)",
                (
                    int(product_id), product["current_batch_id"], _json(before),
                    _json(restored), audit["id"], utc_now(),
                ),
            )
        return self.get_product(product_id)

    def _change_match(self, product_id, action, catalog_product_id=None):
        self.database.initialize()
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ? AND active = 1",
                (int(product_id),),
            ).fetchone()
            if product is None:
                raise ValueError("Excel product does not exist")
            previous = _matching_snapshot(product)
            previous_catalog_product_id = product["bitrix_catalog_product_id"]
            state = _empty_enrichment()
            if action == "confirm_bitrix":
                state.update(load_bitrix_enrichment(connection, catalog_product_id))
                state.update({
                    "match_status": "manual_match", "match_method": "manual_confirmation",
                    "match_confidence": 1.0, "match_decision": "manual",
                    "bitrix_link_cardinality": "one_to_one",
                    "shared_bitrix_row_count": 1,
                })
            elif action == "not_in_bitrix":
                state.update({
                    "match_status": "not_in_bitrix", "match_method": "manual_confirmation",
                    "match_confidence": 1.0, "match_decision": "manual_not_in_bitrix",
                    "bitrix_link_cardinality": "unlinked",
                    "shared_bitrix_row_count": 0,
                })
            elif action == "unlink":
                candidates = _load_json(product["candidates_json"], [])
                state.update({
                    "match_status": "ambiguous" if candidates else "not_found",
                    "match_method": "manual_unlink", "match_confidence": 0.0,
                    "match_decision": "pending" if candidates else "unmatched",
                    "bitrix_link_cardinality": "unlinked",
                    "shared_bitrix_row_count": 0,
                })
            else:
                raise ValueError("Unsupported manual match action")
            _restore_columns(connection, product_id, state, MATCH_COLUMNS)
            _refresh_link_cardinality(connection, [
                previous_catalog_product_id,
                state.get("bitrix_catalog_product_id"),
            ])
            connection.execute(
                "UPDATE catalog_excel_products SET updated_at = ? WHERE id = ?",
                (utc_now(), int(product_id)),
            )
            state = _matching_snapshot(connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ?", (int(product_id),)
            ).fetchone())
            connection.execute(
                "INSERT INTO catalog_excel_match_audit ("
                "product_id, batch_id, action, previous_state_json, new_state_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    int(product_id), product["current_batch_id"], action,
                    _json(previous), _json(state), utc_now(),
                ),
            )
        return self.get_product(product_id)

    @staticmethod
    def _prepare_product(item):
        item["display_name"] = item.get("bitrix_name") or item.get("excel_name_raw")
        item["display_brand"] = item.get("bitrix_brand") or item.get("excel_brand")
        item["display_category"] = item.get("bitrix_category") or item.get("excel_category")
        item["candidates"] = _load_json(item.get("candidates_json"), [])
        item["gallery"] = _load_json(item.get("bitrix_gallery_json"), [])
        item["properties"] = _load_json(item.get("bitrix_properties_json"), [])
        item["raw_excel"] = _load_json(item.get("raw_excel_json"), {})
        return item
