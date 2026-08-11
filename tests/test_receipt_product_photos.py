import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import web
from app.catalog_db import CatalogDatabase
from app.services.excel_product_catalog import (
    ExcelProductBatchService,
    ExcelProductCatalog,
)
from app.services.shared_catalog import SharedCatalog


def receipt(index, product_id=""):
    return {
        "id": "receipt-{}".format(index),
        "number": "REC-{:03d}".format(index),
        "created_at": "2026-08-{:02d}T10:00:00".format((index % 28) + 1),
        "receipt_date": "2026-08-{:02d}".format((index % 28) + 1),
        "brand": "Бренд",
        "category": "Категория",
        "product_id": str(product_id),
        "product_name": "Товар {:03d}".format(index),
        "note": "Комментарий",
        "status": "posted",
        "status_label": "Проведён",
        "total_quantity": 1,
        "positions": [],
    }


class ReceiptProductPhotosTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = CatalogDatabase(Path(self.temp.name) / "catalog.db")
        ExcelProductBatchService(self.database).apply(
            [{
                "excel_row": 2,
                "excel_name": "Служебная карточка",
                "excel_brand": "Служебный бренд",
                "excel_article": "SEED",
                "article_quality": "code_like",
                "category": "Служебная категория",
                "stock": 0.0,
                "stock_valid": True,
                "cell": "A-1",
                "product_id": None,
                "match_status": "not_found",
                "match_method": "test",
                "confidence": 0,
                "alternatives": [],
            }],
            "d" * 64,
            "receipt-photos.xlsx",
        )
        self.catalog = ExcelProductCatalog(self.database)
        self.shared = SharedCatalog(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def create_product(self, name="Товар", active=True, **images):
        product = self.catalog.create_product(
            name=name,
            article=name,
            brand="Бренд",
            category="Категория",
            stock=1,
        )
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET active = ?, "
                "bitrix_thumbnail_url = ?, bitrix_primary_image_url = ?, "
                "moysklad_product_id = ? WHERE id = ?",
                (
                    int(active),
                    images.get("thumbnail"),
                    images.get("primary"),
                    images.get("moysklad_id"),
                    int(product["id"]),
                ),
            )
        return str(product["id"])

    def test_batch_resolution_uses_current_canonical_photo_and_placeholders(self):
        active_id = self.create_product(
            "С фото",
            thumbnail="https://cdn.test/current-thumb.jpg",
            primary="https://cdn.test/primary.jpg",
        )
        no_photo_id = self.create_product("Без фото")
        fallback_id = self.create_product(
            "МойСклад",
            moysklad_id="ms-product-1",
        )
        deleted_id = self.create_product(
            "Удалённый",
            active=False,
            thumbnail="https://cdn.test/deleted.jpg",
        )
        rows = [
            receipt(1, active_id),
            receipt(2, no_photo_id),
            receipt(3, fallback_id),
            receipt(4, deleted_id),
            receipt(5),
        ]

        web.attach_receipt_product_thumbnails(rows, self.shared)

        self.assertEqual(
            rows[0]["product_thumbnail_url"],
            "https://cdn.test/current-thumb.jpg",
        )
        self.assertEqual(rows[1]["product_thumbnail_url"], "")
        self.assertEqual(
            rows[2]["product_thumbnail_url"],
            "/warehouse/product/ms-product-1/thumbnail",
        )
        self.assertEqual(rows[3]["product_thumbnail_url"], "")
        self.assertEqual(rows[4]["product_thumbnail_url"], "")

    def test_photo_is_read_fresh_and_never_copied_from_receipt(self):
        product_id = self.create_product(
            thumbnail="https://cdn.test/old.jpg",
        )
        row = receipt(1, product_id)
        row["product_image_url"] = "https://stale.test/receipt-copy.jpg"
        web.attach_receipt_product_thumbnails([row], self.shared)
        self.assertEqual(
            row["product_thumbnail_url"], "https://cdn.test/old.jpg"
        )

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE catalog_excel_products SET bitrix_thumbnail_url = ? "
                "WHERE id = ?",
                ("https://cdn.test/new.jpg", int(product_id)),
            )
        web.attach_receipt_product_thumbnails([row], self.shared)
        self.assertEqual(
            row["product_thumbnail_url"], "https://cdn.test/new.jpg"
        )

    def test_unique_ids_are_resolved_once_without_external_calls(self):
        rows = [receipt(index, "17") for index in range(1, 101)]
        catalog = mock.Mock()
        catalog.products_by_ids.return_value = {
            "17": {"image_url": "https://cdn.test/17.jpg"}
        }

        web.attach_receipt_product_thumbnails(rows, catalog)

        catalog.products_by_ids.assert_called_once_with(
            {"17"}, include_archived=False
        )
        self.assertTrue(all(
            row["product_thumbnail_url"] == "https://cdn.test/17.jpg"
            for row in rows
        ))

    def test_route_resolves_only_current_page_after_filters_and_sort(self):
        records = [receipt(index, index) for index in range(1, 121)]
        resolved = []

        def capture(rows, shared_catalog=None):
            resolved.extend(row["product_id"] for row in rows)
            for row in rows:
                row["product_thumbnail_url"] = ""
            return rows

        with web.app.test_request_context(
            "/app/receipts?page=2&per_page=25&sort=document&sort_dir=asc"
        ), mock.patch.object(
            web, "api_receipt_records", return_value=records
        ), mock.patch.object(
            web, "attach_receipt_product_thumbnails", side_effect=capture
        ) as resolver, mock.patch.object(
            web, "render_template", side_effect=lambda name, **context: context
        ):
            context = web.receipts_page()

        resolver.assert_called_once()
        self.assertEqual(len(resolved), 25)
        self.assertEqual(resolved[0], "26")
        self.assertEqual(resolved[-1], "50")
        self.assertEqual(context["pagination"]["total"], 120)

    def test_template_has_dedicated_lazy_photo_column_and_broken_fallback(self):
        source = (web.PROJECT_ROOT / "app/templates/receipts.html").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            source.index('<col data-column-key="photo">'),
            source.index('<col data-column-key="product">'),
        )
        self.assertIn('class="col-photo" data-column-key="photo"', source)
        self.assertIn('loading="lazy"', source)
        self.assertIn('decoding="async"', source)
        self.assertIn("this.nextElementSibling.hidden=false", source)
        self.assertIn("receipt-product-photo-placeholder", source)
        self.assertIn("@media (max-width: 900px)", source)
        components = (
            web.PROJECT_ROOT / "app/static/css/erp-components.css"
        ).read_text(encoding="utf-8")
        self.assertIn(
            ".receipts-table .receipt-row > .col-photo", components
        )
        self.assertIn("position: absolute !important", components)
        self.assertIn("padding-left: 54px !important", components)

    def test_page_render_has_one_image_or_placeholder_per_row(self):
        rows = [receipt(1, "1"), receipt(2, "2")]
        rows[0]["product_thumbnail_url"] = "https://cdn.test/broken.jpg"
        rows[1]["product_thumbnail_url"] = ""
        started = time.perf_counter()
        with mock.patch.object(
            web, "api_receipt_records", return_value=rows
        ), mock.patch.object(
            web,
            "attach_receipt_product_thumbnails",
            side_effect=lambda page_rows: page_rows,
        ), mock.patch.dict(
            web.app.config,
            {"TESTING": True, "AUTH_TESTING": False},
        ):
            response = web.app.test_client().get("/app/receipts")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertEqual(html.count('class="receipt-product-photo"'), 2)
        self.assertEqual(html.count('class="receipt-product-thumb"'), 1)
        self.assertLess(elapsed_ms, 1000)


if __name__ == "__main__":
    unittest.main()
