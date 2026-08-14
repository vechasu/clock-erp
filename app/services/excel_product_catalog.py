"""Internal Excel-authoritative product catalog and stock adjustment batches.

This module writes only to the local Vechasu catalog database. It never calls
Bitrix or MoySklad clients and therefore cannot change either external system.
"""

import json
import math
import re
import uuid
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.brand_values import is_numeric_brand, normalize_brand
from app.services.product_reconciliation import (
    AUTOMATIC_STATUSES,
    article_quality,
    batch_id_for,
    normalize_text,
    text,
)
from app.services.shared_catalog import (
    DuplicateCatalogValueError,
    assign_product_taxonomy,
    catalog_prefix_pattern,
    ensure_brand,
    ensure_category,
    register_catalog_search,
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

VISIBLE_PRODUCT_SQL = (
    "(p.source_key LIKE 'bitrix:%' "
    "OR (b.status = 'active' AND p.current_batch_id = b.id))"
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

UNSET = object()


def optional_price_text(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        price = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Цена должна быть числом.")
    if not math.isfinite(price) or price < 0:
        raise ValueError("Цена должна быть неотрицательным числом.")
    return "{:g}".format(price)


def ensure_unique_article(connection, article, exclude_product_id=None):
    """Reject an exact article duplicate without changing legacy rows."""
    article = text(article)
    if not article:
        return
    parameters = [article]
    exclusion = ""
    if exclude_product_id is not None:
        exclusion = "AND id <> ? "
        parameters.append(int(exclude_product_id))
    duplicate = connection.execute(
        "SELECT id, excel_name_raw FROM catalog_excel_products "
        "WHERE active = 1 AND trim(COALESCE(excel_article, '')) = ? "
        + exclusion
        + "ORDER BY id LIMIT 1",
        parameters,
    ).fetchone()
    if duplicate is None:
        return
    raise DuplicateCatalogValueError(
        "Товар с артикулом «{}» уже существует: {} (ID {}).".format(
            article, duplicate["excel_name_raw"], duplicate["id"]
        ),
        {
            "id": str(duplicate["id"]),
            "product_id": str(duplicate["id"]),
            "name": duplicate["excel_name_raw"],
            "article": article,
        },
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


def category_for_product_name(name, category):
    if re.search(r"(?<!\w)ремень(?!\w)", text(name), re.IGNORECASE):
        return "Ремень"
    return category


def _load_json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed


def parse_initial_stock(value):
    raw_value = str(value if value is not None else "").strip()
    if not re.fullmatch(r"\d+", raw_value):
        raise ValueError(
            "Начальный остаток должен быть целым числом от 0 и выше."
        )
    return int(raw_value)


def _record_manual_stock_adjustment(
        connection, product_id, stock_before, stock_after, reason):
    if stock_after == stock_before:
        return
    connection.execute(
        "INSERT INTO catalog_excel_manual_stock_operations ("
        "id, product_id, stock_before, stock_after, stock_difference, "
        "reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()), int(product_id), stock_before, stock_after,
            stock_after - stock_before, text(reason) or None, utc_now(),
        ),
    )


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
        "bitrix_brand": normalize_brand(product["brand"]) or None,
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
    if "excel_brand" in columns or "excel_category" in columns:
        assign_product_taxonomy(
            connection,
            product_id,
            brand=state.get("excel_brand"),
            category=state.get("excel_category"),
            brand_id=state.get("brand_id"),
            category_id=state.get("category_id"),
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
        numeric_brand_rows = []
        missing_brand_rows = 0
        for result in results:
            raw_brand = text(result.get("excel_brand"))
            if is_numeric_brand(raw_brand):
                result["_source_excel_brand"] = raw_brand
                result["excel_brand"] = ""
                result["brand_validation_error"] = "numeric_brand_rejected"
                numeric_brand_rows.append({
                    "excel_row": result.get("excel_row"),
                    "value": raw_brand,
                })
            if not normalize_brand(result.get("excel_brand")):
                missing_brand_rows += 1
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
                        "brand_validation": {
                            "numeric_rejected": numeric_brand_rows,
                            "missing_count": missing_brand_rows,
                        },
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
                if (
                    product["source_key"] in incoming_keys
                    or str(product["source_key"]).startswith("bitrix:")
                ):
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
                deleted_product = connection.execute(
                    "SELECT id FROM catalog_excel_products "
                    "WHERE deleted_at IS NOT NULL AND deleted_source_key = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (source_key,),
                ).fetchone()
                if deleted_product is not None:
                    continue
                product = connection.execute(
                    "SELECT * FROM catalog_excel_products WHERE source_key = ?",
                    (source_key,),
                ).fetchone()
                if (
                    product is None
                    and result.get("match_status") in AUTOMATIC_STATUSES
                    and result.get("product_id") is not None
                ):
                    linked_cards = connection.execute(
                        "SELECT * FROM catalog_excel_products "
                        "WHERE active = 1 AND source_key LIKE 'bitrix:%' "
                        "AND bitrix_catalog_product_id = ? ORDER BY id",
                        (int(result["product_id"]),),
                    ).fetchall()
                    if len(linked_cards) == 1:
                        product = linked_cards[0]
                        connection.execute(
                            "UPDATE catalog_excel_products SET source_key = ? WHERE id = ?",
                            (source_key, product["id"]),
                        )
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
                assign_product_taxonomy(
                    connection,
                    product_id,
                    brand=state.get("excel_brand"),
                    category=state.get("excel_category"),
                )
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
            "excel_brand": result.get(
                "_source_excel_brand",
                result.get("excel_brand"),
            ),
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
            "excel_brand": (
                normalize_brand(result.get("excel_brand"))
                or normalize_brand(enrichment.get("bitrix_brand"))
            ),
            "excel_category": text(category_for_product_name(
                result.get("excel_name"), result.get("category")
            )) or None,
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
        self.database = database or CatalogDatabase(cache_initialization=True)

    def list_products(self, query="", brand="", category="", cell="",
                      match_status="all", hide_zero=False, sort_by="name",
                      sort_dir="asc", page=1, per_page=50,
                      created_from="", created_to="", brand_id=None,
                      category_id=None, product_id=None,
                      include_cell_item_names=True, include_facets=True):
        self.database.initialize()
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 100000))
        allowed_sort_fields = {
            "name": "p.excel_name_raw COLLATE NOCASE",
            "article": "COALESCE(p.excel_article, '')",
            "brand": "COALESCE(p.excel_brand, '')",
            "category": "COALESCE(p.excel_category, '')",
            "stock": "p.stock",
            "cell": "COALESCE(p.cell, '')",
            "created_at": "p.created_at",
            "price": "CAST(NULLIF(p.bitrix_price_amount, '') AS REAL)",
            "match_status": "p.match_status",
        }
        sort_by = sort_by if sort_by in allowed_sort_fields else "name"
        sort_dir = sort_dir if sort_dir in {"asc", "desc"} else "asc"
        visible_cards_sql = VISIBLE_PRODUCT_SQL
        where = ["p.active = 1", visible_cards_sql]
        parameters = []
        if query:
            prefix_pattern = catalog_prefix_pattern(query)
            where.append(
                "(catalog_search_key(p.excel_name_raw) LIKE ? ESCAPE '\\' "
                "OR catalog_search_key(p.excel_article) LIKE ? ESCAPE '\\' "
                "OR catalog_search_key(cp.barcode) LIKE ? ESCAPE '\\')"
            )
            parameters.extend([prefix_pattern, prefix_pattern, prefix_pattern])
        if category:
            where.append(
                "(COALESCE(p.excel_category, '') = ? OR "
                "substr(COALESCE(p.excel_category, ''), "
                "1, length(?) + 1) = ? || '/')"
            )
            parameters.extend([category, category, category])
        if category_id not in (None, ""):
            selected_category_id = int(category_id)
            if selected_category_id == 0:
                where.append("p.category_id IS NULL")
            else:
                where.append(
                    "p.category_id IN ("
                    "SELECT matching.id FROM erp_categories matching "
                    "WHERE matching.active = 1 "
                    "AND matching.normalized_name = ("
                    "SELECT selected.normalized_name "
                    "FROM erp_categories selected WHERE selected.id = ?))"
                )
                parameters.append(selected_category_id)
        if product_id not in (None, ""):
            where.append("p.id = ?")
            parameters.append(int(product_id))
        if cell:
            if cell == "Без ячейки":
                where.append("trim(COALESCE(p.cell, '')) = ''")
            else:
                where.append("trim(COALESCE(p.cell, '')) = ?")
                parameters.append(cell)
        if hide_zero:
            where.append("p.stock > 0 AND CAST(p.stock AS REAL) > 0")
        if created_from:
            where.append("substr(p.created_at, 1, 10) >= ?")
            parameters.append(created_from)
        if created_to:
            where.append("substr(p.created_at, 1, 10) <= ?")
            parameters.append(created_to)
        if match_status == "requires_mapping":
            where.append("p.match_status = 'ambiguous'")
        elif match_status != "all":
            where.append("p.match_status = ?")
            parameters.append(match_status)
        brand_facet_where = list(where)
        brand_facet_parameters = list(parameters)
        if brand:
            where.append("COALESCE(p.excel_brand, '') = ?")
            parameters.append(brand)
        if brand_id not in (None, ""):
            selected_brand_id = int(brand_id)
            if selected_brand_id == 0:
                where.append("p.brand_id IS NULL")
            else:
                where.append("p.brand_id = ?")
                parameters.append(selected_brand_id)
        where_sql = " WHERE " + " AND ".join(where)
        brand_facet_where_sql = (
            " WHERE " + " AND ".join(brand_facet_where)
        )
        select_sql = (
            "SELECT p.*, cp.barcode AS bitrix_barcode, "
            "b.source_filename, b.applied_at, b.row_count AS batch_row_count "
            "FROM catalog_excel_products p JOIN catalog_excel_batches b "
            "ON b.id = p.current_batch_id "
            "LEFT JOIN catalog_products cp "
            "ON cp.id = p.bitrix_catalog_product_id"
        )
        with self.database.connect() as connection:
            if query:
                register_catalog_search(connection)
            active_batch = connection.execute(
                "SELECT * FROM catalog_excel_batches WHERE status = 'active' "
                "ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
            total = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products p "
                "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                "LEFT JOIN catalog_products cp "
                "ON cp.id = p.bitrix_catalog_product_id" + where_sql,
                parameters,
            ).fetchone()[0]
            pages = (total + per_page - 1) // per_page
            if pages and page > pages:
                page = pages
            stats = dict(connection.execute(
                "SELECT COUNT(*) AS positions, COALESCE(SUM(p.stock), 0) AS total_stock, "
                "SUM(CASE WHEN p.stock > 0 THEN 1 ELSE 0 END) AS positive_positions, "
                "SUM(CASE WHEN p.stock <= 0 THEN 1 ELSE 0 END) AS zero_positions, "
                "SUM(CASE WHEN p.bitrix_catalog_product_id IS NOT NULL THEN 1 ELSE 0 END) "
                "AS matched_positions FROM catalog_excel_products p "
                "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                "LEFT JOIN catalog_products cp "
                "ON cp.id = p.bitrix_catalog_product_id" + where_sql,
                parameters,
            ).fetchone())
            missing_price_sql = (
                "CASE WHEN NULLIF(p.bitrix_price_amount, '') IS NULL THEN 1 ELSE 0 END, "
                if sort_by == "price" else ""
            )
            if sort_by == "created_at":
                order_sql = (
                    " ORDER BY CASE WHEN datetime(p.created_at) IS NULL "
                    "THEN 1 ELSE 0 END ASC, datetime(p.created_at) {}"
                ).format(sort_dir.upper())
                stable_order_sql = ", p.id {}".format(sort_dir.upper())
            else:
                order_sql = " ORDER BY {}{} {}".format(
                    missing_price_sql,
                    allowed_sort_fields[sort_by],
                    sort_dir.upper(),
                )
                stable_order_sql = ", p.excel_row ASC, p.id ASC"
            rows = connection.execute(
                select_sql + where_sql + order_sql + stable_order_sql
                + " LIMIT ? OFFSET ?",
                parameters + [per_page, (page - 1) * per_page],
            ).fetchall()
            brands = []
            categories = []
            brand_groups = []
            category_groups = []
            brand_all_count = 0
            cell_groups = []
            status_counts = {}
            if include_facets:
                brand_groups = [dict(row) for row in connection.execute(
                    "SELECT trim(p.excel_brand) AS name, COUNT(*) AS count "
                    "FROM catalog_excel_products p JOIN catalog_excel_batches b "
                    "ON b.id = p.current_batch_id "
                    "LEFT JOIN catalog_products cp "
                    "ON cp.id = p.bitrix_catalog_product_id"
                    + brand_facet_where_sql + " "
                    "AND trim(COALESCE(p.excel_brand, '')) <> '' "
                    "AND trim(p.excel_brand) GLOB '*[^0-9]*' "
                    "GROUP BY trim(p.excel_brand) COLLATE NOCASE "
                    "HAVING COUNT(*) > 0 ORDER BY name COLLATE NOCASE",
                    brand_facet_parameters,
                ).fetchall()]
                brand_all_count = connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_products p "
                    "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                    "LEFT JOIN catalog_products cp "
                    "ON cp.id = p.bitrix_catalog_product_id"
                    + brand_facet_where_sql,
                    brand_facet_parameters,
                ).fetchone()[0]
                brands = [group["name"] for group in brand_groups]
                categories = [row[0] for row in connection.execute(
                    "SELECT DISTINCT COALESCE(p.excel_category, '') AS value "
                    "FROM catalog_excel_products p JOIN catalog_excel_batches b "
                    "ON b.id = p.current_batch_id WHERE p.active = 1 AND "
                    + visible_cards_sql + " "
                    "AND trim(COALESCE(p.excel_category, '')) <> '' "
                    "ORDER BY value"
                ).fetchall()]
                category_groups = [dict(row) for row in connection.execute(
                    "SELECT COALESCE(p.excel_category, '') AS name, "
                    "COUNT(*) AS count FROM catalog_excel_products p "
                    "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                    "WHERE p.active = 1 AND " + visible_cards_sql + " "
                    "AND trim(COALESCE(p.excel_category, '')) <> '' "
                    "GROUP BY name ORDER BY name"
                ).fetchall()]
                cell_item_names_sql = (
                    ", GROUP_CONCAT(p.excel_name_raw, char(31)) AS item_names "
                    if include_cell_item_names
                    else ""
                )
                cell_groups = [dict(row) for row in connection.execute(
                    "SELECT CASE WHEN trim(COALESCE(p.cell, '')) = '' THEN "
                    "'Без ячейки' ELSE trim(p.cell) END AS cell, "
                    "COUNT(*) AS count, COALESCE(SUM(p.stock), 0) AS stock "
                    + cell_item_names_sql
                    + "FROM catalog_excel_products p "
                    "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                    "WHERE p.active = 1 AND " + visible_cards_sql + " "
                    "GROUP BY CASE WHEN trim(COALESCE(p.cell, '')) = '' "
                    "THEN 'Без ячейки' ELSE trim(p.cell) END "
                    "ORDER BY CASE WHEN trim(COALESCE(p.cell, '')) = '' "
                    "THEN 1 ELSE 0 END, cell"
                ).fetchall()]
                status_counts = {
                    row["match_status"]: row["count"]
                    for row in connection.execute(
                        "SELECT p.match_status, COUNT(*) AS count "
                        "FROM catalog_excel_products p "
                        "JOIN catalog_excel_batches b "
                        "ON b.id = p.current_batch_id WHERE p.active = 1 AND "
                        + visible_cards_sql + " GROUP BY p.match_status"
                    ).fetchall()
                }
        items = [self._prepare_product(dict(row)) for row in rows]
        return {
            "items": items, "total": total, "page": page, "per_page": per_page,
            "pages": pages,
            "brands": brands, "categories": categories,
            "brand_groups": brand_groups, "category_groups": category_groups,
            "brand_all_count": brand_all_count,
            "cell_groups": cell_groups,
            "stats": stats, "sort_by": sort_by, "sort_dir": sort_dir,
            "status_counts": status_counts,
            "active_batch": dict(active_batch) if active_batch else None,
        }

    def get_product(self, product_id):
        self.database.initialize()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT p.*, cp.barcode AS bitrix_barcode, "
                "b.source_filename, b.applied_at FROM catalog_excel_products p "
                "JOIN catalog_excel_batches b ON b.id = p.current_batch_id "
                "LEFT JOIN catalog_products cp "
                "ON cp.id = p.bitrix_catalog_product_id "
                "WHERE p.id = ? AND p.active = 1 AND "
                + VISIBLE_PRODUCT_SQL,
                (int(product_id),),
            ).fetchone()
        return self._prepare_product(dict(row)) if row else None

    def list_repair_catalog_source_items(self, limit=100000):
        """Return only fields needed by the repair product selector."""
        self.database.initialize()
        limit = max(1, min(int(limit), 100000))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT p.id, p.excel_name_raw AS name, "
                "COALESCE(p.excel_brand, '') AS brand, "
                "COALESCE(p.excel_article, '') AS article, "
                "COALESCE(cp.barcode, '') AS barcode, '' AS model, "
                "p.bitrix_external_product_id, p.bitrix_thumbnail_url, "
                "p.bitrix_primary_image_url, p.bitrix_gallery_json, "
                "p.moysklad_product_id "
                "FROM catalog_excel_products p JOIN catalog_excel_batches b "
                "ON b.id = p.current_batch_id LEFT JOIN catalog_products cp "
                "ON cp.id = p.bitrix_catalog_product_id "
                "WHERE p.active = 1 AND " + VISIBLE_PRODUCT_SQL +
                " ORDER BY p.excel_brand COLLATE NOCASE, "
                "p.excel_name_raw COLLATE NOCASE, "
                "p.excel_article COLLATE NOCASE, p.id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_bitrix_images(self, product_id, external_product_id,
                             primary_url, thumbnail_url, gallery):
        """Persist a verified Bitrix gallery without touching catalog or stock."""
        external_product_id = text(external_product_id)
        if not external_product_id:
            raise ValueError("Bitrix product ID is required")
        self.database.initialize()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT bitrix_external_product_id "
                "FROM catalog_excel_products WHERE id = ? AND active = 1",
                (int(product_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Товар не найден.")
            if text(row["bitrix_external_product_id"]) != external_product_id:
                raise ValueError("Связь товара с Bitrix изменилась.")
            connection.execute(
                "UPDATE catalog_excel_products SET "
                "bitrix_primary_image_url = ?, bitrix_thumbnail_url = ?, "
                "bitrix_gallery_json = ?, updated_at = ? WHERE id = ?",
                (
                    text(primary_url) or None,
                    text(thumbnail_url) or None,
                    json.dumps(gallery or [], ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    int(product_id),
                ),
            )
        return self.get_product(product_id)

    def create_product(
            self, name, article="", brand="", category="", cell="", stock=0,
            brand_id=None, category_id=None, price=None, enforce_unique=False,
            moysklad_product_id=None, actor_id="", actor_name="",
            actor_type="system"):
        name = text(name)
        if not name:
            raise ValueError("Название товара обязательно.")
        article = text(article)
        if is_numeric_brand(brand):
            raise ValueError("Бренд не может состоять только из цифр.")
        brand = normalize_brand(brand)
        category = text(category)
        cell = text(cell)
        stock = parse_initial_stock(stock)
        moysklad_product_id = text(moysklad_product_id) or None
        self.database.initialize()
        with self.database.transaction() as connection:
            ensure_unique_article(connection, article)
            brand_row = ensure_brand(
                connection,
                name=brand,
                brand_id=brand_id,
                create=True,
            )
            category_row = ensure_category(
                connection,
                brand_row["id"] if brand_row else None,
                name=category,
                category_id=category_id,
                create=True,
            )
            brand = brand_row["name"] if brand_row else ""
            category = category_row["name"] if category_row else ""
            duplicate = connection.execute(
                "SELECT id, excel_name_raw FROM catalog_excel_products "
                "WHERE active = 1 AND normalized_name = ? "
                "AND COALESCE(brand_id, 0) = COALESCE(?, 0) "
                "AND COALESCE(category_id, 0) = COALESCE(?, 0) "
                "ORDER BY id LIMIT 1",
                (
                    normalize_text(name),
                    brand_row["id"] if brand_row else None,
                    category_row["id"] if category_row else None,
                ),
            ).fetchone()
            if enforce_unique and duplicate is not None:
                raise DuplicateCatalogValueError(
                    "Такой товар уже существует: {} (ID {}).".format(
                        duplicate["excel_name_raw"], duplicate["id"]
                    ),
                    {
                        "id": str(duplicate["id"]),
                        "product_id": str(duplicate["id"]),
                        "name": duplicate["excel_name_raw"],
                        "brand_id": (
                            brand_row["id"] if brand_row else None
                        ),
                        "category_id": (
                            category_row["id"] if category_row else None
                        ),
                    },
                )
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
            enrichment["bitrix_price_amount"] = optional_price_text(price)
            enrichment["bitrix_price_currency"] = (
                "RUB" if enrichment["bitrix_price_amount"] is not None else None
            )
            columns = (
                "source_key", "created_batch_id", "current_batch_id", "active",
                "raw_excel_json", "excel_row", "excel_name_raw", "normalized_name",
                "excel_article", "article_quality", "excel_brand", "excel_category",
                "brand_id", "category_id",
                "stock", "cell", "stock_source", "file_sha256", "match_status",
                "match_method", "match_confidence", "match_decision", "candidates_json",
                "bitrix_link_cardinality", "shared_bitrix_row_count",
            ) + tuple(enrichment) + (
                "moysklad_product_id", "moysklad_sync_status",
                "created_at", "updated_at",
            )
            values = (
                source_key, batch["id"], batch["id"], 1,
                _json({"source": "manual", "name": name, "article": article,
                       "brand": brand, "category": category, "cell": cell,
                       "stock": stock, "price": enrichment["bitrix_price_amount"]}),
                excel_row, name, normalize_text(name), article or None,
                article_quality(article), brand, category or None,
                brand_row["id"] if brand_row else None,
                category_row["id"] if category_row else None,
                stock, cell or None,
                "manual", batch["file_sha256"], "not_found", "manual_create", 0.0,
                "unmatched", "[]", "unlinked", 0,
            ) + tuple(enrichment.values()) + (
                moysklad_product_id,
                "linked" if moysklad_product_id else "not_linked",
                now,
                now,
            )
            connection.execute(
                "INSERT INTO catalog_excel_products ({}) VALUES ({})".format(
                    ", ".join(columns), ", ".join("?" for _ in columns)
                ),
                values,
            )
            product_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            _record_manual_stock_adjustment(
                connection,
                product_id,
                0,
                stock,
                "Начальный остаток при создании товара",
            )
            AuditJournal(self.database).record(
                "product", product_id,
                "created" if actor_name else "system_created",
                name, article,
                after={
                    "name": name, "article": article, "brand": brand,
                    "category": category, "price": price, "cell": cell,
                    "stock": stock,
                },
                metadata={"article": article}, actor_id=actor_id,
                actor_name=actor_name, actor_type=actor_type,
                connection=connection,
            )
        return self.get_product(product_id)

    def update_product(self, product_id, name=None, article=None, brand=None,
                       category=None, cell=None, stock=None, stock_reason="",
                       brand_id=None, category_id=None, price=UNSET,
                       actor_id="", actor_name="", actor_type="system"):
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
                normalized_article = text(article) or None
                if normalized_article != (text(product["excel_article"]) or None):
                    ensure_unique_article(
                        connection,
                        normalized_article,
                        exclude_product_id=product_id,
                    )
                values["excel_article"] = normalized_article
                values["article_quality"] = article_quality(values["excel_article"])
            if brand is not None:
                if is_numeric_brand(brand):
                    raise ValueError("Бренд не может состоять только из цифр.")
                values["excel_brand"] = normalize_brand(brand)
            if category is not None:
                values["excel_category"] = text(category) or None
            brand_changed = brand is not None or brand_id not in (None, "")
            category_changed = category is not None or category_id not in (None, "")
            if brand_changed:
                brand_row = ensure_brand(
                    connection,
                    name=values["excel_brand"],
                    brand_id=brand_id,
                    create=True,
                )
                values["brand_id"] = brand_row["id"] if brand_row else None
                values["excel_brand"] = brand_row["name"] if brand_row else ""
                if not category_changed:
                    values["category_id"] = None
                    values["excel_category"] = None
            else:
                brand_row = ensure_brand(
                    connection,
                    name=values["excel_brand"],
                    brand_id=values.get("brand_id"),
                    create=True,
                )
            if category_changed:
                category_row = ensure_category(
                    connection,
                    brand_row["id"] if brand_row else None,
                    name=values["excel_category"],
                    category_id=category_id,
                    create=True,
                )
                values["category_id"] = (
                    category_row["id"] if category_row else None
                )
                values["excel_category"] = (
                    category_row["name"] if category_row else None
                )
            duplicate = connection.execute(
                "SELECT id, excel_name_raw FROM catalog_excel_products "
                "WHERE active = 1 AND normalized_name = ? AND id <> ? "
                "AND COALESCE(brand_id, 0) = COALESCE(?, 0) "
                "AND COALESCE(category_id, 0) = COALESCE(?, 0) "
                "ORDER BY id LIMIT 1",
                (
                    values["normalized_name"],
                    int(product_id),
                    values.get("brand_id"),
                    values.get("category_id"),
                ),
            ).fetchone()
            if duplicate is not None:
                raise DuplicateCatalogValueError(
                    "Такой товар уже существует: {} (ID {}).".format(
                        duplicate["excel_name_raw"], duplicate["id"]
                    ),
                    {
                        "id": str(duplicate["id"]),
                        "product_id": str(duplicate["id"]),
                        "name": duplicate["excel_name_raw"],
                        "brand_id": values.get("brand_id"),
                        "category_id": values.get("category_id"),
                    },
                )
            if cell is not None:
                values["cell"] = text(cell) or None
            if price is not UNSET:
                values["bitrix_price_amount"] = optional_price_text(price)
                if values["bitrix_price_amount"] is not None:
                    values["bitrix_price_currency"] = (
                        values.get("bitrix_price_currency") or "RUB"
                    )
            stock_before = float(values["stock"] or 0)
            if stock is not None:
                try:
                    stock_after = float(str(stock).replace(",", "."))
                except (TypeError, ValueError):
                    raise ValueError("Остаток должен быть числом.")
                if not math.isfinite(stock_after) or stock_after < 0:
                    raise ValueError("Остаток должен быть неотрицательным числом.")
                values["stock"] = stock_after
                values["stock_source"] = "manual"
            raw_excel = _load_json(values.get("raw_excel_json"), {})
            raw_excel.update({
                "excel_name": values["excel_name_raw"],
                "excel_article": values["excel_article"],
                "excel_brand": values["excel_brand"],
                "category": values["excel_category"],
                "cell": values["cell"],
                "price": values.get("bitrix_price_amount"),
            })
            values["raw_excel_json"] = _json(raw_excel)
            values["updated_at"] = utc_now()
            _restore_columns(connection, product_id, values, PRODUCT_MUTABLE_COLUMNS)
            assign_product_taxonomy(
                connection,
                product_id,
                brand=values["excel_brand"],
                category=values["excel_category"],
                brand_id=values.get("brand_id"),
                category_id=values.get("category_id"),
            )
            if stock is not None:
                _record_manual_stock_adjustment(
                    connection,
                    product_id,
                    stock_before,
                    stock_after,
                    stock_reason,
                )
            before = {
                "name": product["excel_name_raw"],
                "article": product["excel_article"],
                "brand": product["excel_brand"],
                "category": product["excel_category"],
                "price": product["bitrix_price_amount"],
                "cell": product["cell"],
                "stock": float(product["stock"] or 0),
            }
            after = {
                "name": values["excel_name_raw"],
                "article": values["excel_article"],
                "brand": values["excel_brand"],
                "category": values["excel_category"],
                "price": values.get("bitrix_price_amount"),
                "cell": values["cell"],
                "stock": float(values["stock"] or 0),
            }
            if before != after:
                AuditJournal(self.database).record(
                    "product", product_id, "updated", after["name"],
                    after["article"] or "", before=before, after=after,
                    metadata={"article": after["article"] or ""},
                    actor_id=actor_id, actor_name=actor_name,
                    actor_type=actor_type, connection=connection,
                )
        return self.get_product(product_id)

    def list_manual_stock_operations(self, limit=5000):
        self.database.initialize()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM catalog_excel_manual_stock_operations "
                "ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),),
            ).fetchall()
        operations = []
        for row in rows:
            item = dict(row)
            difference = float(item["stock_difference"])
            item.update({
                "type": "manual",
                "label": "Корректировка остатка",
                "quantity": abs(difference),
                "diff": difference,
                "source": "Vechasu ERP",
            })
            operations.append(item)
        return operations

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

    def delete_product(
            self, product_id, external_references=None, force=False,
            actor_id="", actor_name="", actor_type="user"):
        """Permanently remove a card from the active catalog via tombstone.

        Related sales, receipts and movements keep their foreign keys and
        snapshots.  A deleted source key is released so a later product is a
        genuinely new card instead of resurrecting this row.
        """
        del external_references
        self.database.initialize()
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ? AND active = 1",
                (int(product_id),),
            ).fetchone()
            if product is None:
                raise ValueError("Товар не найден.")
            self._validate_products_for_delete([product], force)
            result = self._delete_product_in_transaction(
                connection, product, force=force, actor_id=actor_id,
                actor_name=actor_name, actor_type=actor_type,
            )
        return result

    @staticmethod
    def _validate_products_for_delete(products, force):
        blocked = [row for row in products if float(row["stock"] or 0) != 0]
        if blocked and not force:
            if len(products) == 1:
                raise ProductDeleteBlockedError(
                    "Товар с ненулевым остатком нельзя удалить."
                )
            raise ProductDeleteBlockedError(
                "Нельзя удалить: у {} товар(ов) ненулевой остаток."
                .format(len(blocked))
            )

    def _delete_product_in_transaction(
            self, connection, product, force=False, actor_id="",
            actor_name="", actor_type="user", record_audit=True):
        product_id = int(product["id"])
        stock = float(product["stock"] or 0)
        deleted_at = utc_now()
        source_key = str(product["source_key"])
        connection.execute(
            "UPDATE catalog_excel_products SET active = 0, source_key = ?, "
            "deleted_source_key = ?, deleted_at = ?, deleted_by = ?, "
            "deleted_stock = ?, delete_mode = ?, updated_at = ? "
            "WHERE id = ? AND active = 1",
            (
                "deleted:{}:{}".format(product_id, uuid.uuid4().hex),
                source_key, deleted_at, str(actor_id or "") or None, stock,
                "force" if force else "normal", deleted_at, product_id,
            ),
        )
        if record_audit:
            AuditJournal(self.database).record(
                "product", product_id, "deleted", product["excel_name_raw"],
                product["excel_article"] or "", metadata={
                    "article": product["excel_article"] or "",
                    "force": bool(force), "stock": stock,
                }, actor_id=actor_id, actor_name=actor_name,
                actor_type=actor_type, connection=connection,
            )
        return {"id": product_id, "stock": stock, "force": bool(force),
                "deleted_at": deleted_at}

    def delete_brand_catalog(
            self, brand_id, category_id=None, force=False, actor_id="",
            actor_name="", actor_type="user"):
        """Atomically tombstone a brand/category product set and its relation."""
        self.database.initialize()
        with self.database.transaction() as connection:
            brand = connection.execute(
                "SELECT * FROM erp_brands WHERE id = ? AND active = 1",
                (int(brand_id),),
            ).fetchone()
            if brand is None:
                raise ValueError("Бренд не найден.")
            category = None
            parameters = [int(brand_id)]
            product_where = "p.brand_id = ? AND p.active = 1"
            if category_id not in (None, ""):
                category = connection.execute(
                    "SELECT c.* FROM erp_categories c "
                    "JOIN erp_brand_categories bc ON bc.category_id = c.id "
                    "WHERE bc.brand_id = ? AND c.id = ? AND c.active = 1",
                    (int(brand_id), int(category_id)),
                ).fetchone()
                if category is None:
                    raise ValueError("Категория бренда не найдена.")
                product_where += " AND p.category_id = ?"
                parameters.append(int(category_id))
            products = connection.execute(
                "SELECT p.* FROM catalog_excel_products p WHERE " +
                product_where + " ORDER BY p.id",
                parameters,
            ).fetchall()
            self._validate_products_for_delete(products, force)
            for product in products:
                self._delete_product_in_transaction(
                    connection, product, force=force, actor_id=actor_id,
                    actor_name=actor_name, actor_type=actor_type,
                    record_audit=False,
                )
            if category is not None:
                connection.execute(
                    "DELETE FROM erp_brand_categories "
                    "WHERE brand_id = ? AND category_id = ?",
                    (int(brand_id), int(category_id)),
                )
                AuditJournal(self.database).record(
                    "category", category["id"], "deleted", category["name"],
                    metadata={"brand_id": int(brand_id),
                              "brand_name_snapshot": brand["name"],
                              "products_deleted": len(products),
                              "nonzero_products": sum(
                                  float(item["stock"] or 0) != 0
                                  for item in products
                              ),
                              "stock_total": sum(
                                  float(item["stock"] or 0) for item in products
                              ),
                              "force": bool(force)}, connection=connection,
                    actor_id=actor_id, actor_name=actor_name,
                    actor_type=actor_type,
                )
            else:
                connection.execute(
                    "DELETE FROM erp_brand_categories WHERE brand_id = ?",
                    (int(brand_id),),
                )
                connection.execute(
                    "UPDATE erp_brands SET active = 0, updated_at = ? WHERE id = ?",
                    (utc_now(), int(brand_id)),
                )
                AuditJournal(self.database).record(
                    "brand", brand["id"], "deleted", brand["name"],
                    metadata={"products_deleted": len(products),
                              "nonzero_products": sum(
                                  float(item["stock"] or 0) != 0
                                  for item in products
                              ),
                              "stock_total": sum(
                                  float(item["stock"] or 0) for item in products
                              ),
                              "force": bool(force)}, connection=connection,
                    actor_id=actor_id, actor_name=actor_name,
                    actor_type=actor_type,
                )
        return {"brand_id": int(brand_id),
                "category_id": int(category_id) if category is not None else None,
                "products_deleted": len(products), "force": bool(force)}

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
