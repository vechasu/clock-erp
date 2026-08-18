import base64
import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.brand_images import (
    BitrixBrandImageImporter,
    BrandImageStore,
    BrandImageValidationError,
    MAX_BRAND_IMAGE_BYTES,
    validate_image,
)
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.shared_catalog import SharedCatalog


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PNG_2 = PNG + b"different-revision"


def bitrix_brand(identifier, name, image=True, revision="1"):
    return {
        "id": str(identifier),
        "name": name,
        "images": ([{
            "id": "file-{}".format(revision),
            "updated_at": "2026-08-18T00:00:0{}+00:00".format(revision),
            "filename": "{}.png".format(name),
            "mime_type": "image/png",
            "url": "https://www.tictactoy.ru/upload/{}.png".format(identifier),
        }] if image else []),
    }


class BrandImageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = CatalogDatabase(root / "catalog.db")
        self.database.initialize()
        self.store = BrandImageStore(self.database, root / "brand_images")
        self.catalog = SharedCatalog(self.database)
        self.importer = BitrixBrandImageImporter(self.database, self.store)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def downloader(image):
        content = PNG_2 if image["id"] == "file-2" else PNG
        return content, "image/png", image["filename"]

    def test_bitrix_image_import_and_repeat_are_idempotent(self):
        brand = self.catalog.create_brand("Casio")
        record = bitrix_brand(101, "Casio")

        first = self.importer.run([record], self.downloader, apply=True)
        second = self.importer.run([record], self.downloader, apply=True)
        overview = self.catalog.get_brand_overview(brand["id"])

        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["current"], 1)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(len(list(self.store.root.iterdir())), 1)
        self.assertEqual(overview["image_source"], "bitrix")
        self.assertEqual(overview["bitrix_brand_id"], "101")

    def test_without_image_is_skipped_and_unmatched_brand_is_not_created(self):
        self.catalog.create_brand("Casio")
        report = self.importer.run([
            bitrix_brand(101, "Casio", image=False),
            bitrix_brand(102, "Unknown"),
        ])
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM erp_brands").fetchone()[0]
        self.assertEqual(report["with_images"], 1)
        self.assertEqual(report["skipped"], 2)
        self.assertEqual(report["unmatched"][0]["name"], "Unknown")
        self.assertEqual(count, 1)

    def test_ambiguous_alias_does_not_assign_image(self):
        now = "2026-08-18T00:00:00+00:00"
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO erp_brands (name, normalized_name, active, created_at, updated_at) "
                "VALUES ('A.B. Art', 'a.b. art', 1, ?, ?)", (now, now)
            )
            connection.execute(
                "INSERT INTO erp_brands (name, normalized_name, active, created_at, updated_at) "
                "VALUES ('A B ART', 'a b art', 1, ?, ?)", (now, now)
            )
        report = self.importer.run([bitrix_brand(201, "A-B-Art")])
        self.assertEqual(len(report["ambiguous"]), 1)
        self.assertEqual(report["to_import"], 0)

    def test_manual_image_has_priority_and_manual_replace_delete_work(self):
        brand = self.catalog.create_brand("Casio")
        first_path, _ = self.store.set_image(
            brand["id"], PNG, "manual.png", "image/png", "manual"
        )
        report = self.importer.run(
            [bitrix_brand(101, "Casio")], self.downloader, apply=True
        )
        second_path, _ = self.store.set_image(
            brand["id"], PNG_2, "replacement.png", "image/png", "manual"
        )
        self.assertEqual(report["imported"], 0)
        self.assertEqual(report["skipped"], 1)
        self.assertNotEqual(first_path, second_path)
        self.assertFalse((self.store.root / first_path).exists())
        self.store.remove_image(brand["id"])
        self.assertIsNone(self.catalog.get_brand_overview(brand["id"])["image_path"])
        self.assertFalse((self.store.root / second_path).exists())

    def test_changed_bitrix_image_replaces_previous_import(self):
        brand = self.catalog.create_brand("Casio")
        self.importer.run([bitrix_brand(101, "Casio")], self.downloader, apply=True)
        old_path = self.catalog.get_brand_overview(brand["id"])["image_path"]
        result = self.importer.run(
            [bitrix_brand(101, "Casio", revision="2")],
            self.downloader,
            apply=True,
        )
        new_path = self.catalog.get_brand_overview(brand["id"])["image_path"]
        self.assertEqual(result["imported"], 1)
        self.assertNotEqual(old_path, new_path)
        self.assertFalse((self.store.root / old_path).exists())

    def test_invalid_and_oversized_files_are_rejected(self):
        with self.assertRaises(BrandImageValidationError):
            validate_image(b"<?php echo 1;", "logo.png", "image/png")
        with self.assertRaises(BrandImageValidationError):
            validate_image(
                b"x" * (MAX_BRAND_IMAGE_BYTES + 1),
                "logo.png",
                "image/png",
            )

    def test_catalog_data_and_relationships_are_unchanged(self):
        products = ExcelProductCatalog(self.database)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches (id, file_sha256, source_filename, "
                "row_count, total_stock, positive_rows, zero_rows, status, created_at, applied_at) "
                "VALUES ('brand-image', 'sha', 'x.xlsx', 0, 0, 0, 0, 'active', 'x', 'x')"
            )
        product = products.create_product(
            name="Watch", article="W1", brand="Casio", category="Watch", stock=7
        )
        before = products.get_product(product["id"])
        self.importer.run([bitrix_brand(101, "Casio")], self.downloader, apply=True)
        after = products.get_product(product["id"])
        self.assertEqual(after["stock"], before["stock"])
        self.assertEqual(after["brand_id"], before["brand_id"])
        self.assertEqual(after["category_id"], before["category_id"])

    def test_template_has_list_detail_fallback_preview_and_secure_routes(self):
        project = Path(__file__).resolve().parents[1]
        template = (project / "app/templates/warehouse_brands.html").read_text("utf-8")
        web = (project / "app/web.py").read_text("utf-8")
        self.assertIn('object-fit:contain', template)
        self.assertIn('data-brand-image', template)
        self.assertIn('data-brand-fallback', template)
        self.assertIn('bindImageFallback', template)
        self.assertIn('brand-hero-image', template)
        self.assertIn('brandImagePreview', template)
        self.assertIn('enctype="multipart/form-data"', template)
        self.assertIn('require_csrf_when_authenticated()', web)
        self.assertIn('if not _product_force_delete_allowed():', web)


if __name__ == "__main__":
    unittest.main()
