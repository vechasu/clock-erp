import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook
from werkzeug.datastructures import FileStorage

from app import web


PRODUCT_ID = "11111111-1111-1111-1111-111111111111"


def warehouse_item(has_images=False):
    return {
        "id": PRODUCT_ID,
        "name": "Часы Test",
        "article": "A-1",
        "code": "C-1",
        "brand": "Brand",
        "category": "Коллекция",
        "cell": "A-01",
        "stock": 4,
        "stock_display": "4",
        "reserve": 0,
        "quantity": 4,
        "created_at": 1,
        "created_at_display": "01.01.2026",
        "has_images": has_images,
        "thumbnail_url": (
            f"/warehouse/product/{PRODUCT_ID}/thumbnail"
            if has_images
            else ""
        ),
        "cell_source": "product",
        "cell_source_label": "у позиции",
        "cell_source_path": "",
        "moysklad_url": "#",
        "raw_category": "Brand/Коллекция",
    }


def report_sale(**changes):
    sale = {
        "id": "sale-1",
        "sale_type": "manual",
        "sale_type_label": "Ручная",
        "is_manual": True,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "source": "Tictactoy",
        "order_number": "ORDER-100",
        "product_id": PRODUCT_ID,
        "product_name": "Часы Test",
        "brand": "Brand",
        "category": "Коллекция",
        "quantity_value": 2,
        "unit_price": 1000.0,
        "unit_price_display": "1 000 ₽",
        "total_amount": 2000.0,
        "total_amount_display": "2 000 ₽",
        "track_number": "TRACK-100",
        "delivery_method": "СДЭК",
        "region": "Москва",
        "city": "Москва",
        "note": "Тест",
        "recipient": "ПВЗ",
        "recipient_name": "Иванов Иван",
        "payment_method": "Robokassa",
        "commission_amount": 100.0,
        "commission_display": "100 ₽",
        "commission_type": "fixed_rub",
        "order_status": "completed",
        "order_status_label": "Завершён",
        "is_cancelled": False,
        "cancelled_at": "",
        "sticker_number": "",
    }
    sale.update(changes)
    return sale


class FakeReceiptMoySkladClient:
    def __init__(self, existing_has_images=False):
        self.existing_has_images = existing_has_images
        self.uploads = []
        self.created_products = []
        self.stock_enters = []

    def product_has_images(self, product_id):
        return self.existing_has_images

    def upload_product_image(self, product_id, filename, content):
        self.uploads.append((product_id, filename, content))
        return {"id": product_id}

    def get_or_create_product_folder(self, path):
        return {
            "id": "folder-1",
            "meta": {"href": "https://example.test/folder-1"},
        }

    def create_product(self, **payload):
        self.created_products.append(payload)
        return {
            "id": PRODUCT_ID,
            "name": payload["name"],
            "code": payload["code"],
        }

    def create_stock_enter_many(self, positions, reason, moment):
        self.stock_enters.append((positions, reason, moment))
        return {
            "id": "enter-1",
            "name": "Оприходование 1",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }


class OwnerFeedbackTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_directory.name)

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_product_image_validation_accepts_jpeg_and_png(self):
        jpeg = FileStorage(
            stream=BytesIO(b"\xff\xd8\xffcontent"),
            filename="watch.jpeg",
            content_type="image/jpeg",
        )
        png = FileStorage(
            stream=BytesIO(b"\x89PNG\r\n\x1a\ncontent"),
            filename="watch.png",
            content_type="image/png",
        )

        self.assertEqual(
            web.read_product_image_upload(jpeg)["filename"],
            "watch.jpg",
        )
        self.assertEqual(
            web.read_product_image_upload(png)["filename"],
            "watch.png",
        )

    def test_product_image_validation_rejects_spoof_and_oversize(self):
        spoof = FileStorage(
            stream=BytesIO(b"not-an-image"),
            filename="watch.jpg",
            content_type="image/jpeg",
        )
        oversized = FileStorage(
            stream=BytesIO(
                b"\x89PNG\r\n\x1a\n"
                + b"x" * web.PRODUCT_IMAGE_MAX_BYTES
            ),
            filename="watch.png",
            content_type="image/png",
        )

        with self.assertRaisesRegex(ValueError, "JPEG и PNG"):
            web.read_product_image_upload(spoof)

        with self.assertRaisesRegex(ValueError, "3 МБ"):
            web.read_product_image_upload(oversized)

    def test_receipt_keeps_brand_category_and_skips_duplicate_image(self):
        item = warehouse_item(has_images=True)
        fake_client = FakeReceiptMoySkladClient(
            existing_has_images=True
        )
        saved_receipts = []

        with mock.patch.object(
            web, "get_warehouse_items", return_value=[item]
        ), mock.patch.object(
            web, "MoySkladClient", return_value=fake_client
        ), mock.patch.object(
            web, "load_receipts", return_value=[]
        ), mock.patch.object(
            web,
            "save_receipts",
            side_effect=lambda receipts: saved_receipts.extend(receipts),
        ), mock.patch.object(web, "add_stock_operation"):
            response = self.client.post(
                "/receipts/create",
                data={
                    "receipt_date": "2026-07-22",
                    "brand": "Brand",
                    "category": "Коллекция",
                    "product_id": PRODUCT_ID,
                    "quantity": "2",
                    "purchase_price": "500",
                    "product_image": (
                        BytesIO(b"\x89PNG\r\n\x1a\ncontent"),
                        "watch.png",
                    ),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(fake_client.uploads, [])
        self.assertEqual(saved_receipts[0]["brand"], "Brand")
        self.assertEqual(saved_receipts[0]["category"], "Коллекция")
        self.assertEqual(
            saved_receipts[0]["positions"][0]["category"],
            "Коллекция",
        )

    def test_new_product_receipt_passes_validated_image_to_creation(self):
        fake_client = FakeReceiptMoySkladClient()

        with mock.patch.object(
            web, "get_warehouse_items", return_value=[]
        ), mock.patch.object(
            web, "MoySkladClient", return_value=fake_client
        ), mock.patch.object(
            web, "load_receipts", return_value=[]
        ), mock.patch.object(web, "save_receipts"), mock.patch.object(
            web, "add_stock_operation"
        ), mock.patch.object(web, "record_warehouse_created_at"):
            response = self.client.post(
                "/receipts/create",
                data={
                    "receipt_date": "2026-07-22",
                    "brand": "Brand",
                    "category": "Коллекция",
                    "new_product_name": "Новые часы",
                    "product_id": "__new__",
                    "quantity": "1",
                    "purchase_price": "500",
                    "product_image": (
                        BytesIO(b"\xff\xd8\xffcontent"),
                        "new-watch.jpg",
                    ),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(fake_client.created_products), 1)
        self.assertEqual(
            fake_client.created_products[0]["image"]["filename"],
            "new-watch.jpg",
        )
        self.assertEqual(fake_client.uploads, [])

    def test_manual_sales_support_existing_and_custom_sources(self):
        path = self.temp_path / "manual_sales.json"
        sources = [*web.DEFAULT_SALES_SOURCES, "__custom__"]

        with mock.patch.object(
            web, "get_manual_sales_path", return_value=path
        ), mock.patch.object(
            web,
            "get_warehouse_items",
            return_value=[warehouse_item()],
        ):
            for index, source in enumerate(sources, start=1):
                response = self.client.post(
                    "/sales/manual/add",
                    data={
                        "created_at": "2026-07-22",
                        "source": source,
                        "custom_source": "Avito",
                        "product_id": PRODUCT_ID,
                        "product_name": "Подменённое название",
                        "brand": "Подменённый бренд",
                        "category": "Подменённая категория",
                        "quantity": "1",
                        "unit_price": "1000",
                        "commission_amount": "125,50",
                        "order_number": f"ORDER-{index}",
                        "sticker_number": (
                            "WB-STICKER"
                            if source == "Wildberries"
                            else ""
                        ),
                        "track_number": "TRACK-1",
                        "recipient": "ПВЗ",
                        "recipient_name": "Иванов Иван",
                        "payment_method": "Robokassa",
                        "delivery_method": "СДЭК",
                        "region": "Москва",
                        "city": "Москва",
                        "note": "Тест",
                        "order_status": "processing",
                    },
                )
                self.assertEqual(response.status_code, 302)

            sales = web.load_manual_sales()

        self.assertEqual(len(sales), len(sources))
        self.assertEqual(sales[-1]["source"], "Avito")
        self.assertEqual(sales[0]["product_name"], "Часы Test")
        self.assertEqual(sales[0]["brand"], "Brand")
        self.assertEqual(sales[0]["category"], "Коллекция")
        self.assertEqual(sales[0]["payment_method"], "Robokassa")
        self.assertEqual(sales[0]["commission_amount"], 125.5)
        self.assertEqual(sales[0]["commission_type"], "fixed_rub")
        self.assertEqual(sales[0]["order_status"], "processing")

    def test_old_manual_delete_route_cancels_without_removing(self):
        path = self.temp_path / "manual_sales.json"
        path.write_text(
            json.dumps([
                {
                    "id": "sale-1",
                    "product_name": "Часы Test",
                    "quantity": 1,
                }
            ]),
            encoding="utf-8",
        )

        with mock.patch.object(
            web, "get_manual_sales_path", return_value=path
        ):
            response = self.client.post(
                "/sales/manual/delete",
                data={"sale_id": "sale-1"},
            )
            sales = web.load_manual_sales()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["order_status"], "cancelled")
        self.assertTrue(sales[0]["cancelled_at"])

    def test_sales_status_route_cancels_manual_and_automatic_without_delete(self):
        manual_path = self.temp_path / "manual_sales.json"
        overrides_path = self.temp_path / "automatic_sales_overrides.json"
        manual_path.write_text(
            json.dumps([{"id": "manual-1", "order_status": "completed"}]),
            encoding="utf-8",
        )
        operation = {
            "id": "automatic-1",
            "source": "Заказ Битрикс",
            "type": "writeoff",
        }

        with mock.patch.object(
            web, "get_manual_sales_path", return_value=manual_path
        ), mock.patch.object(
            web,
            "get_automatic_sales_overrides_path",
            return_value=overrides_path,
        ), mock.patch.object(
            web, "load_stock_operations", return_value=[operation]
        ):
            manual_response = self.client.post(
                "/sales/status",
                data={
                    "sale_id": "manual-1",
                    "sale_type": "manual",
                    "order_status": "cancelled",
                },
            )
            automatic_response = self.client.post(
                "/sales/status",
                data={
                    "sale_id": "automatic-1",
                    "sale_type": "automatic",
                    "order_status": "cancelled",
                },
            )
            manual_sales = web.load_manual_sales()
            overrides = web.load_automatic_sales_overrides()

        self.assertEqual(manual_response.status_code, 302)
        self.assertEqual(automatic_response.status_code, 302)
        self.assertEqual(len(manual_sales), 1)
        self.assertEqual(manual_sales[0]["order_status"], "cancelled")
        self.assertEqual(
            overrides["automatic-1"]["order_status"],
            "cancelled",
        )

    def test_sales_report_filters_one_sided_dates_order_and_cancelled_totals(self):
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (
            datetime.now() - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        future = (
            datetime.now() + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        sales = [
            report_sale(id="today", created_at=today),
            report_sale(
                id="past",
                created_at=yesterday,
                order_number="PAST-1",
            ),
            report_sale(
                id="future",
                created_at=future,
                order_number="FUTURE-1",
            ),
        ]

        result_from = web.filter_sales_report_records(
            sales,
            {"date_from": today},
        )
        result_to = web.filter_sales_report_records(
            sales,
            {"date_to": today},
        )
        result_order = web.filter_sales_report_records(
            sales,
            {"order_number": "past-1"},
        )

        self.assertEqual([sale["id"] for sale in result_from], ["today"])
        self.assertEqual(
            {sale["id"] for sale in result_to},
            {"today", "past"},
        )
        self.assertEqual([sale["id"] for sale in result_order], ["past"])

        cancelled = report_sale(
            id="cancelled",
            order_number="ORDER-200",
            quantity_value=50,
            total_amount=50000,
            order_status="cancelled",
            order_status_label="Отменён",
            is_cancelled=True,
        )

        with mock.patch.object(
            web,
            "build_sales_report_records",
            return_value=[sales[0], cancelled],
        ), web.app.test_request_context("/sales/report"):
            context = web.build_sales_report_context()

        self.assertEqual(context["total_records"], 2)
        self.assertEqual(context["total_sales"], 1)
        self.assertEqual(context["total_cancelled"], 1)
        self.assertEqual(context["total_quantity"], "2")
        self.assertEqual(context["total_revenue"], 2000.0)

    def test_sales_report_html_xlsx_and_pdf_use_same_filtered_records(self):
        sales = [
            report_sale(),
            report_sale(id="other", order_number="OTHER-200"),
        ]

        with mock.patch.object(
            web, "build_sales_report_records", return_value=sales
        ):
            html = self.client.get(
                "/sales/report?order_number=ORDER-100"
            )
            xlsx = self.client.get(
                "/sales/report.xlsx?order_number=ORDER-100"
            )
            pdf = self.client.get(
                "/sales/report.pdf?order_number=ORDER-100"
            )

        self.assertEqual(html.status_code, 200)
        html_text = html.get_data(as_text=True)
        self.assertIn("ORDER-100", html_text)
        self.assertNotIn("OTHER-200", html_text)

        self.assertEqual(xlsx.status_code, 200)
        workbook = load_workbook(BytesIO(xlsx.data), read_only=True)
        sheet = workbook.active
        self.assertEqual(sheet["L5"].value, "ORDER-100")
        self.assertIsNone(sheet["L6"].value)

        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def test_repair_archive_restore_keeps_history(self):
        path = self.temp_path / "repair_cases.json"
        path.write_text(
            json.dumps([
                {
                    "id": "repair-1",
                    "repair_number": "R-2026-0001",
                    "status": "ready",
                    "client_name": "Иван",
                    "product_name": "Часы Test",
                }
            ], ensure_ascii=False),
            encoding="utf-8",
        )

        with mock.patch.object(
            web, "get_repair_cases_path", return_value=path
        ):
            archived = self.client.post(
                "/repair/delete",
                data={"case_id": "repair-1"},
            )
            cases_after_archive = web.load_repair_cases()
            active_page = self.client.get("/repair?view=active")
            archive_page = self.client.get("/repair?view=archive")
            restored = self.client.post(
                "/repair/status",
                data={
                    "case_id": "repair-1",
                    "status": "in_progress",
                },
            )
            cases_after_restore = web.load_repair_cases()

        self.assertEqual(archived.status_code, 302)
        self.assertEqual(len(cases_after_archive), 1)
        self.assertEqual(cases_after_archive[0]["status"], "completed")
        self.assertTrue(cases_after_archive[0]["archived_at"])
        self.assertNotIn("R-2026-0001", active_page.get_data(as_text=True))
        self.assertIn("R-2026-0001", archive_page.get_data(as_text=True))
        self.assertEqual(restored.status_code, 302)
        self.assertEqual(cases_after_restore[0]["status"], "in_progress")
        self.assertEqual(cases_after_restore[0]["archived_at"], "")

    def test_warehouse_bulk_edit_validates_then_updates_selected_items(self):
        fake_client = mock.Mock()
        fake_client.get_or_create_product_folder.return_value = {
            "meta": {"href": "https://example.test/folder"}
        }
        fake_client.update_product.return_value = {"id": PRODUCT_ID}
        fake_client.update_product_cell_attribute.return_value = {
            "id": PRODUCT_ID
        }
        saved_cells = []

        with mock.patch.object(
            web,
            "get_warehouse_items",
            return_value=[warehouse_item()],
        ), mock.patch.object(
            web, "MoySkladClient", return_value=fake_client
        ), mock.patch.object(
            web,
            "load_warehouse_cells",
            return_value={PRODUCT_ID: "A-01"},
        ), mock.patch.object(
            web,
            "save_warehouse_cells",
            side_effect=lambda cells: saved_cells.append(dict(cells)),
        ), mock.patch.object(
            web.CatalogReader,
            "list_active_brands",
            return_value=["Новый бренд"],
        ):
            response = self.client.post(
                "/warehouse/bulk-edit",
                data={
                    "product_ids": [PRODUCT_ID],
                    "apply_brand": "1",
                    "brand": " новый   БРЕНД ",
                    "apply_category": "1",
                    "category": "Новая категория",
                    "apply_cell": "1",
                    "cell": "B-02",
                    "activity": "archive",
                },
            )

        self.assertEqual(response.status_code, 302)
        fake_client.get_or_create_product_folder.assert_called_once_with(
            "Новый бренд/Новая категория"
        )
        self.assertTrue(
            fake_client.update_product.call_args.kwargs["archived"]
        )
        fake_client.update_product_cell_attribute.assert_called_once_with(
            PRODUCT_ID,
            "B-02",
        )
        self.assertEqual(saved_cells[0][PRODUCT_ID], "B-02")

    def test_warehouse_bulk_edit_rejects_brand_outside_catalog(self):
        with mock.patch.object(
            web.CatalogReader,
            "list_active_brands",
            return_value=["Casio"],
        ), mock.patch.object(web, "MoySkladClient") as client_class:
            response = self.client.post(
                "/warehouse/bulk-edit",
                data={
                    "product_ids": [PRODUCT_ID],
                    "apply_brand": "1",
                    "brand": "Casi0",
                },
            )

        self.assertEqual(response.status_code, 302)
        client_class.assert_not_called()
        self.assertIn(
            "%D0%B8%D0%B7+%D1%81%D0%BF%D0%B8%D1%81%D0%BA%D0%B0",
            response.headers["Location"],
        )

    def test_warehouse_bulk_edit_does_not_apply_empty_field(self):
        with mock.patch.object(web, "MoySkladClient") as client_class:
            response = self.client.post(
                "/warehouse/bulk-edit",
                data={
                    "product_ids": [PRODUCT_ID],
                    "apply_brand": "1",
                    "brand": "",
                },
            )

        self.assertEqual(response.status_code, 302)
        client_class.assert_not_called()
        self.assertIn(
            "%D0%B1%D1%80%D0%B5%D0%BD%D0%B4",
            response.headers["Location"],
        )

    def test_warehouse_thumbnail_proxy_and_live_search_template(self):
        item = warehouse_item(has_images=True)

        with mock.patch.object(
            web.MoySkladClient,
            "download_product_thumbnail",
            return_value=(b"image", "image/png"),
        ):
            thumbnail = self.client.get(
                f"/warehouse/product/{PRODUCT_ID}/thumbnail"
            )

        self.assertEqual(thumbnail.status_code, 200)
        self.assertEqual(thumbnail.mimetype, "image/png")
        self.assertEqual(thumbnail.headers["X-Content-Type-Options"], "nosniff")

        with mock.patch.object(
            web, "get_warehouse_items", return_value=[item]
        ), mock.patch.object(
            web, "load_stock_operations", return_value=[]
        ), mock.patch.object(
            web.CatalogReader,
            "list_active_brands",
            return_value=["A & Co.", "Бренд.ру"],
        ):
            page = self.client.get("/warehouse?q=Часы")

        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("filterWarehouseRows", html)
        self.assertIn("window.history.replaceState", html)
        self.assertIn(item["thumbnail_url"], html)
        self.assertIn("warehouseBulkForm", html)
        self.assertIn("confirmWarehouseBulkEdit", html)
        self.assertIn('id="bulkBrandCombobox"', html)
        self.assertIn('role="combobox"', html)
        self.assertIn('data-brand="A &amp; Co."', html)
        self.assertIn('id="bulkBrandClear"', html)
        self.assertIn("normalizeBrandSearch", html)
        self.assertIn('event.key === "ArrowDown"', html)
        self.assertNotIn('id="warehouseBrandOptions"', html)

    def test_sales_template_has_search_state_resize_fallback_and_mobile_css(self):
        with mock.patch.object(
            web, "get_warehouse_items", return_value=[warehouse_item()]
        ), mock.patch.object(
            web, "load_stock_operations", return_value=[]
        ), mock.patch.object(
            web, "load_manual_sales", return_value=[]
        ), mock.patch.object(
            web, "load_automatic_sales_overrides", return_value={}
        ), mock.patch.object(
            web,
            "get_russian_region_cities",
            return_value={"Москва": ["Москва"]},
        ):
            page = self.client.get("/sales")

        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("vechasu-sales-filters-v1", html)
        self.assertIn("vechasu-sales-scroll-y", html)
        self.assertIn("minimumPixels[index] || 82", html)
        self.assertIn("clearSalesDateFrom", html)
        self.assertIn("@media (max-width:", html)

    def test_manual_and_automatic_sales_rows_match_table_headers(self):
        manual_sale = {
            "id": "manual-1",
            "created_at": "2026-07-22",
            "source": "Tictactoy",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "quantity": 1,
            "unit_price": 1000,
            "order_status": "completed",
        }
        operation = {
            "id": "automatic-1",
            "created_at": "2026-07-22",
            "source": "Заказ Битрикс",
            "type": "writeoff",
            "product_id": PRODUCT_ID,
            "product_name": "Часы Test",
            "quantity": 1,
            "order_number": "ORDER-1",
        }

        with mock.patch.object(
            web, "get_warehouse_items", return_value=[warehouse_item()]
        ), mock.patch.object(
            web, "load_stock_operations", return_value=[operation]
        ), mock.patch.object(
            web, "load_manual_sales", return_value=[manual_sale]
        ), mock.patch.object(
            web, "load_automatic_sales_overrides", return_value={}
        ), mock.patch.object(
            web,
            "get_russian_region_cities",
            return_value={"Москва": ["Москва"]},
        ):
            page = self.client.get("/sales")

        html = page.get_data(as_text=True)
        header = re.search(
            r"<thead>\s*<tr>(.*?)</tr>",
            html,
            re.DOTALL,
        )
        rows = re.findall(
            r'<tr\s+class="sale-row[^"]*"[^>]*>(.*?)</tr>',
            html,
            re.DOTALL,
        )

        self.assertIsNotNone(header)
        self.assertEqual(len(rows), 2)
        header_count = len(re.findall(r"<th\b", header.group(1)))

        for row in rows:
            self.assertEqual(len(re.findall(r"<td\b", row)), header_count)


if __name__ == "__main__":
    unittest.main()
