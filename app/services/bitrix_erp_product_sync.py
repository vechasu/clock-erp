"""Safely merge normalized Bitrix catalog cards into ERP product cards."""

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import load_bitrix_enrichment
from app.services.product_reconciliation import article_quality, normalize_text, reliable_article


CATALOG_BATCH_ID = "bitrix-catalog-products"
CATALOG_BATCH_SHA256 = hashlib.sha256(CATALOG_BATCH_ID.encode("utf-8")).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value):
    return str(value or "").strip()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _primary_category(product):
    categories = product.get("categories") or []
    category = categories[0] if categories else product.get("category") or {}
    return _text(category.get("name"))


def _sale_price(product):
    price = product.get("sale_price") or {}
    if price.get("value") is None:
        return None, None
    amount = price.get("value_text")
    if amount in (None, ""):
        amount = str(price["value"])
    return str(amount), _text(price.get("currency"))


def _primary_images(product):
    images = [image for image in product.get("images") or [] if image.get("original_url")]
    primary = next((image for image in images if image.get("is_primary")), None)
    if primary is None and images:
        primary = images[0]
    previews = [image for image in images if image.get("kind") == "preview"]
    thumbnail_pool = previews or images
    thumbnail = min(
        thumbnail_pool,
        key=lambda image: (
            int(image.get("file_size") or 10 ** 15),
            int(image.get("width") or 10 ** 9) * int(image.get("height") or 10 ** 9),
            int(image.get("order") or 500),
        ),
    ) if thumbnail_pool else None
    return primary, thumbnail, images


def enrichment_from_product(product, catalog_product_id=None):
    primary, thumbnail, images = _primary_images(product)
    amount, currency = _sale_price(product)
    properties = []
    for prop in product.get("properties") or []:
        value = prop.get("display_value")
        if value in (None, "", []):
            value = prop.get("value")
        if value in (None, "", []):
            continue
        properties.append({
            "name": _text(prop.get("name")),
            "code": _text(prop.get("code")),
            "value": value,
        })
    description = "\n\n".join(
        part for part in (
            _text(product.get("preview_text")),
            _text(product.get("detail_text")),
        )
        if part
    )
    return {
        "bitrix_catalog_product_id": catalog_product_id,
        "bitrix_external_product_id": _text(product.get("external_product_id")),
        "bitrix_xml_id": _text(product.get("external_xml_id")),
        "bitrix_name": _text(product.get("name")),
        "bitrix_brand": _text(product.get("brand")) or None,
        "bitrix_category": _primary_category(product) or None,
        "bitrix_source_url": _text(product.get("url")) or None,
        "bitrix_primary_image_url": primary.get("original_url") if primary else None,
        "bitrix_thumbnail_url": thumbnail.get("original_url") if thumbnail else None,
        "bitrix_gallery_json": _json(images),
        "bitrix_price_amount": amount,
        "bitrix_price_currency": currency or None,
        "bitrix_description": description or None,
        "bitrix_properties_json": _json(properties),
        "bitrix_active": int(bool(product.get("active", True))),
    }


