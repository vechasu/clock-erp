import hashlib
import json
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.clients.bitrix_catalog import normalize_key


IMPORT_MODES = {"preview", "create_only", "fill_empty", "update_content", "full_sync"}
MODE_RANK = {"fill_empty": 1, "update_content": 2, "full_sync": 3}
CONTENT_FIELDS = {
    "name",
    "slug",
    "article",
    "barcode",
    "brand",
    "preview_text",
    "detail_text",
    "preview_text_format",
    "detail_text_format",
    "source_url",
    "external_xml_id",
    "external_created_at",
    "external_updated_at",
}
FULL_FIELDS = CONTENT_FIELDS | {"active"}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_payload(product):
    return json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(product):
    return hashlib.sha256(canonical_payload(product).encode("utf-8")).hexdigest()


def is_empty(value):
    return value in (None, "", [], {})


def product_values(product):
    return {
        "name": str(product.get("name") or "").strip(),
        "slug": str(product.get("code") or "").strip(),
        "article": str(product.get("external_sku") or "").strip(),
        "barcode": str(product.get("barcode") or "").strip(),
        "brand": str(product.get("brand") or "").strip(),
        "preview_text": product.get("preview_text") or "",
        "detail_text": product.get("detail_text") or "",
        "preview_text_format": product.get("preview_text_type") or "text",
        "detail_text_format": product.get("detail_text_type") or "text",
        "active": 1 if product.get("active", True) else 0,
        "source_url": product.get("url") or "",
        "external_source": product.get("external_source") or "bitrix",
        "external_product_id": str(product.get("external_product_id") or ""),
        "external_xml_id": str(product.get("external_xml_id") or ""),
        "external_created_at": product.get("created_at"),
        "external_updated_at": product.get("updated_at"),
    }


