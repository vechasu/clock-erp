import base64
import tempfile
import unittest
from pathlib import Path

from app.catalog_db import CatalogDatabase
from app.services.brand_images import BrandImageValidationError
from app.services.excel_product_catalog import ExcelProductCatalog
from app.services.product_images import ProductImageImporter, ProductImageStore


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def source_product(identifier="501", article="SKU-1", brand="Casio", name="A100"):
    return {
        "id": identifier,
        "xml_id": "xml-{}".format(identifier),
        "article": article,
        "brand": brand,
        "name": name,
        "images": [{
            "id": "image-1", "url": "https://tictactoy.ru/upload/one.png",
            "filename": "one.png", "mime_type": "image/png",
        }],
    }


class ProductImageImporterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = CatalogDatabase(root / "catalog.db")
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO catalog_excel_batches ("
                "id, file_sha256, source_filename, row_count, total_stock, "
                "positive_rows, zero_rows, status, created_at, applied_at"
                ") VALUES ('images', 'images-sha', 'images.xlsx', 0, 0, 0, 0, "
                "'active', '2026-08-21T00:00:00+00:00', "
                "'2026-08-21T00:00:00+00:00')"
            )
        self.catalog = ExcelProductCatalog(self.database)
        self.product = self.catalog.create_product(
            "A100", article="SKU-1", brand="Casio", category="Часы", stock=7
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_external_product_id = ?, "
                "bitrix_xml_id = ?, bitrix_source_url = ? WHERE id = ?",
                ("501", "xml-501", "https://tictactoy.ru/catalog/a100/", self.product["id"]),
            )
        self.store = ProductImageStore(self.database, root / "product_images")
        self.importer = ProductImageImporter(self.database, self.store)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def downloader(_image):
        return PNG, "image/png", "one.png"

    def snapshot_business_data(self):
        with self.database.connect() as connection:
            return tuple(connection.execute(
                "SELECT stock, excel_name_raw, excel_article, excel_brand, "
                "excel_category, active FROM catalog_excel_products WHERE id = ?",
                (self.product["id"],),
            ).fetchone())

    def test_import_is_idempotent_and_preserves_catalog_and_stock(self):
        before = self.snapshot_business_data()
        first = self.importer.run(
            [source_product()], "bitrix", self.downloader, apply=True
        )
        second = self.importer.run(
            [source_product()], "bitrix", self.downloader, apply=True
        )
        after = self.snapshot_business_data()
        with self.database.connect() as connection:
            stored = dict(connection.execute(
                "SELECT local_image_path, local_image_source FROM "
                "catalog_excel_products WHERE id = ?", (self.product["id"],)
            ).fetchone())

        self.assertEqual(first["added"], 1)
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["existing"], 1)
        self.assertEqual(before, after)
        self.assertEqual(stored["local_image_source"], "bitrix")
        self.assertTrue((self.store.root / stored["local_image_path"]).is_file())
        self.assertEqual(len(list(self.store.root.iterdir())), 1)

    def test_bitrix_replaces_lower_priority_existing_erp_image(self):
        self.store.set_image(
            self.product["id"], PNG, "manual.png", "image/png", "manual"
        )
        report = self.importer.run(
            [source_product()], "bitrix", self.downloader, apply=True
        )
        self.assertEqual(report["existing"], 0)
        self.assertEqual(report["added"], 1)
        with self.database.connect() as connection:
            stored = connection.execute(
                "SELECT local_image_source FROM catalog_excel_products WHERE id = ?",
                (self.product["id"],),
            ).fetchone()
        self.assertEqual(stored["local_image_source"], "bitrix")

    def test_ambiguous_exact_name_is_reported_without_writes(self):
        self.catalog.create_product(
            "A100", article="SKU-2", brand="Casio", category="Часы", stock=1
        )
        record = source_product(identifier="", article="")
        record["xml_id"] = ""
        report = self.importer.run([record], "bitrix", apply=False)
        self.assertEqual(len(report["ambiguous"]), 1)
        self.assertEqual(report["writes_performed"], 0)

    def test_invalid_download_is_rejected_without_business_changes(self):
        before = self.snapshot_business_data()
        report = self.importer.run(
            [source_product()], "bitrix",
            lambda _image: (b"<html>error</html>", "image/png", "one.png"),
            apply=True,
        )
        self.assertEqual(report["added"], 0)
        self.assertEqual(report["errors"][0]["error"], BrandImageValidationError.__name__)
        self.assertEqual(before, self.snapshot_business_data())

    def test_site_source_cannot_replace_bitrix_image(self):
        self.importer.run([source_product()], "bitrix", self.downloader, apply=True)
        site_record = source_product(identifier="", article="SKU-1")
        site_record["source_url"] = "https://tictactoy.ru/catalog/a100/"
        report = self.importer.run(
            [site_record], "tictactoy", self.downloader, apply=True
        )
        self.assertEqual(report["existing"], 1)
        self.assertEqual(report["added"], 0)


if __name__ == "__main__":
    unittest.main()
