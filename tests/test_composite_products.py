import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.composite_products import CompositeProducts
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.sales_inventory import InsufficientStockError, SalesInventory
from app.schema_migrations import apply_migrations


class CompositeProductsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temporary.name) / "catalog.db")
        apply_migrations(self.database.path, app_commit="composite-test")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches(id,file_sha256,source_filename,row_count,total_stock,positive_rows,zero_rows,status,created_at,applied_at) "
                "VALUES('batch','sha','test.xlsx',0,0,0,0,'active','2026-09-03T00:00:00+00:00','2026-09-03T00:00:00+00:00')"
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.composites = CompositeProducts(self.database)
        self.inventory = SalesInventory(self.database)
        self.counter = 0

    def tearDown(self):
        CatalogDatabase._schema_cache.clear()
        self.temporary.cleanup()

    def product(self, name, stock):
        self.counter += 1
        return self.catalog.create_product(
            name=name, article="CP-{}".format(self.counter), brand="Test",
            category="Ремешки" if "Strap" in name else "Часы", stock=stock,
        )

    def setup_clock(self, head_stock=1, strap_stock=1, suffix="Milanese"):
        storefront = self.product("Clock 07 " + suffix, 99)
        head = self.product("Clock 07 Head", head_stock)
        strap = self.product(suffix + " Strap", strap_stock)
        mapping = self.composites.save(storefront["id"], [
            {"type": "head", "product_id": head["id"], "quantity": 1},
            {"type": "strap", "product_id": strap["id"], "quantity": 1},
        ])
        return storefront, head, strap, mapping

    def sale(self, storefront, sale_id="sale-1", quantity=1, **kwargs):
        payload = {"id": sale_id, "source": "tictactoy", "order_number": sale_id,
                   "product_name": storefront["display_name"], "created_at": "2026-09-03"}
        return self.inventory.create_sale(
            payload, storefront["id"], quantity, 1000,
            idempotency_key=sale_id, **kwargs
        )

    def stock(self, product):
        return self.catalog.get_product(product["id"])["stock"]

    def test_01_sale_consumes_head_and_strap_only(self):
        storefront, head, strap, _mapping = self.setup_clock()
        self.sale(storefront)
        self.assertEqual((self.stock(head), self.stock(strap)), (0, 0))
        self.assertEqual(self.stock(storefront), 99)

    def test_02_zero_head_makes_composite_unavailable(self):
        storefront, _head, _strap, _mapping = self.setup_clock(head_stock=0, strap_stock=10)
        self.assertEqual(self.composites.resolve(storefront["id"])["available_quantity"], 0)

    def test_03_zero_strap_makes_composite_unavailable(self):
        storefront, _head, _strap, _mapping = self.setup_clock(head_stock=10, strap_stock=0)
        self.assertEqual(self.composites.resolve(storefront["id"])["available_quantity"], 0)

    def test_04_shared_head_limits_all_variants(self):
        first, head, first_strap, _mapping = self.setup_clock()
        variants = []
        for name in ("Black", "Brown", "Mesh", "Nylon", "NATO", "Silver"):
            storefront = self.product("Clock 07 " + name, 50)
            strap = self.product(name + " Strap", 1)
            self.composites.save(storefront["id"], [
                {"type": "head", "product_id": head["id"], "quantity": 1},
                {"type": "strap", "product_id": strap["id"], "quantity": 1},
            ])
            variants.append((storefront, strap))
        self.sale(first)
        self.assertEqual(self.stock(first_strap), 0)
        self.assertTrue(all(self.composites.resolve(item[0]["id"])["available_quantity"] == 0 for item in variants))
        self.assertTrue(all(self.stock(item[1]) == 1 for item in variants))

    def test_05_cancellation_restores_components(self):
        storefront, head, strap, _mapping = self.setup_clock()
        sale = self.sale(storefront)
        self.inventory.cancel_sale(sale["id"], reason="test")
        self.assertEqual((self.stock(head), self.stock(strap)), (1, 1))

    def test_06_regular_product_is_unchanged(self):
        product = self.product("Casio A168", 2)
        self.sale(product)
        self.assertEqual(self.stock(product), 1)

    def test_07_quantity_two_rejected_by_head(self):
        storefront, head, strap, _mapping = self.setup_clock(1, 10)
        with self.assertRaises(InsufficientStockError):
            self.sale(storefront, quantity=2)
        self.assertEqual((self.stock(head), self.stock(strap)), (1, 10))

    def test_08_quantity_two_rejected_by_strap(self):
        storefront, head, strap, _mapping = self.setup_clock(2, 1)
        with self.assertRaises(InsufficientStockError):
            self.sale(storefront, quantity=2)
        self.assertEqual((self.stock(head), self.stock(strap)), (2, 1))

    def test_09_concurrent_sales_cannot_share_one_head(self):
        first, head, _strap, _mapping = self.setup_clock()
        second = self.product("Clock 07 Black", 99)
        second_strap = self.product("Black Strap", 1)
        self.composites.save(second["id"], [
            {"type": "head", "product_id": head["id"], "quantity": 1},
            {"type": "strap", "product_id": second_strap["id"], "quantity": 1},
        ])
        def conduct(args):
            product, sale_id = args
            try:
                SalesInventory(CatalogDatabase(self.database.path)).create_sale(
                    {"id": sale_id, "source": "tictactoy", "order_number": sale_id},
                    product["id"], 1, 1, idempotency_key=sale_id,
                )
                return True
            except InsufficientStockError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(conduct, [(first, "a"), (second, "b")]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.stock(head), 0)

    def test_10_order_snapshot_survives_mapping_edit(self):
        storefront, head, strap, mapping = self.setup_clock()
        snapshot = self.composites.snapshot_order_item("10045", "line-1", storefront["id"], 1)
        other = self.product("Other Strap", 1)
        self.composites.save(storefront["id"], [
            {"type": "head", "product_id": head["id"], "quantity": 1},
            {"type": "strap", "product_id": other["id"], "quantity": 1},
        ], mapping_id=mapping["id"])
        self.assertEqual(self.composites.order_item_snapshot("10045", "line-1")[1]["component_product_id"], strap["id"])
        self.assertEqual(snapshot[1]["component_product_id"], strap["id"])
        self.inventory.create_sale_batch(
            {"id": "order-snapshot-sale", "source": "tictactoy",
             "external_order_id": "10045"},
            [{"product_id": storefront["id"], "quantity": 1, "unit_price": 1,
              "bitrix_order_line_id": "line-1"}],
        )
        self.assertEqual(self.stock(strap), 0)
        self.assertEqual(self.stock(other), 1)

    def test_11_sale_snapshot_survives_mapping_edit(self):
        storefront, head, strap, mapping = self.setup_clock()
        sale = self.sale(storefront)
        other = self.product("Other Strap", 1)
        self.composites.save(storefront["id"], [
            {"type": "head", "product_id": head["id"], "quantity": 1},
            {"type": "strap", "product_id": other["id"], "quantity": 1},
        ], mapping_id=mapping["id"])
        self.assertEqual(sale["items"][0]["components"][1]["product_id"], str(strap["id"]))

    def test_12_repeated_sale_is_idempotent(self):
        storefront, head, strap, _mapping = self.setup_clock()
        self.sale(storefront)
        self.sale(storefront)
        self.assertEqual((self.stock(head), self.stock(strap)), (0, 0))

    def test_13_second_component_failure_rolls_back_everything(self):
        storefront, head, strap, _mapping = self.setup_clock()
        def fail(_connection):
            raise RuntimeError("after movements")
        with self.assertRaises(RuntimeError):
            self.sale(storefront, failure_hook=fail)
        self.assertEqual((self.stock(head), self.stock(strap)), (1, 1))
        self.assertIsNone(self.inventory.get_sale("sale-1"))

    def test_14_regular_and_composite_batch_are_atomic(self):
        storefront, head, strap, _mapping = self.setup_clock()
        regular = self.product("Casio A168", 1)
        self.inventory.create_sale_batch(
            {"id": "mixed", "source": "tictactoy"},
            [{"product_id": storefront["id"], "quantity": 1, "unit_price": 1},
             {"product_id": regular["id"], "quantity": 1, "unit_price": 1}],
        )
        self.assertEqual((self.stock(head), self.stock(strap), self.stock(regular)), (0, 0, 0))

    def test_15_partial_return_restores_component_ratio(self):
        storefront, head, strap, _mapping = self.setup_clock(2, 2)
        sale = self.sale(storefront, quantity=2)
        self.inventory.return_sale(sale["id"], 1, idempotency_key="return-1")
        self.assertEqual((self.stock(head), self.stock(strap)), (1, 1))


if __name__ == "__main__":
    unittest.main()
