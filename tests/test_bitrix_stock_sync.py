import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.clients.bitrix_catalog import normalize_product
from app.services.bitrix_stock_sync import BitrixStockSync
from app.services.excel_product_catalog import ExcelProductBatchService
from scripts.sync_bitrix_stock import sync_bitrix_stock


def excel_result(row, name, brand, stock, article):
    return {
        "excel_row": row,
        "excel_name": name,
        "excel_name_raw": name,
        "excel_article": article,
        "excel_brand": brand,
        "category": "Часы",
        "stock": stock,
        "stock_valid": True,
        "cell": "A-1",
        "match_status": "not_found",
        "match_method": "none",
        "confidence": 0,
        "alternatives": [],
    }


def product(identity, name, brand, stock):
    return {
        "external_product_id": str(identity),
        "external_xml_id": "xml-" + str(identity),
        "external_sku": "SKU-" + str(identity),
        "name": name,
        "brand": brand,
        "stock": stock,
        "stock_source_field": "CCatalogProduct.QUANTITY",
        "properties": [],
    }


class FakeClient:
    def __init__(self, products):
        self.products = list(products)

    def get_products_page(self, page, limit, include_inactive=False):
        start = (page - 1) * limit
        rows = self.products[start:start + limit]
        return {
            "products": rows,
            "total": len(self.products),
            "has_more": start + limit < len(self.products),
            "generated_at": "2026-08-21T12:00:00+03:00",
        }


class BitrixStockSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        ExcelProductBatchService(self.database).apply(
            [
                excel_result(2, "Braun Alarm", "Braun", 0, "SKU-1"),
                excel_result(3, "Decrease", "Brand", 10, "SKU-2"),
                excel_result(4, "Same", "Brand", 4, "SKU-3"),
                excel_result(5, "Protected", "Ziiiro", 8, "SKU-4"),
                excel_result(6, "Ambiguous A", "Brand", 2, "SKU-5A"),
                excel_result(7, "Ambiguous B", "Brand", 3, "SKU-5B"),
            ],
            "b" * 64,
            "existing.xlsx",
        )
        with self.database.transaction() as connection:
            for row, external_id in ((2, "1"), (3, "2"), (4, "3"), (5, "4")):
                connection.execute(
                    "UPDATE catalog_excel_products SET bitrix_external_product_id = ? "
                    "WHERE excel_row = ?",
                    (external_id, row),
                )
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_external_product_id = '5' "
                "WHERE excel_row IN (6, 7)"
            )
        self.products = [
            product("1", "Braun Alarm", "Braun", 7),
            product("2", "Decrease", "Brand", 6),
            product("3", "Same", "Brand", 4),
            product("4", "Protected", "Ziiiro", 99),
            product("5", "Ambiguous", "Brand", 1),
            product("6", "Missing", "Brand", 2),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_available_quantity_is_catalog_quantity_source(self):
        normalized = normalize_product({
            "id": "1", "name": "Product", "available_quantity": "7",
        })
        self.assertEqual(normalized["stock"], 7)
        self.assertEqual(
            normalized["stock_source_field"], "CCatalogProduct.QUANTITY"
        )

    def test_dry_run_reports_directions_without_writes(self):
        before = self.path.read_bytes()
        report = BitrixStockSync(self.database).synchronize(self.products)
        after = self.path.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(
            (
                report["increased"], report["decreased"], report["unchanged"],
                report["protected"], report["ambiguous"], report["unmatched"],
            ),
            (1, 1, 1, 1, 1, 1),
        )
        self.assertEqual(report["braun"]["examples"][0]["bitrix_stock"], 7)
        self.assertTrue(report["protected_unchanged"])

    def test_apply_updates_stock_with_three_audit_layers_and_is_idempotent(self):
        first = BitrixStockSync(self.database).synchronize(
            self.products, apply=True, source_generated_at="source-time"
        )
        second = BitrixStockSync(self.database).synchronize(
            self.products, apply=True, source_generated_at="source-time"
        )
        self.assertEqual((first["updated"], second["updated"]), (2, 0))
        with self.database.connect() as connection:
            stocks = dict(connection.execute(
                "SELECT bitrix_external_product_id, stock FROM catalog_excel_products "
                "WHERE bitrix_external_product_id IN ('1','2','3','4')"
            ).fetchall())
            manual_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_manual_stock_operations "
                "WHERE reason LIKE 'Синхронизация остатка из %'"
            ).fetchone()[0]
            movement_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_stock_movements "
                "WHERE source_type = 'bitrix_catalog' AND operation_kind = 'quantity_sync'"
            ).fetchone()[0]
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM erp_audit_events "
                "WHERE entity_type = 'product' AND source_snapshot = 'bitrix_catalog_quantity'"
            ).fetchone()[0]
        self.assertEqual(stocks, {"1": 7, "2": 6, "3": 4, "4": 8})
        self.assertEqual((manual_count, movement_count, audit_count), (2, 2, 2))
        self.assertTrue(first["protected_unchanged"])

    def test_script_creates_verified_backup_before_dry_run(self):
        report = sync_bitrix_stock(
            FakeClient(self.products),
            self.database,
            backup=True,
            backup_root=Path(self.temp.name) / "backups",
            page_size=2,
        )
        self.assertTrue(Path(report["backup_path"]).exists())
        self.assertEqual(report["database"]["quick_check"], "ok")
        self.assertEqual(report["writes_performed"], 0)


if __name__ == "__main__":
    unittest.main()