class BitrixCatalogImporter:
    def __init__(self, database=None, confirmed_mappings=None, failure_hook=None):
        self.database = database or CatalogDatabase()
        self.confirmed_mappings = confirmed_mappings or {}
        self.failure_hook = failure_hook

    def preview(self, products, target_mode="full_sync"):
        if target_mode not in IMPORT_MODES - {"preview"}:
            raise ValueError("Unsupported preview target mode")
        if not self.database.exists():
            return self._empty_database_preview(products, target_mode)
        with self.database.connect() as connection:
            return self._preview_with_connection(connection, products, target_mode)

    def import_products(self, products, mode="preview"):
        if mode not in IMPORT_MODES:
            raise ValueError("Unsupported import mode")
        products = list(products)
        if mode == "preview":
            return self.preview(products, "full_sync")

        self.database.initialize()
        started_at = utc_now()
        try:
            with self.database.transaction() as connection:
                run_id = connection.execute(
                    "INSERT INTO catalog_sync_runs (mode, status, started_at) VALUES (?, ?, ?)",
                    (mode, "running", started_at),
                ).lastrowid
                result = self._apply_products(connection, products, mode)
                connection.execute(
                    """
                    UPDATE catalog_sync_runs SET
                        status = 'success', finished_at = ?, products_received = ?,
                        products_created = ?, products_updated = ?,
                        products_unchanged = ?, products_conflicted = ?, details_json = ?
                    WHERE id = ?
                    """,
                    (
                        utc_now(), len(products), result["created"], result["updated"],
                        result["unchanged"], result["conflicts"],
                        json.dumps(result["items"], ensure_ascii=False), run_id,
                    ),
                )
                result.update({"mode": mode, "status": "success", "sync_run_id": run_id})
                return result
        except Exception as error:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO catalog_sync_runs
                        (mode, status, started_at, finished_at, products_received,
                         errors_count, error_summary)
                    VALUES (?, 'failed', ?, ?, ?, 1, ?)
                    """,
                    (mode, started_at, utc_now(), len(products), type(error).__name__),
                )
            raise

    def _empty_database_preview(self, products, target_mode):
        items = []
        for product in products:
            values = product_values(product)
            items.append({
                "external_product_id": values["external_product_id"],
                "status": "new",
                "match_method": "not_found",
                "target_mode": target_mode,
                "changes": {key: {"old": None, "new": value} for key, value in values.items()},
            })
        return {
            "mode": "preview",
            "target_mode": target_mode,
            "writes_performed": 0,
            "created": len(items),
            "updated": 0,
            "unchanged": 0,
            "conflicts": 0,
            "items": items,
        }

    def _preview_with_connection(self, connection, products, target_mode):
        result = {"created": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "items": []}
        for product in products:
            match = self._match_product(connection, product)
            item = self._preview_item(connection, product, match, target_mode)
            result["items"].append(item)
            if item["status"] == "new":
                result["created"] += 1
            elif item["status"] == "changed":
                result["updated"] += 1
            elif item["status"] == "requires_mapping":
                result["conflicts"] += 1
            else:
                result["unchanged"] += 1
        result.update({"mode": "preview", "target_mode": target_mode, "writes_performed": 0})
        return result

    def _apply_products(self, connection, products, mode):
        result = {"created": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "items": []}
        for index, product in enumerate(products):
            if self.failure_hook:
                self.failure_hook(index, product)
            match = self._match_product(connection, product)
            if match["status"] == "ambiguous":
                result["conflicts"] += 1
                result["items"].append(self._conflict_item(product, match))
                continue
            existing = match.get("product")
            if existing is not None and mode == "create_only":
                result["unchanged"] += 1
                result["items"].append(self._result_item(product, "unchanged", match, {}))
                continue
            incoming_hash = payload_hash(product)
            if existing is not None:
                previous_rank = MODE_RANK.get(existing["last_sync_mode"], 0)
                current_rank = MODE_RANK.get(mode, 0)
                if existing["payload_hash"] == incoming_hash and previous_rank >= current_rank:
                    result["unchanged"] += 1
                    result["items"].append(self._result_item(product, "unchanged", match, {}))
                    continue

            if existing is None:
                product_id = self._insert_product(connection, product, incoming_hash, mode)
                self._replace_relations(connection, product_id, product, include_prices=True)
                result["created"] += 1
                result["items"].append(self._result_item(product, "created", match, {}))
                continue

            changes = self._update_product(connection, existing, product, incoming_hash, mode)
            product_id = existing["id"]
            if mode == "full_sync":
                self._replace_relations(connection, product_id, product, include_prices=True)
            elif mode == "update_content":
                self._replace_relations(connection, product_id, product, include_prices=False)
            elif mode == "fill_empty":
                self._fill_empty_relations(connection, product_id, product)
            if changes or mode in {"full_sync", "update_content"}:
                result["updated"] += 1
                result["items"].append(self._result_item(product, "updated", match, changes))
            else:
                result["unchanged"] += 1
                result["items"].append(self._result_item(product, "unchanged", match, changes))
        return result

    def _match_product(self, connection, product):
        values = product_values(product)
        confirmed_key = f"{values['external_source']}:{values['external_product_id']}"
        confirmed_id = self.confirmed_mappings.get(confirmed_key)
        if confirmed_id:
            row = connection.execute(
                "SELECT * FROM catalog_products WHERE id = ?", (int(confirmed_id),)
            ).fetchone()
            if row:
                return {"status": "matched", "method": "confirmed_mapping", "product": row, "candidate_count": 1}

        row = connection.execute(
            "SELECT * FROM catalog_products WHERE external_source = ? AND external_product_id = ?",
            (values["external_source"], values["external_product_id"]),
        ).fetchone()
        if row:
            return {"status": "matched", "method": "external_id", "product": row, "candidate_count": 1}

        for field, value, method in (
            ("external_xml_id", values["external_xml_id"], "xml_id"),
            ("article", values["article"], "article"),
        ):
            if not value:
                continue
            rows = connection.execute(
                f"SELECT * FROM catalog_products WHERE {field} = ? AND external_source <> ?",
                (value, values["external_source"]),
            ).fetchall()
            if len(rows) == 1:
                return {"status": "matched", "method": method, "product": rows[0], "candidate_count": 1}
            if len(rows) > 1:
                return {"status": "ambiguous", "method": method, "product": None, "candidate_count": len(rows)}

        target_name = normalize_key(values["name"])
        if target_name:
            rows = connection.execute(
                "SELECT * FROM catalog_products WHERE name IS NOT NULL AND external_source <> ?",
                (values["external_source"],),
            ).fetchall()
            candidates = [row for row in rows if normalize_key(row["name"]) == target_name]
            if len(candidates) == 1:
                return {"status": "matched", "method": "exact_name", "product": candidates[0], "candidate_count": 1}
            if len(candidates) > 1:
                return {"status": "ambiguous", "method": "exact_name", "product": None, "candidate_count": len(candidates)}
        return {"status": "new", "method": "not_found", "product": None, "candidate_count": 0}

    def _preview_item(self, connection, product, match, target_mode):
        if match["status"] == "ambiguous":
            return self._conflict_item(product, match)
        if match.get("product") is None:
            values = product_values(product)
            return {
                "external_product_id": values["external_product_id"],
                "status": "new",
                "match_method": match["method"],
                "target_mode": target_mode,
                "changes": {key: {"old": None, "new": value} for key, value in values.items()},
            }
        existing = match["product"]
        changes = self._scalar_changes(existing, product, target_mode)
        relation_changes = self._relation_changes(connection, existing["id"], product, target_mode)
        changes.update(relation_changes)
        return self._result_item(product, "changed" if changes else "unchanged", match, changes)

    def _scalar_changes(self, existing, product, mode):
        incoming = product_values(product)
        allowed = FULL_FIELDS if mode == "full_sync" else CONTENT_FIELDS
        changes = {}
        for field in allowed:
            old = existing[field]
            new = incoming[field]
            if mode == "fill_empty" and not is_empty(old):
                continue
            if old != new:
                changes[field] = {"old": old, "new": new}
        return changes

    def _relation_changes(self, connection, product_id, product, mode):
        changes = {}
        relation_specs = {
            "categories": ("catalog_product_categories", len(product.get("categories") or [])),
            "properties": ("catalog_product_property_values", len(product.get("properties") or [])),
            "images": ("catalog_images", len(product.get("images") or [])),
            "offers": ("catalog_offers", len(product.get("offers") or [])),
            "prices": ("catalog_prices", len([p for p in product.get("prices") or [] if not p.get("is_purchase")])),
        }
        for label, (table, new_count) in relation_specs.items():
            if mode == "update_content" and label == "prices":
                continue
            old_count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE product_id = ?", (product_id,)
            ).fetchone()[0]
            if mode == "fill_empty" and old_count > 0:
                continue
            if old_count != new_count:
                changes[label] = {"old_count": old_count, "new_count": new_count}
        return changes

    def _insert_product(self, connection, product, incoming_hash, mode):
        now = utc_now()
        values = product_values(product)
        sync_mode = "full_sync" if mode == "create_only" else mode
        cursor = connection.execute(
            """
            INSERT INTO catalog_products (
                name, slug, article, barcode, brand, preview_text, detail_text,
                preview_text_format, detail_text_format, active, source_url,
                external_source, external_product_id, external_xml_id,
                external_created_at, external_updated_at, payload_hash,
                normalized_payload_json, created_at, updated_at, first_synced_at,
                last_synced_at, last_sync_mode
            ) VALUES (
                :name, :slug, :article, :barcode, :brand, :preview_text, :detail_text,
                :preview_text_format, :detail_text_format, :active, :source_url,
                :external_source, :external_product_id, :external_xml_id,
                :external_created_at, :external_updated_at, :payload_hash,
                :normalized_payload_json, :created_at, :updated_at, :first_synced_at,
                :last_synced_at, :last_sync_mode
            )
            """,
            dict(
                values,
                payload_hash=incoming_hash,
                normalized_payload_json=canonical_payload(product),
                created_at=now,
                updated_at=now,
                first_synced_at=now,
                last_synced_at=now,
                last_sync_mode=sync_mode,
            ),
        )
        return cursor.lastrowid

    def _update_product(self, connection, existing, product, incoming_hash, mode):
        incoming = product_values(product)
        changes = self._scalar_changes(existing, product, mode)
        updates = {field: change["new"] for field, change in changes.items()}
        updates.update({
            "external_source": incoming["external_source"],
            "external_product_id": incoming["external_product_id"],
            "external_xml_id": incoming["external_xml_id"],
            "payload_hash": incoming_hash,
            "normalized_payload_json": canonical_payload(product),
            "updated_at": utc_now(),
            "last_synced_at": utc_now(),
            "last_sync_mode": (
                mode
                if MODE_RANK.get(mode, 0) >= MODE_RANK.get(existing["last_sync_mode"], 0)
                else existing["last_sync_mode"]
            ),
        })
        assignments = ", ".join(f"{field} = :{field}" for field in updates)
        updates["id"] = existing["id"]
        connection.execute(f"UPDATE catalog_products SET {assignments} WHERE id = :id", updates)
        return changes

    def _replace_relations(self, connection, product_id, product, include_prices):
        self._replace_categories(connection, product_id, product.get("categories") or [])
        self._replace_properties(connection, product_id, product.get("properties") or [])
        connection.execute("DELETE FROM catalog_images WHERE product_id = ?", (product_id,))
        self._insert_images(connection, product_id, None, product.get("images") or [])
        connection.execute("DELETE FROM catalog_offers WHERE product_id = ?", (product_id,))
        self._insert_offers(connection, product_id, product.get("offers") or [])
        if include_prices:
            connection.execute("DELETE FROM catalog_prices WHERE product_id = ?", (product_id,))
            self._insert_prices(connection, product_id, None, product.get("prices") or [])

    def _fill_empty_relations(self, connection, product_id, product):
        specs = (
            ("catalog_product_categories", self._replace_categories, product.get("categories") or []),
            ("catalog_product_property_values", self._replace_properties, product.get("properties") or []),
        )
        for table, method, values in specs:
            count = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE product_id = ?", (product_id,)).fetchone()[0]
            if count == 0:
                method(connection, product_id, values)
        if connection.execute("SELECT COUNT(*) FROM catalog_images WHERE product_id = ?", (product_id,)).fetchone()[0] == 0:
            self._insert_images(connection, product_id, None, product.get("images") or [])
        if connection.execute("SELECT COUNT(*) FROM catalog_offers WHERE product_id = ?", (product_id,)).fetchone()[0] == 0:
            self._insert_offers(connection, product_id, product.get("offers") or [])
        if connection.execute("SELECT COUNT(*) FROM catalog_prices WHERE product_id = ?", (product_id,)).fetchone()[0] == 0:
            self._insert_prices(connection, product_id, None, product.get("prices") or [])

    def _upsert_category_path(self, connection, category):
        now = utc_now()
        parent_id = None
        path = category.get("path_items") or category.get("path") or []
        for part in path:
            if isinstance(part, dict):
                external_id = str(part.get("id") or "")
                name = str(part.get("name") or "")
            else:
                external_id = ""
                name = str(part or "")
            if not external_id:
                continue
            existing = connection.execute(
                "SELECT id FROM catalog_categories WHERE external_source = 'bitrix' AND external_category_id = ?",
                (external_id,),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE catalog_categories SET name = ?, parent_id = ?, updated_at = ? WHERE id = ?",
                    (name, parent_id, now, existing["id"]),
                )
                parent_id = existing["id"]
            else:
                parent_id = connection.execute(
                    """
                    INSERT INTO catalog_categories (
                        external_category_id, name, parent_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (external_id, name, parent_id, now, now),
                ).lastrowid
        external_id = str(category.get("id") or "")
        if not external_id:
            return parent_id
        path_json = json.dumps(path, ensure_ascii=False)
        existing = connection.execute(
            "SELECT id FROM catalog_categories WHERE external_source = 'bitrix' AND external_category_id = ?",
            (external_id,),
        ).fetchone()
        values = (
            category.get("xml_id") or "", category.get("code") or "",
            category.get("name") or "", parent_id if str(category.get("parent_id") or "") else None,
            int(category.get("sort") or 500), 1 if category.get("active", True) else 0,
            path_json, now,
        )
        if existing:
            connection.execute(
                """
                UPDATE catalog_categories SET
                    external_xml_id = ?, code = ?, name = ?, parent_id = ?,
                    sort = ?, active = ?, path_json = ?, updated_at = ?
                WHERE id = ?
                """,
                values + (existing["id"],),
            )
            return existing["id"]
        return connection.execute(
            """
            INSERT INTO catalog_categories (
                external_category_id, external_xml_id, code, name, parent_id,
                sort, active, path_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (external_id,) + values[:7] + (now, now),
        ).lastrowid

    def _replace_categories(self, connection, product_id, categories):
        connection.execute("DELETE FROM catalog_product_categories WHERE product_id = ?", (product_id,))
        primary_external_id = None
        for position, category in enumerate(categories):
            category_id = self._upsert_category_path(connection, category)
            if category_id is None:
                continue
            is_primary = position == 0
            if is_primary:
                primary_external_id = category_id
            connection.execute(
                "INSERT OR REPLACE INTO catalog_product_categories "
                "(product_id, category_id, is_primary, sort) VALUES (?, ?, ?, ?)",
                (product_id, category_id, 1 if is_primary else 0, position),
            )
        connection.execute(
            "UPDATE catalog_products SET primary_category_id = ? WHERE id = ?",
            (primary_external_id, product_id),
        )

    def _upsert_property(self, connection, prop):
        now = utc_now()
        external_id = str(prop.get("id") or prop.get("code") or "")
        existing = connection.execute(
            "SELECT id FROM catalog_properties WHERE external_source = 'bitrix' AND external_property_id = ?",
            (external_id,),
        ).fetchone()
        values = (
            prop.get("code") or "", prop.get("name") or "",
            prop.get("type") or "string", 1 if prop.get("multiple") else 0,
            int(prop.get("sort") or 500), now,
        )
        if existing:
            connection.execute(
                """
                UPDATE catalog_properties SET code = ?, name = ?, property_type = ?,
                    multiple = ?, sort = ?, updated_at = ? WHERE id = ?
                """,
                values + (existing["id"],),
            )
            return existing["id"]
        return connection.execute(
            """
            INSERT INTO catalog_properties (
                external_property_id, code, name, property_type, multiple,
                sort, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (external_id,) + values[:5] + (now, now),
        ).lastrowid

    def _replace_properties(self, connection, product_id, properties):
        connection.execute("DELETE FROM catalog_product_property_values WHERE product_id = ?", (product_id,))
        for prop in properties:
            property_id = self._upsert_property(connection, prop)
            connection.execute(
                """
                INSERT INTO catalog_product_property_values (
                    product_id, property_id, value_json, display_value_json,
                    enum_id_json, sort
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id, property_id,
                    json.dumps(prop.get("value"), ensure_ascii=False),
                    json.dumps(prop.get("display_value"), ensure_ascii=False),
                    json.dumps(prop.get("enum_id"), ensure_ascii=False),
                    int(prop.get("sort") or 500),
                ),
            )

    def _insert_images(self, connection, product_id, offer_id, images):
        now = utc_now()
        for image in images:
            url = image.get("original_url") or image.get("url") or ""
            if not url:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO catalog_images (
                    product_id, offer_id, external_file_id, image_type, original_url,
                    filename, mime_type, width, height, file_size, sort,
                    is_primary, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id, offer_id, str(image.get("id") or ""),
                    image.get("kind") or image.get("type") or "gallery", url,
                    image.get("filename") or "", image.get("mime_type") or "",
                    int(image.get("width") or 0), int(image.get("height") or 0),
                    int(image.get("file_size") or 0), int(image.get("order") or image.get("sort") or 0),
                    1 if image.get("is_primary") else 0, now, now,
                ),
            )

    def _insert_prices(self, connection, product_id, offer_id, prices):
        now = utc_now()
        for price in prices:
            if price.get("is_purchase") or price.get("value") is None:
                continue
            connection.execute(
                """
                INSERT OR REPLACE INTO catalog_prices (
                    product_id, offer_id, external_price_id, price_type, price_name,
                    amount, currency, is_base, old_amount, old_amount_source,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id, offer_id, price.get("type_id") or "",
                    price.get("type_code") or price.get("role") or "sale",
                    price.get("type_name") or "", str(price.get("value")),
                    price.get("currency") or "", 1 if price.get("role") == "base" else 0,
                    str(price.get("old_value")) if price.get("old_value") is not None else None,
                    price.get("old_value_source") or None, now, now,
                ),
            )

    def _insert_offers(self, connection, product_id, offers):
        now = utc_now()
        for offer in offers:
            offer_hash = payload_hash(offer)
            cursor = connection.execute(
                """
                INSERT INTO catalog_offers (
                    product_id, external_offer_id, external_xml_id, code, name,
                    article, barcode, active, external_updated_at, payload_hash,
                    normalized_payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id, offer.get("external_offer_id") or offer.get("external_product_id") or "",
                    offer.get("external_xml_id") or "", offer.get("code") or "",
                    offer.get("name") or "", offer.get("external_sku") or "",
                    offer.get("barcode") or "", 1 if offer.get("active", True) else 0,
                    offer.get("updated_at"), offer_hash, canonical_payload(offer), now, now,
                ),
            )
            offer_id = cursor.lastrowid
            for prop in offer.get("properties") or []:
                property_id = self._upsert_property(connection, prop)
                connection.execute(
                    """
                    INSERT INTO catalog_offer_property_values (
                        offer_id, property_id, value_json, display_value_json,
                        enum_id_json, sort
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        offer_id, property_id,
                        json.dumps(prop.get("value"), ensure_ascii=False),
                        json.dumps(prop.get("display_value"), ensure_ascii=False),
                        json.dumps(prop.get("enum_id"), ensure_ascii=False),
                        int(prop.get("sort") or 500),
                    ),
                )
            self._insert_images(connection, None, offer_id, offer.get("images") or [])
            self._insert_prices(connection, None, offer_id, offer.get("prices") or [])

    @staticmethod
    def _conflict_item(product, match):
        return {
            "external_product_id": str(product.get("external_product_id") or ""),
            "status": "requires_mapping",
            "match_method": match["method"],
            "candidate_count": match["candidate_count"],
            "changes": {},
        }

    @staticmethod
    def _result_item(product, status, match, changes):
        return {
            "external_product_id": str(product.get("external_product_id") or ""),
            "status": status,
            "match_method": match["method"],
            "candidate_count": match["candidate_count"],
            "changes": changes,
        }
