import binascii
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.product_images import ProductImageStore
from app.schema_migrations import apply_migrations
from scripts.import_bitrix_product import (
    AmbiguousBitrixProductError,
    find_exact_product,
    import_single_product,
)


def png(red):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)
        )

    raw = bytes((0, red, 0, 0, 255))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def product(identity="204699", code="nato-97", name="Nato 97", images=2):
    gallery = [{
        "id": str(44610 + index),
        "kind": "preview" if index == 0 else "gallery",
        "original_url": "https://www.tictactoy.ru/upload/nato-{}.png".format(index),
        "filename": "nato-{}.png".format(index),
        "mime_type": "image/png",
        "order": index,
        "is_primary": index == 0,
    } for index in range(images)]
    category = {
        "id": "17", "name": "РЕМЕШКИ", "code": "straps", "path": ["РЕМЕШКИ"],
        "path_items": [{"id": "17", "name": "РЕМЕШКИ"}],
    }
    return {
        "external_source": "bitrix",
        "external_product_id": identity,
        "external_xml_id": "xml-" + identity,
        "external_sku": "NATO97",
        "code": code,
        "url": "https://www.tictactoy.ru/catalog/" + code + "/",
        "name": name,
        "preview_text": "Ремешок",
        "detail_text": "Описание",
        "preview_text_type": "text",
        "detail_text_type": "text",
        "active": True,
        "created_at": None,
        "updated_at": None,
        "brand": "",
        "category": category,
        "categories": [category],
        "properties": [{
            "id": "90", "code": "COLOR", "name": "Цвет", "type": "string",
            "value": "Черный", "display_value": "Черный", "multiple": False,
        }],
        "images": gallery,
        "prices": [{
            "type_id": "1", "type_code": "BASE", "type_name": "Розничная",
            "role": "base", "value": 990.0, "value_text": "990",
            "currency": "RUB", "is_purchase": False,
        }],
        "offers": [],
        "sale_price": {"value": 990.0, "value_text": "990", "currency": "RUB"},
        "stock": 4.0,
        "stock_source_field": "CCatalogProduct.QUANTITY",
    }


class FakeClient:
    def __init__(self, products, failing_ids=None, invalid_ids=None):
        self.products = list(products)
        self.failing_ids = set(failing_ids or [])
        self.invalid_ids = set(invalid_ids or [])
        self.downloads = 0

    def get_products_page(self, page, limit, include_inactive=False):
        rows = self.products if page == 1 else []
        return {"products": rows, "total": len(self.products), "has_more": False}

    def download_brand_image(self, image, max_bytes=None):
        self.downloads += 1
        identity = str(image.get("id"))
        if identity in self.failing_ids:
            raise OSError("fixture download failure")
        content = b"not-an-image" if identity in self.invalid_ids else png(int(identity) % 255)
        return content, "image/png", image.get("filename") or "image.png"


class SingleBitrixProductImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = CatalogDatabase(root / "catalog.db")
        apply_migrations(self.database.path, app_commit="test-import-bitrix-product")
        self.store = ProductImageStore(self.database, root / "product_images")
        self.backups = root / "backups"

    def tearDown(self):
        self.temp.cleanup()

    def run_import(self, client, apply=True):
        return import_single_product(
            client, self.database, apply=apply,
            backup_root=self.backups, image_store=self.store,
        )

    def test_new_product_and_repeat_are_idempotent(self):
        client = FakeClient([product()])
        first = self.run_import(client)
        first_downloads = client.downloads
        second = self.run_import(client)
        with self.database.connect() as connection:
            row = dict(connection.execute(
                "SELECT * FROM catalog_excel_products WHERE normalized_name = 'nato 97'"
            ).fetchone())
            count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products WHERE normalized_name = 'nato 97'"
            ).fetchone()[0]
        gallery = json.loads(row["bitrix_gallery_json"])
        self.assertEqual((first["status"], second["status"]), ("success", "success"))
        self.assertEqual(count, 1)
        self.assertEqual(len(gallery), 2)
        self.assertEqual(len(list(self.store.root.iterdir())), 2)
        self.assertEqual(client.downloads, first_downloads)
        self.assertEqual(second["writes_performed"], 0)
        self.assertFalse(second["images"]["database_updated"])
        self.assertEqual([item["external_file_id"] for item in gallery], ["44610", "44611"])
        self.assertTrue(gallery[0]["is_primary"])

    def test_existing_product_without_photos_is_updated_not_duplicated(self):
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id, file_sha256, source_filename, "
                "row_count, total_stock, positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('manual', 'sha', 'manual.xlsx', 0, 0, 0, 0, 'active', 'now', 'now')"
            )
        existing = ExcelProductCatalog(self.database).create_product(
            "Nato 97", brand="Nato", category="РЕМЕШКИ", stock=7
        )
        report = self.run_import(FakeClient([product()]))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM catalog_excel_products WHERE normalized_name = 'nato 97'"
            ).fetchall()
        self.assertEqual(report["status"], "success")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], existing["id"])
        self.assertEqual(rows[0]["stock"], 7)
        self.assertEqual(len(json.loads(rows[0]["bitrix_gallery_json"])), 2)

    def test_multiple_exact_bitrix_candidates_fail_closed(self):
        client = FakeClient([product("1"), product("2")])
        with self.assertRaises(AmbiguousBitrixProductError) as raised:
            find_exact_product(client, "nato-97", "Nato 97")
        self.assertEqual([item["id"] for item in raised.exception.candidates], ["1", "2"])
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM catalog_excel_products"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_one_download_failure_keeps_card_and_other_image(self):
        report = self.run_import(FakeClient([product()], failing_ids={"44611"}))
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT bitrix_external_product_id, bitrix_gallery_json "
                "FROM catalog_excel_products WHERE normalized_name = 'nato 97'"
            ).fetchone()
        self.assertEqual(report["status"], "completed_with_errors")
        self.assertEqual(row["bitrix_external_product_id"], "204699")
        self.assertEqual(len(json.loads(row["bitrix_gallery_json"])), 1)
        self.assertEqual(len(list(self.store.root.iterdir())), 1)

    def test_invalid_image_is_rejected_by_existing_photo_layer(self):
        report = self.run_import(FakeClient([product(images=1)], invalid_ids={"44610"}))
        self.assertEqual(report["status"], "completed_with_errors")
        self.assertEqual(report["images"]["stored"], 0)
        self.assertEqual(list(self.store.root.glob("*")), [])

    def test_unrelated_product_is_unchanged(self):
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id, file_sha256, source_filename, "
                "row_count, total_stock, positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('manual', 'sha', 'manual.xlsx', 0, 0, 0, 0, 'active', 'now', 'now')"
            )
        unrelated = ExcelProductCatalog(self.database).create_product(
            "Other", article="OTHER", brand="Other brand", category="Часы", stock=2
        )
        with self.database.connect() as connection:
            before = tuple(connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ?", (unrelated["id"],)
            ).fetchone())
        report = self.run_import(FakeClient([product()]))
        with self.database.connect() as connection:
            after = tuple(connection.execute(
                "SELECT * FROM catalog_excel_products WHERE id = ?", (unrelated["id"],)
            ).fetchone())
        self.assertEqual(report["status"], "success")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
