#!/usr/bin/env python3
"""Back up, migrate and audit the unified product catalog safely."""

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_VERSION = "unified_catalog_v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import (  # noqa: E402
    CatalogDatabase,
    DEFAULT_CATALOG_DATABASE_PATH,
)
from app.services.shared_catalog import SharedCatalog  # noqa: E402


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def table_exists(connection, name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def scalar(connection, query, default=0):
    try:
        row = connection.execute(query).fetchone()
    except sqlite3.OperationalError:
        return default
    return row[0] if row and row[0] is not None else default


def database_snapshot(path):
    with sqlite3.connect(str(path)) as connection:
        return {
            "products": int(
                scalar(
                    connection,
                    "SELECT COUNT(*) FROM catalog_excel_products",
                )
            ),
            "active_products": int(
                scalar(
                    connection,
                    "SELECT COUNT(*) FROM catalog_excel_products "
                    "WHERE active = 1",
                )
            ),
            "stock_total": float(
                scalar(
                    connection,
                    "SELECT COALESCE(SUM(stock), 0) "
                    "FROM catalog_excel_products",
                )
            ),
            "managed_sales": int(
                scalar(connection, "SELECT COUNT(*) FROM erp_sales")
            ),
            "managed_receipts": int(
                scalar(connection, "SELECT COUNT(*) FROM erp_receipts")
            ),
            "movements": int(
                scalar(
                    connection,
                    "SELECT COUNT(*) FROM catalog_stock_movements",
                )
            ),
        }


def backup_database(source, backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / "catalog-before-{}-{}.db".format(
        MIGRATION_VERSION,
        stamp,
    )
    with sqlite3.connect(str(source)) as source_connection:
        backup_method = getattr(source_connection, "backup", None)
        if backup_method is not None:
            with sqlite3.connect(str(target)) as target_connection:
                backup_method(target_connection)
        else:
            escaped_target = str(target).replace("'", "''")
            completed = subprocess.run(
                [
                    "sqlite3",
                    str(source),
                    ".backup '{}'".format(escaped_target),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Не удалось создать SQLite backup: {}".format(
                        completed.stderr.strip() or "неизвестная ошибка"
                    )
                )
    with sqlite3.connect(str(target)) as connection:
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
    if check != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError("Резервная копия SQLite не прошла quick_check.")
    return target


def normalized(value):
    return str(value or "").strip().casefold()


def load_json_list(path):
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def audit_legacy_links(database_path, instance_dir):
    with sqlite3.connect(str(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT p.id, p.excel_name_raw AS name, "
            "COALESCE(b.name, p.excel_brand, '') AS brand, "
            "COALESCE(c.name, p.excel_category, '') AS category "
            "FROM catalog_excel_products p "
            "LEFT JOIN erp_brands b ON b.id = p.brand_id "
            "LEFT JOIN erp_categories c ON c.id = p.category_id"
        ).fetchall()
    products = [dict(row) for row in rows]
    products_by_id = {str(row["id"]): row for row in products}
    records = []
    for sale in load_json_list(instance_dir / "manual_sales.json"):
        if isinstance(sale, dict):
            records.append(("sale", str(sale.get("id") or ""), 0, sale))
    for receipt in load_json_list(instance_dir / "receipts.json"):
        if not isinstance(receipt, dict):
            continue
        positions = receipt.get("positions") or [receipt]
        for index, position in enumerate(positions):
            if isinstance(position, dict):
                records.append(
                    (
                        "receipt",
                        str(receipt.get("id") or ""),
                        index,
                        position,
                    )
                )

    report = {"matched": [], "ambiguous": [], "unmatched": []}
    for entity_type, entity_id, position_index, record in records:
        raw_product_id = str(
            record.get("catalog_product_id")
            or record.get("product_id")
            or ""
        )
        candidates = []
        method = "exact_id"
        if raw_product_id in products_by_id:
            candidates = [products_by_id[raw_product_id]]
        else:
            method = "exact_normalized_snapshot"
            name = normalized(
                record.get("product_name") or record.get("name")
            )
            brand = normalized(record.get("brand"))
            category = normalized(record.get("category"))
            candidates = [
                product
                for product in products
                if normalized(product["name"]) == name
                and (not brand or normalized(product["brand"]) == brand)
                and (
                    not category
                    or normalized(product["category"]) == category
                )
            ]
        item = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "position_index": position_index,
            "snapshot_product_id": raw_product_id,
            "snapshot_name": str(
                record.get("product_name") or record.get("name") or ""
            ),
            "candidate_product_ids": [
                str(candidate["id"]) for candidate in candidates
            ],
        }
        if len(candidates) == 1:
            item.update({
                "product_id": str(candidates[0]["id"]),
                "match_method": method,
            })
            report["matched"].append(item)
        elif candidates:
            report["ambiguous"].append(item)
        else:
            report["unmatched"].append(item)
    return report


def persist_legacy_audit(database_path, audit):
    database = CatalogDatabase(database_path)
    database.initialize()
    with database.transaction() as connection:
        for item in audit["matched"]:
            connection.execute(
                "INSERT OR REPLACE INTO erp_legacy_catalog_links "
                "(entity_type, entity_id, position_index, product_id, "
                "match_method, snapshot_product_id, snapshot_name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item["entity_type"],
                    item["entity_id"],
                    item["position_index"],
                    int(item["product_id"]),
                    item["match_method"],
                    item["snapshot_product_id"],
                    item["snapshot_name"],
                    now_iso(),
                ),
            )
            connection.execute(
                "DELETE FROM erp_legacy_catalog_ambiguities "
                "WHERE entity_type = ? AND entity_id = ? AND position_index = ?",
                (
                    item["entity_type"],
                    item["entity_id"],
                    item["position_index"],
                ),
            )
            if (
                item["entity_type"] == "receipt"
                and item["snapshot_product_id"]
                and not item["snapshot_product_id"].isdigit()
            ):
                occupied = connection.execute(
                    "SELECT id FROM catalog_excel_products "
                    "WHERE moysklad_product_id = ? AND id <> ?",
                    (item["snapshot_product_id"], int(item["product_id"])),
                ).fetchone()
                if occupied is None:
                    connection.execute(
                        "UPDATE catalog_excel_products "
                        "SET moysklad_product_id = COALESCE("
                        "moysklad_product_id, ?), "
                        "moysklad_sync_status = CASE "
                        "WHEN moysklad_product_id IS NULL THEN 'linked' "
                        "ELSE moysklad_sync_status END, updated_at = ? "
                        "WHERE id = ?",
                        (
                            item["snapshot_product_id"],
                            now_iso(),
                            int(item["product_id"]),
                        ),
                    )
        for item in audit["ambiguous"]:
            connection.execute(
                "INSERT OR REPLACE INTO erp_legacy_catalog_ambiguities "
                "(entity_type, entity_id, position_index, snapshot_product_id, "
                "snapshot_name, candidate_product_ids_json, resolution, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'manual_review', ?)",
                (
                    item["entity_type"],
                    item["entity_id"],
                    item["position_index"],
                    item["snapshot_product_id"],
                    item["snapshot_name"],
                    json.dumps(item["candidate_product_ids"]),
                    now_iso(),
                ),
            )
    return {
        "linked": len(audit["matched"]),
        "ambiguous": len(audit["ambiguous"]),
        "unmatched": len(audit["unmatched"]),
    }


def migration_applied(path):
    with sqlite3.connect(str(path)) as connection:
        if not table_exists(connection, "erp_schema_migrations"):
            return False
        return connection.execute(
            "SELECT 1 FROM erp_schema_migrations WHERE version = ?",
            (MIGRATION_VERSION,),
        ).fetchone() is not None


def migrate(path):
    before = database_snapshot(path)
    database = CatalogDatabase(path)
    database.initialize()
    audit = SharedCatalog(database).duplicate_audit()
    after = database_snapshot(path)
    if before["products"] != after["products"]:
        raise RuntimeError("Миграция изменила количество карточек товаров.")
    if abs(before["stock_total"] - after["stock_total"]) > 0.000001:
        raise RuntimeError("Миграция изменила суммарный остаток.")
    with database.transaction() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO erp_schema_migrations "
            "(version, applied_at, details_json) VALUES (?, ?, ?)",
            (
                MIGRATION_VERSION,
                now_iso(),
                json.dumps(
                    {
                        "before": before,
                        "after": after,
                        "ambiguous_products": len(
                            audit["ambiguous_products"]
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
    return before, after, audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_CATALOG_DATABASE_PATH,
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=PROJECT_ROOT / "instance" / "backups",
    )
    parser.add_argument("--instance-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    database_path = arguments.database.resolve()
    instance_dir = (
        arguments.instance_dir.resolve()
        if arguments.instance_dir
        else database_path.parent
    )
    if not database_path.exists():
        raise SystemExit("База каталога не найдена: {}".format(database_path))

    backup_path = None
    already_applied = migration_applied(database_path)
    if arguments.apply and already_applied:
        before = after = database_snapshot(database_path)
        audit = SharedCatalog(CatalogDatabase(database_path)).duplicate_audit()
        legacy = audit_legacy_links(database_path, instance_dir)
        persisted = persist_legacy_audit(database_path, legacy)
    elif arguments.apply:
        backup_path = backup_database(
            database_path,
            arguments.backup_dir.resolve(),
        )
        before, after, audit = migrate(database_path)
        legacy = audit_legacy_links(database_path, instance_dir)
        persisted = persist_legacy_audit(database_path, legacy)
    else:
        with tempfile.TemporaryDirectory() as temp_directory:
            dry_run_path = Path(temp_directory) / database_path.name
            backup_database(database_path, dry_run_path.parent)
            generated_backup = next(
                dry_run_path.parent.glob(
                    "catalog-before-{}-*.db".format(MIGRATION_VERSION)
                )
            )
            generated_backup.replace(dry_run_path)
            before, after, audit = migrate(dry_run_path)
            legacy = audit_legacy_links(dry_run_path, instance_dir)
            persisted = persist_legacy_audit(dry_run_path, legacy)
    result = {
        "version": MIGRATION_VERSION,
        "mode": "apply" if arguments.apply else "dry-run",
        "already_applied": already_applied,
        "backup": str(backup_path) if backup_path else "",
        "before": before,
        "after": after,
        "linked_variants": len(audit["linked_variants"]),
        "ambiguous_products": audit["ambiguous_products"],
        "potential_brand_aliases": audit["potential_brand_aliases"],
        "legacy_links": {
            "persisted": persisted,
            "ambiguous_records": legacy["ambiguous"],
            "unmatched_records": legacy["unmatched"],
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
