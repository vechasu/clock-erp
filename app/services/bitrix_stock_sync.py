"""Atomic synchronization of Bitrix catalog quantity into existing ERP cards."""

import math
import uuid
from datetime import datetime, timezone

from app.catalog_db import CatalogDatabase
from app.services.audit_journal import AuditJournal
from app.services.bitrix_erp_product_sync import BitrixERPProductSync
from app.services.inventory_lock import assert_no_active_inventory
from app.services.protected_catalog_brands import (
    canonical_protected_brand,
    protected_brand_rows,
    protected_product_brand,
    protected_state_digest,
)


SOURCE_FIELD = "CCatalogProduct.QUANTITY"
EXPORT_FIELD = "available_quantity"
SITE_AVAILABILITY_FILTER = "CATALOG_QUANTITY > 0"


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value):
    return str(value or "").strip()


def exact_quantity(product):
    if product.get("stock_source_field") != SOURCE_FIELD:
        return None
    value = product.get("stock")
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


class BitrixStockSync:
    def __init__(self, database=None):
        self.database = database or CatalogDatabase()
        self.card_sync = BitrixERPProductSync(self.database)

    def synchronize(self, products, apply=False, source_generated_at=""):
        products = list(products)
        if not self.database.exists():
            raise ValueError("ERP catalog database does not exist")
        run_id = "bitrix-stock-{}".format(
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        with self.database.transaction() as connection:
            assert_no_active_inventory(connection)
            protected_before = protected_state_digest(connection)
            protected_rows = protected_brand_rows(connection)
            protected_brand_ids = {int(row["id"]) for row in protected_rows}
            report = self._report(run_id, apply, source_generated_at, protected_rows)
            seen_source_ids = set()
            for product in products:
                source_id = _text(product.get("external_product_id"))
                if source_id in seen_source_ids:
                    report["source_duplicates"] += 1
                    report["not_synchronized"].append(
                        self._item(product, "duplicate_source_id")
                    )
                    continue
                if source_id:
                    seen_source_ids.add(source_id)
                protected = protected_product_brand(product)
                if protected:
                    report["protected"] += 1
                    report["protected_by_brand"][protected] += 1
                    continue
                quantity = exact_quantity(product)
                if quantity is None:
                    report["invalid_stock"] += 1
                    report["not_synchronized"].append(
                        self._item(product, "exact_quantity_unavailable")
                    )
                    continue
                deleted = self.card_sync._deleted_product(connection, product)
                if deleted is not None:
                    report["deleted"] += 1
                    report["not_synchronized"].append(
                        self._item(product, "deleted_tombstone", [deleted["id"]])
                    )
                    continue
                match = self.card_sync._match(connection, product)
                if match["status"] == "ambiguous":
                    report["ambiguous"] += 1
                    report["not_synchronized"].append(
                        self._item(
                            product,
                            "ambiguous_{}".format(match["method"]),
                            [row["id"] for row in match["candidates"]],
                        )
                    )
                    continue
                existing = match.get("product")
                if existing is None:
                    report["unmatched"] += 1
                    report["not_synchronized"].append(
                        self._item(product, "erp_card_not_found")
                    )
                    continue
                if (
                    existing["brand_id"] in protected_brand_ids
                    or canonical_protected_brand(existing["excel_brand"])
                    or canonical_protected_brand(existing["bitrix_brand"])
                ):
                    protected_name = (
                        canonical_protected_brand(existing["excel_brand"])
                        or canonical_protected_brand(existing["bitrix_brand"])
                        or "protected_erp_brand_id"
                    )
                    report["protected"] += 1
                    report["protected_by_brand"].setdefault(protected_name, 0)
                    report["protected_by_brand"][protected_name] += 1
                    continue
                stock_before = float(existing["stock"] or 0)
                delta = quantity - stock_before
                direction = (
                    "increased" if delta > 0
                    else "decreased" if delta < 0
                    else "unchanged"
                )
                report[direction] += 1
                report["matched"] += 1
                if delta > 0:
                    report["increase_quantity"] += delta
                elif delta < 0:
                    report["decrease_quantity"] += abs(delta)
                result = self._item(
                    product,
                    direction,
                    [existing["id"]],
                    stock_before=stock_before,
                    bitrix_stock=quantity,
                    match_method=match["method"],
                )
                report["items"].append(result)
                if _text(product.get("brand")).casefold() == "braun":
                    report["braun"][direction] += 1
                    report["braun"]["matched"] += 1
                    if len(report["braun"]["examples"]) < 12:
                        report["braun"]["examples"].append(result)
                if apply and delta:
                    self._apply_adjustment(
                        connection,
                        existing,
                        product,
                        stock_before,
                        quantity,
                        delta,
                        run_id,
                        source_generated_at,
                    )
                    report["updated"] += 1
            protected_after = protected_state_digest(connection)
            report["protected_unchanged"] = protected_before == protected_after
            if not report["protected_unchanged"]:
                raise RuntimeError("Protected brand state changed")
            report["status"] = "success"
            return report

    @staticmethod
    def _apply_adjustment(connection, existing, product, stock_before,
                          stock_after, delta, run_id, source_generated_at):
        now = utc_now()
        product_id = int(existing["id"])
        external_id = _text(product.get("external_product_id"))
        connection.execute(
            "UPDATE catalog_excel_products SET stock = ?, "
            "stock_source = 'bitrix_catalog_quantity', updated_at = ? WHERE id = ?",
            (stock_after, now, product_id),
        )
        connection.execute(
            "INSERT INTO catalog_excel_manual_stock_operations ("
            "id, product_id, stock_before, stock_after, stock_difference, reason, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), product_id, stock_before, stock_after, delta,
                "Синхронизация остатка из {} (Bitrix ID {})".format(
                    SOURCE_FIELD, external_id
                ),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO catalog_stock_movements ("
            "id, product_id, movement_type, quantity_delta, stock_before, stock_after, "
            "idempotency_key, source_type, source_id, operation_kind, source, comment, "
            "created_at) VALUES (?, ?, 'manual_adjustment', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), product_id, delta, stock_before, stock_after,
                "{}:{}".format(run_id, product_id),
                "bitrix_catalog", external_id, "quantity_sync", SOURCE_FIELD,
                "Фактический остаток сайта; generated_at={}".format(
                    source_generated_at or "unknown"
                ),
                now,
            ),
        )
        AuditJournal().record(
            "product",
            product_id,
            "updated",
            existing["excel_name_raw"],
            object_secondary=existing["excel_article"] or "",
            before={"stock": stock_before},
            after={"stock": stock_after},
            metadata={
                "brand": existing["excel_brand"],
                "article": existing["excel_article"],
                "bitrix_product_id": external_id,
                "source_field": SOURCE_FIELD,
                "run_id": run_id,
            },
            actor_type="system",
            source="bitrix_catalog_quantity",
            connection=connection,
        )

    @staticmethod
    def _item(product, reason, candidate_ids=None, stock_before=None,
              bitrix_stock=None, match_method=""):
        item = {
            "external_product_id": _text(product.get("external_product_id")),
            "name": _text(product.get("name")),
            "brand": _text(product.get("brand")),
            "reason": reason,
            "candidate_ids": list(candidate_ids or []),
        }
        if stock_before is not None:
            item.update({
                "erp_stock": stock_before,
                "bitrix_stock": bitrix_stock,
                "delta": bitrix_stock - stock_before,
                "match_method": match_method,
            })
        return item

    @staticmethod
    def _report(run_id, apply, source_generated_at, protected_rows):
        from app.services.protected_catalog_brands import PROTECTED_BRANDS

        return {
            "status": "running",
            "mode": "apply" if apply else "dry_run",
            "run_id": run_id,
            "source": {
                "field": SOURCE_FIELD,
                "export_field": EXPORT_FIELD,
                "site_filter": SITE_AVAILABILITY_FILTER,
                "subtract_reserved_again": False,
                "store_id": None,
                "store_note": (
                    "The site reads the aggregate catalog quantity; store 1 amounts "
                    "are not used by the catalog availability filter."
                ),
                "generated_at": source_generated_at,
            },
            "matched": 0,
            "updated": 0,
            "increased": 0,
            "decreased": 0,
            "unchanged": 0,
            "increase_quantity": 0.0,
            "decrease_quantity": 0.0,
            "protected": 0,
            "protected_by_brand": {brand: 0 for brand in PROTECTED_BRANDS},
            "protected_erp_brands": protected_rows,
            "protected_unchanged": False,
            "ambiguous": 0,
            "unmatched": 0,
            "deleted": 0,
            "invalid_stock": 0,
            "source_duplicates": 0,
            "not_synchronized": [],
            "items": [],
            "braun": {
                "matched": 0,
                "increased": 0,
                "decreased": 0,
                "unchanged": 0,
                "examples": [],
            },
        }