def create_database_backup(database, backup_root=None):
    """Create and verify a consistent SQLite backup before an apply run."""
    if not database.exists():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    root = Path(backup_root) if backup_root else database.path.parent / "backups"
    destination_directory = root / (timestamp + "-before-bitrix-products")
    destination_directory.mkdir(parents=True, exist_ok=False)
    destination = destination_directory / database.path.name
    source_connection = database.connect()
    try:
        backup_method = getattr(source_connection, "backup", None)
        if backup_method is not None:
            backup_connection = sqlite3.connect(str(destination))
            try:
                backup_method(backup_connection)
            finally:
                backup_connection.close()
        else:
            source_connection.close()
            source_connection = None
            quoted_destination = str(destination).replace('"', '""')
            subprocess.run(
                [
                    "sqlite3",
                    str(database.path),
                    '.backup "{}"'.format(quoted_destination),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
    finally:
        if source_connection is not None:
            source_connection.close()
    with sqlite3.connect(str(destination)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("Catalog backup verification failed")
    return destination


class BitrixERPProductSync:
    """Match Bitrix products to ERP cards without changing inventory."""

    def __init__(self, database=None):
        self.database = database or CatalogDatabase()
        self._label_cache = {}

    def preview_products(self, products):
        products = list(products)
        if not self.database.exists():
            return [self._new_preview(product) for product in products]
        with self.database.connect() as connection:
            return [self._preview_one(connection, product) for product in products]

    def apply_products(self, products):
        self.database.initialize()
        results = []
        with self.database.transaction() as connection:
            for index, product in enumerate(products):
                savepoint = "bitrix_product_{}".format(index)
                connection.execute("SAVEPOINT " + savepoint)
                try:
                    result = self._apply_one(connection, product)
                    connection.execute("RELEASE SAVEPOINT " + savepoint)
                except Exception as error:
                    connection.execute("ROLLBACK TO SAVEPOINT " + savepoint)
                    connection.execute("RELEASE SAVEPOINT " + savepoint)
                    result = {
                        "status": "error",
                        "external_product_id": _text(product.get("external_product_id")),
                        "name": _text(product.get("name")),
                        "error": type(error).__name__,
                    }
                results.append(result)
        return results

    @staticmethod
    def _new_preview(product):
        return {
            "status": "created",
            "match_method": "not_found",
            "external_product_id": _text(product.get("external_product_id")),
            "name": _text(product.get("name")),
            "candidate_ids": [],
        }

    def _preview_one(self, connection, product):
        validation = self._validate(product)
        if validation:
            return validation
        match = self._match(connection, product)
        if match["status"] == "ambiguous":
            return self._conflict_result(product, match)
        row = match.get("product")
        if row is None:
            return self._new_preview(product)
        changes = self._card_changes(
            connection, row, product,
            enrichment_from_product(product, row["bitrix_catalog_product_id"]),
        )
        return {
            "status": "updated" if changes else "unchanged",
            "match_method": match["method"],
            "external_product_id": _text(product.get("external_product_id")),
            "name": _text(product.get("name")),
            "erp_product_id": row["id"],
            "candidate_ids": [row["id"]],
            "changes": sorted(changes),
        }

    def _apply_one(self, connection, product):
        validation = self._validate(product)
        if validation:
            return validation
        catalog_product = connection.execute(
            "SELECT id FROM catalog_products "
            "WHERE external_source = 'bitrix' AND external_product_id = ?",
            (_text(product.get("external_product_id")),),
        ).fetchone()
        if catalog_product is None:
            raise ValueError("Bitrix catalog product must be imported first")
        match = self._match(connection, product)
        if match["status"] == "ambiguous":
            return self._conflict_result(product, match)
        enrichment = load_bitrix_enrichment(connection, catalog_product["id"])
        existing = match.get("product")
        if existing is None:
            product_id = self._insert_card(connection, product, enrichment)
            return {
                "status": "created",
                "match_method": "not_found",
                "external_product_id": _text(product.get("external_product_id")),
                "name": _text(product.get("name")),
                "erp_product_id": product_id,
                "candidate_ids": [],
            }
        changes = self._card_changes(connection, existing, product, enrichment)
        if changes:
            self._update_card(connection, existing, product, enrichment, changes)
        return {
            "status": "updated" if changes else "unchanged",
            "match_method": match["method"],
            "external_product_id": _text(product.get("external_product_id")),
            "name": _text(product.get("name")),
            "erp_product_id": existing["id"],
            "candidate_ids": [existing["id"]],
            "changes": sorted(changes),
        }

    @staticmethod
    def _validate(product):
        product_id = _text(product.get("external_product_id"))
        name = _text(product.get("name"))
        if product_id and name:
            return None
        return {
            "status": "skipped",
            "match_method": "invalid_source",
            "external_product_id": product_id,
            "name": name,
            "candidate_ids": [],
            "error": "missing_product_id" if not product_id else "missing_name",
        }

    def _match(self, connection, product):
        product_id = _text(product.get("external_product_id"))
        xml_id = _text(product.get("external_xml_id")).casefold()
        sku = _text(product.get("external_sku"))
        brand = normalize_text(product.get("brand"))
        name = normalize_text(product.get("name"))

        indexes = (
            (
                "bitrix_id",
                product_id,
                connection.execute(
                    "SELECT * FROM catalog_excel_products "
                    "WHERE bitrix_external_product_id = ? ORDER BY id",
                    (product_id,),
                ).fetchall(),
            ),
            (
                "xml_id",
                xml_id,
                connection.execute(
                    "SELECT * FROM catalog_excel_products "
                    "WHERE lower(trim(COALESCE(bitrix_xml_id, ''))) = ? ORDER BY id",
                    (xml_id,),
                ).fetchall() if xml_id else [],
            ),
            (
                "sku",
                sku.casefold() if reliable_article(sku) else "",
                [
                    row for row in connection.execute(
                        "SELECT * FROM catalog_excel_products "
                        "WHERE lower(trim(COALESCE(excel_article, ''))) = ? ORDER BY id",
                        (sku.casefold(),),
                    ).fetchall()
                    if reliable_article(row["excel_article"])
                ] if reliable_article(sku) else [],
            ),
            (
                "brand_normalized_name",
                (brand, name) if brand and name else None,
                [
                    row for row in connection.execute(
                        "SELECT * FROM catalog_excel_products "
                        "WHERE normalized_name = ? ORDER BY id",
                        (name,),
                    ).fetchall()
                    if brand and name
                    and normalize_text(row["bitrix_brand"] or row["excel_brand"]) == brand
                ],
            ),
        )
        for method, value, candidates in indexes:
            if not value:
                continue
            compatible = [
                row for row in candidates
                if not _text(row["bitrix_external_product_id"])
                or _text(row["bitrix_external_product_id"]) == product_id
            ]
            if len(compatible) == 1:
                return {
                    "status": "matched",
                    "method": method,
                    "product": compatible[0],
                    "candidates": compatible,
                }
            if (
                method == "brand_normalized_name"
                and candidates
                and not compatible
            ):
                continue
            if len(candidates) > 1 or (candidates and not compatible):
                return {
                    "status": "ambiguous",
                    "method": method,
                    "product": None,
                    "candidates": candidates,
                }
        return {
            "status": "new",
            "method": "not_found",
            "product": None,
            "candidates": [],
        }

    @staticmethod
    def _conflict_result(product, match):
        return {
            "status": "ambiguous",
            "match_method": match["method"],
            "external_product_id": _text(product.get("external_product_id")),
            "name": _text(product.get("name")),
            "candidate_ids": [row["id"] for row in match["candidates"]],
        }

    def _canonical_label(self, connection, column, incoming):
        incoming = _text(incoming)
        key = normalize_text(incoming)
        if not key:
            return ""
        labels = self._label_cache.get(column)
        if labels is None:
            rows = connection.execute(
                "SELECT DISTINCT {} FROM catalog_excel_products "
                "WHERE trim(COALESCE({}, '')) <> ''".format(column, column)
            ).fetchall()
            labels = {
                normalize_text(row[0]): _text(row[0])
                for row in rows
                if normalize_text(row[0])
            }
            self._label_cache[column] = labels
        if key not in labels:
            labels[key] = incoming
        return labels[key]

    def _desired_card_values(self, connection, existing, product, enrichment):
        existing = dict(existing) if existing is not None else {}
        enrichment = dict(enrichment)
        name = _text(product.get("name")) or _text(existing.get("excel_name_raw"))
        brand = self._canonical_label(
            connection, "excel_brand",
            _text(product.get("brand")) or _text(existing.get("excel_brand")),
        )
        category = self._canonical_label(
            connection, "excel_category",
            _primary_category(product) or _text(existing.get("excel_category")),
        )
        incoming_article = _text(product.get("external_sku"))
        article = _text(existing.get("excel_article"))
        if not article and reliable_article(incoming_article):
            article = incoming_article
        preserve_empty_enrichment = (
            "bitrix_name",
            "bitrix_brand",
            "bitrix_category",
            "bitrix_source_url",
            "bitrix_primary_image_url",
            "bitrix_thumbnail_url",
            "bitrix_price_amount",
            "bitrix_price_currency",
            "bitrix_description",
        )
        for field in preserve_empty_enrichment:
            if enrichment.get(field) in (None, "") and existing.get(field) not in (None, ""):
                enrichment[field] = existing[field]
        for field in ("bitrix_gallery_json", "bitrix_properties_json"):
            if enrichment.get(field) in (None, "", "[]") and existing.get(field) not in (
                None, "", "[]",
            ):
                enrichment[field] = existing[field]
        enrichment["bitrix_brand"] = brand or None
        enrichment["bitrix_category"] = category or None
        values = {
            "active": 1,
            "excel_name_raw": name,
            "normalized_name": normalize_text(name),
            "excel_article": article or None,
            "article_quality": article_quality(article),
            "excel_brand": brand,
            "excel_category": category or None,
            "match_status": "exact",
            "match_method": "bitrix_catalog_sync",
            "match_confidence": 1.0,
            "match_decision": "automatic",
            "candidates_json": "[]",
            "bitrix_link_cardinality": "one_to_one",
            "shared_bitrix_row_count": 1,
        }
        values.update(enrichment)
        return values

    def _card_changes(self, connection, existing, product, enrichment):
        desired = self._desired_card_values(connection, existing, product, enrichment)
        return {
            field: {"old": existing[field], "new": value}
            for field, value in desired.items()
            if existing[field] != value
        }

    @staticmethod
    def _ensure_catalog_batch(connection):
        batch = connection.execute(
            "SELECT * FROM catalog_excel_batches WHERE id = ?",
            (CATALOG_BATCH_ID,),
        ).fetchone()
        if batch is not None:
            return batch["id"]
        active_batch = connection.execute(
            "SELECT id FROM catalog_excel_batches WHERE status = 'active' "
            "ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
        now = utc_now()
        connection.execute(
            "INSERT INTO catalog_excel_batches ("
            "id, file_sha256, source_filename, sheet_name, source_type, operation_type, "
            "row_count, total_stock, positive_rows, zero_rows, status, "
            "created_at, applied_at, details_json"
            ") VALUES (?, ?, ?, ?, 'bitrix_catalog', 'catalog_sync', "
            "0, 0, 0, 0, ?, ?, ?, ?)",
            (
                CATALOG_BATCH_ID,
                CATALOG_BATCH_SHA256,
                "Bitrix catalog",
                "Каталог",
                "superseded" if active_batch else "active",
                now,
                now,
                _json({
                    "writes": "internal_catalog_only",
                    "inventory_operations": 0,
                    "initial_stock": 0,
                }),
            ),
        )
        return CATALOG_BATCH_ID

    def _insert_card(self, connection, product, enrichment):
        batch_id = self._ensure_catalog_batch(connection)
        now = utc_now()
        values = self._desired_card_values(connection, None, product, enrichment)
        excel_row = connection.execute(
            "SELECT COALESCE(MAX(excel_row), 1) + 1 FROM catalog_excel_products"
        ).fetchone()[0]
        raw = {
            "source": "bitrix_catalog",
            "external_product_id": _text(product.get("external_product_id")),
            "external_xml_id": _text(product.get("external_xml_id")),
        }
        columns = (
            "source_key", "created_batch_id", "current_batch_id", "raw_excel_json",
            "excel_row", "stock", "cell", "stock_source", "file_sha256",
            "moysklad_sync_status", "created_at", "updated_at",
        ) + tuple(values)
        row_values = (
            "bitrix:" + _text(product.get("external_product_id")),
            batch_id,
            batch_id,
            _json(raw),
            excel_row,
            0.0,
            None,
            "bitrix_catalog",
            CATALOG_BATCH_SHA256,
            "not_linked",
            now,
            now,
        ) + tuple(values[column] for column in values)
        connection.execute(
            "INSERT INTO catalog_excel_products ({}) VALUES ({})".format(
                ", ".join(columns),
                ", ".join("?" for _ in columns),
            ),
            row_values,
        )
        connection.execute(
            "UPDATE catalog_excel_batches SET row_count = row_count + 1, "
            "zero_rows = zero_rows + 1 WHERE id = ?",
            (batch_id,),
        )
        return connection.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _update_card(self, connection, existing, product, enrichment, changes):
        desired = self._desired_card_values(connection, existing, product, enrichment)
        updates = {field: desired[field] for field in changes}
        updates["updated_at"] = utc_now()
        assignments = ", ".join("{} = ?".format(field) for field in updates)
        connection.execute(
            "UPDATE catalog_excel_products SET {} WHERE id = ?".format(assignments),
            list(updates.values()) + [existing["id"]],
        )

    def duplicate_bitrix_links(self):
        if not self.database.exists():
            return []
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT bitrix_external_product_id, COUNT(*) AS row_count "
                "FROM catalog_excel_products "
                "WHERE active = 1 AND trim(COALESCE(bitrix_external_product_id, '')) <> '' "
                "GROUP BY bitrix_external_product_id HAVING COUNT(*) > 1 "
                "ORDER BY bitrix_external_product_id"
            ).fetchall()
        return [dict(row) for row in rows]
