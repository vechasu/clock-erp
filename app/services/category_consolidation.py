"""Deterministic, transactional consolidation of global ERP categories."""

import hashlib
import json

from app.services.shared_catalog import catalog_name, normalized_name, utc_now


class CategoryConsolidationError(RuntimeError):
    pass


OPERATIONAL_TABLES = {
    "catalog_excel_products",
    "erp_brand_categories",
}

IMMUTABLE_HISTORY_TABLES = {
    "catalog_excel_receipt_rows",
    "erp_receipt_items",
    "erp_sale_items",
}


def _number(value):
    value = float(value or 0)
    return int(value) if value.is_integer() else value


def _row_hash(rows):
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_sha256(plan):
    stable = {
        key: value for key, value in plan.items()
        if key not in {"generated_at", "plan_sha256"}
    }
    return _row_hash(stable)


def _erp_category_reference_tables(connection):
    result = set()
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    for table_row in tables:
        table = table_row["name"]
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list({})".format(table)
        ).fetchall()
        if any(
            row["table"] == "erp_categories"
            and row["from"] == "category_id"
            for row in foreign_keys
        ):
            result.add(table)
    return result


class CategoryConsolidation:
    """Build and apply a safe merge plan for active logical duplicates.

    Current Product entities and BrandCategory relations are operational and
    move to the survivor. Receipt/sale rows are immutable snapshots, so their
    category IDs are deliberately retained; redundant Category records are
    archived instead of physically deleted to preserve those foreign keys.
    """

    def __init__(self, connection):
        self.connection = connection

    def _global_snapshot(self):
        row = self.connection.execute(
            "SELECT COUNT(*) AS active_products, "
            "COALESCE(SUM(stock), 0) AS total_stock, "
            "COALESCE(SUM(CASE WHEN stock != 0 THEN 1 ELSE 0 END), 0) "
            "AS nonzero_positions, "
            "COALESCE(SUM(CASE WHEN category_id IS NULL THEN 1 ELSE 0 END), 0) "
            "AS uncategorized_products, "
            "COALESCE(SUM(CASE WHEN category_id IS NULL THEN stock ELSE 0 END), 0) "
            "AS uncategorized_stock, "
            "COALESCE(SUM(CASE WHEN brand_id IS NOT NULL THEN 1 ELSE 0 END), 0) "
            "AS product_brand_assignments, COUNT(DISTINCT id) AS product_identities, "
            "COUNT(DISTINCT CASE WHEN trim(COALESCE(excel_article, '')) <> '' "
            "THEN excel_article END) AS distinct_skus "
            "FROM catalog_excel_products WHERE active = 1"
        ).fetchone()
        immutable_product_state = [
            [
                int(item["id"]), item["source_key"], item["excel_article"],
                item["brand_id"], _number(item["stock"]), item["active"],
            ]
            for item in self.connection.execute(
                "SELECT id, source_key, excel_article, brand_id, stock, active "
                "FROM catalog_excel_products ORDER BY id"
            ).fetchall()
        ]
        return {
            "active_products": int(row["active_products"]),
            "total_stock": _number(row["total_stock"]),
            "nonzero_positions": int(row["nonzero_positions"]),
            "uncategorized_products": int(row["uncategorized_products"]),
            "uncategorized_stock": _number(row["uncategorized_stock"]),
            "product_brand_assignments": int(row["product_brand_assignments"]),
            "product_identities": int(row["product_identities"]),
            "distinct_skus": int(row["distinct_skus"]),
            "immutable_product_state_sha256": _row_hash(
                immutable_product_state
            ),
        }

    def _history_snapshot(self):
        result = {}
        for table in sorted(IMMUTABLE_HISTORY_TABLES):
            rows = [
                [int(row["_rowid"]), row["category_id"]]
                for row in self.connection.execute(
                    "SELECT rowid AS _rowid, category_id FROM {} "
                    "ORDER BY rowid".format(
                        table
                    )
                ).fetchall()
            ]
            result[table] = {"rows": len(rows), "sha256": _row_hash(rows)}
        return result

    def _category_metrics(self, category_ids):
        placeholders = ", ".join("?" for _ in category_ids)
        product_row = self.connection.execute(
            "SELECT COUNT(*) AS all_products, "
            "COALESCE(SUM(active), 0) AS products, "
            "COALESCE(SUM(CASE WHEN active = 1 AND stock != 0 THEN 1 ELSE 0 END), 0) "
            "AS in_stock, COALESCE(SUM(CASE WHEN active = 1 THEN stock ELSE 0 END), 0) "
            "AS stock FROM catalog_excel_products WHERE category_id IN ({})".format(
                placeholders
            ),
            category_ids,
        ).fetchone()
        product_brands = {
            int(row[0]) for row in self.connection.execute(
                "SELECT DISTINCT brand_id FROM catalog_excel_products "
                "WHERE active = 1 AND brand_id IS NOT NULL "
                "AND category_id IN ({})".format(placeholders),
                category_ids,
            ).fetchall()
        }
        structural_brands = {
            int(row[0]) for row in self.connection.execute(
                "SELECT DISTINCT brand_id FROM erp_brand_categories "
                "WHERE category_id IN ({})".format(placeholders),
                category_ids,
            ).fetchall()
        }
        return {
            "all_products": int(product_row["all_products"]),
            "products": int(product_row["products"]),
            "in_stock": int(product_row["in_stock"]),
            "stock": _number(product_row["stock"]),
            "product_brands": sorted(product_brands),
            "structural_brands": sorted(structural_brands),
            "brands": sorted(product_brands | structural_brands),
        }

    def _reference_counts(self, category_ids, reference_tables):
        placeholders = ", ".join("?" for _ in category_ids)
        return {
            table: int(self.connection.execute(
                "SELECT COUNT(*) FROM {} WHERE category_id IN ({})".format(
                    table, placeholders
                ),
                category_ids,
            ).fetchone()[0])
            for table in sorted(reference_tables)
        }

    def _candidate(self, row):
        category_id = int(row["id"])
        relations = int(self.connection.execute(
            "SELECT COUNT(*) FROM erp_brand_categories WHERE category_id = ?",
            (category_id,),
        ).fetchone()[0])
        products = int(self.connection.execute(
            "SELECT COUNT(*) FROM catalog_excel_products WHERE category_id = ?",
            (category_id,),
        ).fetchone()[0])
        history = sum(
            int(self.connection.execute(
                "SELECT COUNT(*) FROM {} WHERE category_id = ?".format(table),
                (category_id,),
            ).fetchone()[0])
            for table in IMMUTABLE_HISTORY_TABLES
        )
        return {
            "id": category_id,
            "name": row["name"],
            "stored_normalized_name": row["normalized_name"],
            "owner_brand_id": int(row["brand_id"]),
            "created_at": row["created_at"],
            "product_references": products,
            "brand_category_references": relations,
            "immutable_history_references": history,
        }

    @staticmethod
    def _survivor(candidates):
        return sorted(
            candidates,
            key=lambda item: (
                -item["brand_category_references"],
                -item["product_references"],
                item["created_at"],
                item["id"],
            ),
        )[0]

    def build_plan(self):
        reference_tables = _erp_category_reference_tables(self.connection)
        unknown_tables = sorted(
            reference_tables - OPERATIONAL_TABLES - IMMUTABLE_HISTORY_TABLES
        )
        grouped = {}
        rows = self.connection.execute(
            "SELECT * FROM erp_categories WHERE active = 1 AND id <> 0 "
            "ORDER BY id"
        ).fetchall()
        for row in rows:
            grouped.setdefault(normalized_name(row["name"]), []).append(row)

        groups = []
        for key in sorted(grouped):
            variants = grouped[key]
            if not key or len(variants) < 2:
                continue
            candidates = [self._candidate(row) for row in variants]
            survivor = self._survivor(candidates)
            redundant_ids = [
                item["id"] for item in candidates
                if item["id"] != survivor["id"]
            ]
            all_ids = [item["id"] for item in candidates]
            normalization_consistent = all(
                normalized_name(item["name"]) == key
                and normalized_name(item["stored_normalized_name"]) == key
                and catalog_name(item["name"])
                for item in candidates
            )
            status = (
                "SAFE_TO_MERGE"
                if normalization_consistent and not unknown_tables
                else "MANUAL_REVIEW_REQUIRED"
            )
            before = self._category_metrics(all_ids)
            alias_metrics = self._category_metrics(redundant_ids)
            canonical_relation_brands = set(
                self._category_metrics([survivor["id"]])["structural_brands"]
            )
            relation_union = set(before["structural_brands"])
            reasons = []
            if unknown_tables:
                reasons.append(
                    "Unknown operational references: {}".format(
                        ", ".join(unknown_tables)
                    )
                )
            if not normalization_consistent:
                reasons.append("Stored and current normalization disagree.")
            groups.append({
                "normalized_name": key,
                "exact_names": [item["name"] for item in candidates],
                "categories": candidates,
                "canonical_id": survivor["id"],
                "redundant_ids": redundant_ids,
                "why_canonical": (
                    "Most existing BrandCategory relations, then Product usage, "
                    "then oldest/stable ID; immutable history remains untouched."
                ),
                "products_to_move": alias_metrics["all_products"],
                "brand_categories_to_insert": len(
                    relation_union - canonical_relation_brands
                ),
                "brand_categories_to_remove": self._reference_counts(
                    redundant_ids, {"erp_brand_categories"}
                )["erp_brand_categories"],
                "categories_to_archive": len(redundant_ids),
                "other_active_refs": 0,
                "references": self._reference_counts(
                    all_ids, reference_tables
                ),
                "before": before,
                "predicted_after": before,
                "status": status,
                "manual_review_reasons": reasons,
            })
        plan = {
            "generated_at": utc_now(),
            "mode": "dry_run",
            "reference_contract": {
                "operational": sorted(OPERATIONAL_TABLES),
                "immutable_history": sorted(IMMUTABLE_HISTORY_TABLES),
                "unknown": unknown_tables,
            },
            "baseline": self._global_snapshot(),
            "immutable_history": self._history_snapshot(),
            "groups": groups,
            "safe_groups": sum(
                item["status"] == "SAFE_TO_MERGE" for item in groups
            ),
            "manual_review_groups": sum(
                item["status"] == "MANUAL_REVIEW_REQUIRED" for item in groups
            ),
            "predicted_changes": sum(
                item["products_to_move"]
                + item["brand_categories_to_insert"]
                + item["brand_categories_to_remove"]
                + item["categories_to_archive"]
                for item in groups
                if item["status"] == "SAFE_TO_MERGE"
            ),
        }
        plan["plan_sha256"] = plan_sha256(plan)
        return plan

    def apply(self, expected_plan_sha256=None):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            plan = self.build_plan()
            if (
                expected_plan_sha256
                and plan["plan_sha256"] != expected_plan_sha256
            ):
                raise CategoryConsolidationError(
                    "Database changed after dry-run; plan hash mismatch."
                )
            before_global = plan["baseline"]
            before_history = plan["immutable_history"]
            applied_groups = []
            for group in plan["groups"]:
                if group["status"] != "SAFE_TO_MERGE":
                    continue
                canonical_id = group["canonical_id"]
                redundant_ids = group["redundant_ids"]
                placeholders = ", ".join("?" for _ in redundant_ids)
                insert_cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO erp_brand_categories "
                    "(brand_id, category_id, created_at) "
                    "SELECT brand_id, ?, MIN(created_at) "
                    "FROM erp_brand_categories WHERE category_id IN ({}) "
                    "GROUP BY brand_id".format(placeholders),
                    [canonical_id] + redundant_ids,
                )
                product_cursor = self.connection.execute(
                    "UPDATE catalog_excel_products SET category_id = ? "
                    "WHERE category_id IN ({})".format(placeholders),
                    [canonical_id] + redundant_ids,
                )
                relation_cursor = self.connection.execute(
                    "DELETE FROM erp_brand_categories "
                    "WHERE category_id IN ({})".format(placeholders),
                    redundant_ids,
                )
                archive_cursor = self.connection.execute(
                    "UPDATE erp_categories SET active = 0, updated_at = ? "
                    "WHERE active = 1 AND id IN ({})".format(placeholders),
                    [utc_now()] + redundant_ids,
                )
                after = self._category_metrics([canonical_id])
                if after != group["before"]:
                    raise CategoryConsolidationError(
                        "Group reconciliation failed for {}.".format(
                            group["normalized_name"]
                        )
                    )
                remaining_products = self._reference_counts(
                    redundant_ids, {"catalog_excel_products"}
                )["catalog_excel_products"]
                remaining_relations = self._reference_counts(
                    redundant_ids, {"erp_brand_categories"}
                )["erp_brand_categories"]
                if remaining_products or remaining_relations:
                    raise CategoryConsolidationError(
                        "Operational references remain on redundant IDs."
                    )
                applied_groups.append({
                    "normalized_name": group["normalized_name"],
                    "canonical_id": canonical_id,
                    "redundant_ids": redundant_ids,
                    "products_moved": int(product_cursor.rowcount),
                    "brand_categories_inserted": int(insert_cursor.rowcount),
                    "brand_categories_removed": int(relation_cursor.rowcount),
                    "categories_archived": int(archive_cursor.rowcount),
                    "reconciliation": after,
                })
            after_global = self._global_snapshot()
            after_history = self._history_snapshot()
            if before_global != after_global:
                raise CategoryConsolidationError(
                    "Global Product/stock reconciliation failed."
                )
            if before_history != after_history:
                raise CategoryConsolidationError(
                    "Immutable historical references changed."
                )
            foreign_key_errors = [
                dict(row)
                for row in self.connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
            ]
            if foreign_key_errors:
                raise CategoryConsolidationError(
                    "Foreign-key integrity check failed."
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        second_dry_run = self.build_plan()
        if second_dry_run["predicted_changes"]:
            raise CategoryConsolidationError(
                "Second dry-run is not idempotent."
            )
        return {
            **plan,
            "mode": "apply",
            "applied_at": utc_now(),
            "applied_groups": applied_groups,
            "after_baseline": after_global,
            "after_immutable_history": after_history,
            "second_dry_run_changes": second_dry_run["predicted_changes"],
            "remaining_duplicate_groups": second_dry_run["groups"],
        }
