import json
import re
import tempfile
import time
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
        catalog = mock.Mock()

        with mock.patch.object(
            web,
            "get_excel_warehouse_items",
            return_value=[{"id": PRODUCT_ID, "brand": "Новый бренд"}],
        ), mock.patch.object(
            web, "ExcelProductCatalog", return_value=catalog
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
        catalog.update_product.assert_called_once_with(
            PRODUCT_ID,
            brand="Новый бренд",
            category="Новая категория",
            cell="B-02",
        )
        catalog.archive_product.assert_called_once_with(PRODUCT_ID)

    def test_warehouse_bulk_edit_rejects_brand_outside_catalog(self):
        with mock.patch.object(
            web, "get_excel_warehouse_items",
            return_value=[{"id": PRODUCT_ID, "brand": "Casio"}],
        ), mock.patch.object(web, "ExcelProductCatalog") as catalog_class:
            response = self.client.post(
                "/warehouse/bulk-edit",
                data={
                    "product_ids": [PRODUCT_ID],
                    "apply_brand": "1",
                    "brand": "Casi0",
                },
            )

        self.assertEqual(response.status_code, 302)
        catalog_class.assert_not_called()
        self.assertIn(
            "%D0%B8%D0%B7+%D1%81%D0%BF%D0%B8%D1%81%D0%BA%D0%B0",
            response.headers["Location"],
        )

    def test_warehouse_bulk_edit_can_update_only_brand(self):
        catalog = mock.Mock()

        with mock.patch.object(
            web,
            "get_excel_warehouse_items",
            return_value=[{"id": PRODUCT_ID, "brand": "AARK"}],
        ), mock.patch.object(
            web, "ExcelProductCatalog", return_value=catalog
        ):
            response = self.client.post(
                "/warehouse/bulk-edit",
                data={
                    "product_ids": [PRODUCT_ID],
                    "apply_brand": "1",
                    "brand": "AARK",
                },
            )

        self.assertEqual(response.status_code, 302)
        catalog.update_product.assert_called_once_with(
            PRODUCT_ID,
            brand="AARK",
            category=None,
            cell=None,
        )

    def test_warehouse_bulk_edit_can_update_only_category(self):
        catalog = mock.Mock()

        with mock.patch.object(
            web, "ExcelProductCatalog", return_value=catalog
        ):
            response = self.client.post(
                "/warehouse/bulk-edit",
                data={
                    "product_ids": [PRODUCT_ID],
                    "apply_category": "1",
                    "category": "Наручные часы",
                    "return_query": (
                        "?q=AARK&brand=AARK"
                        "&sort_by=brand&sort_dir=desc"
                    ),
                },
            )

        self.assertEqual(response.status_code, 302)
        catalog.update_product.assert_called_once_with(
            PRODUCT_ID,
            brand=None,
            category="Наручные часы",
            cell=None,
        )
        self.assertIn("q=AARK", response.headers["Location"])
        self.assertIn("brand=AARK", response.headers["Location"])
        self.assertIn("sort_by=brand", response.headers["Location"])
        self.assertIn("sort_dir=desc", response.headers["Location"])

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
            web, "get_excel_warehouse_items", return_value=[item]
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
        self.assertIn('data-brand="Brand"', html)
        self.assertIn(
            'data-bulk-toggle="bulkBrandCombobox"',
            html,
        )
        self.assertNotIn('id="bulkBrandClear"', html)
        self.assertNotIn("normalizeBrandSearch", html)
        self.assertIn('event.key === "ArrowDown"', html)
        self.assertNotIn('id="warehouseBrandOptions"', html)

    def test_warehouse_bulk_selection_mode_uses_light_toolbar(self):
        item = warehouse_item()

        with mock.patch.object(
            web, "get_excel_warehouse_items", return_value=[item]
        ):
            page = self.client.get("/warehouse")

        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(
            page.headers["Cache-Control"],
            "no-store, no-cache, must-revalidate, max-age=0",
        )
        self.assertEqual(page.headers["Pragma"], "no-cache")
        self.assertEqual(page.headers["Expires"], "0")
        self.assertIn('id="warehouseBulkPanel"', html)
        self.assertIn('class="warehouse-bulk-toolbar"', html)
        self.assertIn("Выбрано: 0 товаров", html)
        self.assertIn('id="warehouseBulkSelectAllButton"', html)
        self.assertIn("Выбрать все", html)
        self.assertIn('id="warehouseBulkEditButton"', html)
        self.assertRegex(
            html,
            r'id="warehouseBulkEditButton"[^>]*\sdisabled',
        )
        self.assertIn("Изменить выбранные", html)
        self.assertIn("× Выйти", html)
        self.assertIn("toggleWarehouseBulkEditor(true)", html)
        self.assertIn("selectAllWarehouseBulkRows()", html)
        self.assertIn("row.classList.toggle(", html)
        self.assertIn('"is-bulk-selected"', html)
        self.assertIn(
            ".warehouse-products-table tbody "
            "tr.is-bulk-selected td",
            html,
        )
        self.assertIn("background: #eff6ff", html)
        self.assertIn(".warehouse-bulk-toolbar", html)
        self.assertIn("background: #ffffff", html)
        self.assertIn(
            'editButton.disabled = !selectedCheckboxes.length',
            html,
        )
        self.assertIn("toggleWarehouseBulkEditor(false)", html)
        self.assertIn('checkbox.checked = false', html)
        self.assertIn(
            'count.textContent = "Выбрано: "',
            html,
        )
        self.assertIn(
            '.warehouse-bulk-form.is-open',
            html,
        )
        self.assertIn(
            ".warehouse-bulk-form-actions button[type=\"submit\"]",
            html,
        )
        self.assertIn("background: #2563eb", html)
        self.assertIn('data-bulk-auto-apply="true"', html)
        self.assertIn(
            '<div class="warehouse-bulk-field-title">Бренд</div>',
            html,
        )
        self.assertIn(
            '<div class="warehouse-bulk-field-title">Категория</div>',
            html,
        )
        self.assertIn("cancelWarehouseBulkEditor()", html)
        self.assertIn(">Отмена<", "".join(html.split()))
        self.assertIn(
            "Изменения будут применены к ",
            html,
        )
        self.assertIn('name="return_query"', html)
        self.assertIn(
            "autoApplyToggle.checked = Boolean(brand)",
            html,
        )

    def test_warehouse_bulk_checkboxes_are_inside_photo_cells(self):
        item = warehouse_item(has_images=True)

        with mock.patch.object(
            web, "get_excel_warehouse_items", return_value=[item]
        ):
            page = self.client.get("/warehouse")

        html = page.get_data(as_text=True)
        photo_header = re.search(
            r'<th data-column-key="photo">(.*?)</th>',
            html,
            re.DOTALL,
        )
        name_header = re.search(
            r'<th data-column-key="name">(.*?)</th>',
            html,
            re.DOTALL,
        )
        photo_cell = re.search(
            r'<td data-column-key="photo">(.*?)</td>',
            html,
            re.DOTALL,
        )
        name_cell = re.search(
            r'<td data-column-key="name">(.*?)</td>',
            html,
            re.DOTALL,
        )

        self.assertIsNotNone(photo_header)
        self.assertIsNotNone(name_header)
        self.assertIsNotNone(photo_cell)
        self.assertIsNotNone(name_cell)
        self.assertIn('id="warehouseSelectAll"', photo_header.group(1))
        self.assertNotIn('id="warehouseSelectAll"', name_header.group(1))
        self.assertIn(
            "js-warehouse-product-select",
            photo_cell.group(1),
        )
        self.assertIn(
            "warehouse-product-thumb",
            photo_cell.group(1),
        )
        self.assertLess(
            photo_cell.group(1).index("js-warehouse-product-select"),
            photo_cell.group(1).index("warehouse-product-thumb"),
        )
        self.assertNotIn(
            "js-warehouse-product-select",
            name_cell.group(1),
        )
        self.assertIn(
            "body.warehouse-bulk-edit-open .warehouse-row-select",
            html,
        )

    def test_warehouse_filters_created_date_range_in_local_time(self):
        first = warehouse_item()
        first.update(
            id="11111111-1111-1111-1111-111111111111",
            name="Часы 22 июля",
            created_at=time.mktime(
                time.strptime("2026-07-22 23:30", "%Y-%m-%d %H:%M")
            ),
        )
        second = warehouse_item()
        second.update(
            id="22222222-2222-2222-2222-222222222222",
            name="Часы 23 июля",
            created_at=time.mktime(
                time.strptime("2026-07-23 00:30", "%Y-%m-%d %H:%M")
            ),
        )

        with mock.patch.object(
            web, "get_excel_warehouse_items", return_value=[first, second]
        ):
            page = self.client.get(
                "/warehouse?date_from=2026-07-22&date_to=2026-07-22"
                "&brand=Brand"
            )

        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn(
            'data-product-id="11111111-1111-1111-1111-111111111111"',
            html,
        )
        self.assertNotIn(
            'data-product-id="22222222-2222-2222-2222-222222222222"',
            html,
        )
        self.assertIn('name="date_from" value="2026-07-22"', html)
        self.assertIn('name="date_to" value="2026-07-22"', html)
        self.assertIn("warehouse-calendar-popup", html)
        self.assertIn('id="warehouseFilterReset"', html)
        self.assertNotRegex(
            html,
            r'id="warehouseFilterReset"[^>]*\shidden',
        )
        self.assertIn('aria-label="Сбросить диапазон дат"', html)
        self.assertIn("resetWarehouseTableFilters", html)
        self.assertIn("clearWarehouseDateRange", html)
        self.assertIn('id="warehouseMoreMenu"', html)
        self.assertIn('id="warehouseMoreDropdown"', html)
        self.assertIn("toggleWarehouseMoreMenu", html)
        self.assertIn("runWarehouseMoreAction('bulk')", html)
        self.assertIn("runWarehouseMoreAction('map')", html)
        self.assertIn('role="menuitem"', html)
        self.assertLess(
            html.index("Фильтры"),
            html.index('id="warehouseMoreMenu"'),
        )
        self.assertLess(
            html.index('id="warehouseMoreMenu"'),
            html.index('id="warehouseFilterReset"'),
        )
        self.assertLess(
            html.index('id="warehouseFilterReset"'),
            html.index("+ Добавить товар"),
        )

        with mock.patch.object(
            web, "get_excel_warehouse_items", return_value=[first, second]
        ):
            standard_page = self.client.get("/warehouse")

        self.assertRegex(
            standard_page.get_data(as_text=True),
            r'id="warehouseFilterReset"[^>]*\shidden',
        )

    def test_add_product_comboboxes_use_independent_contains_search(self):
        items = []
        for index, (brand, category) in enumerate((
            ("Hypergrand", "Наручные часы"),
            ("Klokers", "Ремень"),
            ("Contempus", "Аксессуары"),
        ), start=1):
            item = warehouse_item()
            item.update(
                id=f"{index:08d}-1111-1111-1111-111111111111",
                brand=brand,
                category=category,
                raw_category=category,
            )
            items.append(item)

        with mock.patch.object(
            web, "get_excel_warehouse_items", return_value=items
        ):
            page = self.client.get("/warehouse?open_add=1")

        html = page.get_data(as_text=True)
        brand_component = html.split('id="addBrandCombobox"', 1)[1].split(
            'id="addCategoryCombobox"',
            1,
        )[0]
        category_component = html.split('id="addCategoryCombobox"', 1)[1].split(
            'name="stock"',
            1,
        )[0]

        self.assertEqual(html.count('id="addBrandCombobox"'), 1)
        self.assertEqual(html.count('id="addCategoryCombobox"'), 1)
        self.assertIn('data-prefix-search="false"', brand_component)
        self.assertIn('data-prefix-search="false"', category_component)
        self.assertIn("Ничего не найдено", brand_component)
        self.assertIn("Ничего не найдено", category_component)
        self.assertIn(".filter-combobox .brand-combobox-option[hidden]", html)
        self.assertIn("brandName.includes(query)", html)
        self.assertIn('searchInput.addEventListener("input"', html)
        self.assertIn(
            "filterBrandList(searchInput.value, combobox)",
            html,
        )
        self.assertIn('searchInput.dataset.searchBound === "1"', html)
        self.assertIn('searchInput.value = ""', html)
        self.assertIn('filterBrandList("", combobox)', html)
        self.assertIn(
            "combobox.dataset.clearSelectionOnSearchClear",
            html,
        )
        self.assertIn('setBrandComboboxValue(combobox, "")', html)
        self.assertIn('hiddenInput.value = brand', html)
        self.assertIn('event.key === "ArrowDown"', html)
        self.assertIn('event.key === "Enter"', html)
        self.assertIn('event.key === "Escape"', html)

        bulk_brand_component = html.split(
            'id="bulkBrandCombobox"',
            1,
        )[1].split('id="bulkCategory"', 1)[0]
        bulk_category_component = html.split(
            'id="bulkCategory"',
            1,
        )[1].split('data-bulk-toggle="bulkCell"', 1)[0]
        self.assertIn(
            'data-prefix-search="true"',
            bulk_brand_component,
        )
        self.assertIn(
            'data-prefix-search="true"',
            bulk_category_component,
        )
        for component in (bulk_brand_component, bulk_category_component):
            self.assertIn('class="brand-combobox filter-combobox"', component)
            self.assertIn(
                'data-clear-selection-on-search-clear="true"',
                component,
            )
            self.assertIn('data-brand-search-input', component)
            self.assertIn('data-brand-search-clear', component)
            self.assertNotIn(' disabled', component)
        self.assertNotIn("bulk-brand-combobox", html)
        self.assertNotIn("bulk-brand-dropdown", html)

    def test_bulk_brand_search_matches_visible_name_prefix(self):
        items = []
        for index, brand in enumerate(("AARK", "Alpha", "Casio"), start=1):
            item = warehouse_item()
            item.update(
                id=f"{index:08d}-2222-2222-2222-222222222222",
                brand=brand,
                category="Наручные часы",
            )
            items.append(item)

        with mock.patch.object(
            web, "get_excel_warehouse_items", return_value=items
        ):
            page = self.client.get("/warehouse")

        html = page.get_data(as_text=True)
        bulk_brand_component = html.split(
            'id="bulkBrandCombobox"',
            1,
        )[1].split('id="bulkCategory"', 1)[0]

        self.assertIn('data-prefix-search="true"', bulk_brand_component)
        self.assertIn(
            '<span class="brand-combobox-option-label">AARK</span>',
            bulk_brand_component,
        )
        self.assertIn(
            '<span class="brand-combobox-option-label">Alpha</span>',
            bulk_brand_component,
        )
        self.assertIn(
            '<span class="brand-combobox-option-label">Casio</span>',
            bulk_brand_component,
        )
        self.assertIn("trim().toLocaleLowerCase()", html)
        self.assertIn(
            'option.querySelector(',
            html,
        )
        self.assertIn(
            '".brand-combobox-option-label"',
            html,
        )
        self.assertIn("label ? label.textContent : \"\"", html)
        self.assertIn("brandName.startsWith(query)", html)
        self.assertEqual(
            [
                brand
                for brand in ("AARK", "Alpha", "Casio")
                if brand.strip().lower().startswith("a")
            ],
            ["AARK", "Alpha"],
        )
        self.assertEqual(
            [
                brand
                for brand in ("AARK", "Alpha", "Casio")
                if brand.strip().lower().startswith("ca")
            ],
            ["Casio"],
        )
        self.assertNotIn(
            "Casio",
            [
                brand
                for brand in ("AARK", "Alpha", "Casio")
                if brand.strip().lower().startswith("A".lower())
            ],
        )

    def test_bulk_category_search_matches_visible_name_prefix(self):
        items = []
        values = (
            ("AARK", "Наручные часы"),
            ("Casio", "Ремень"),
            ("Alpha", "Аксессуары"),
        )
        for index, (brand, category) in enumerate(values, start=1):
            item = warehouse_item()
            item.update(
                id=f"{index:08d}-3333-3333-3333-333333333333",
                brand=brand,
                category=category,
            )
            items.append(item)

        with mock.patch.object(
            web, "get_excel_warehouse_items", return_value=items
        ):
            page = self.client.get("/warehouse")

        html = page.get_data(as_text=True)
        bulk_brand_component = html.split(
            'id="bulkBrandCombobox"',
            1,
        )[1].split('id="bulkCategory"', 1)[0]
        bulk_category_component = html.split(
            'id="bulkCategory"',
            1,
        )[1].split('data-bulk-toggle="bulkCell"', 1)[0]

        self.assertEqual(html.count('id="bulkBrandCombobox"'), 1)
        self.assertEqual(html.count('id="bulkCategory"'), 1)
        self.assertIn('data-prefix-search="true"', bulk_brand_component)
        self.assertIn('data-prefix-search="true"', bulk_category_component)
        self.assertIn(
            '<span class="brand-combobox-option-label">'
            'Наручные часы</span>',
            bulk_category_component,
        )
        self.assertIn(
            '<span class="brand-combobox-option-label">Ремень</span>',
            bulk_category_component,
        )
        categories = ("Наручные часы", "Ремень", "Аксессуары")
        self.assertEqual(
            [
                category
                for category in categories
                if category.strip().lower().startswith("н")
            ],
            ["Наручные часы"],
        )
        self.assertEqual(
            [
                category
                for category in categories
                if category.strip().lower().startswith("нар")
            ],
            ["Наручные часы"],
        )
        self.assertEqual(
            [
                category
                for category in categories
                if category.strip().lower().startswith("Н".lower())
            ],
            ["Наручные часы"],
        )
        self.assertIn(
            'data-clear-selection-on-search-clear="true"',
            bulk_category_component,
        )
        self.assertIn('searchInput.addEventListener("input"', html)
        self.assertIn('filterBrandList("", combobox)', html)

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
        self.assertIn('id="salesDateFilter"', html)
        self.assertIn('id="clearSalesPeriod"', html)
        self.assertIn("@media (max-width:", html)

    def test_sales_uses_warehouse_period_picker_and_combined_reset(self):
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
        self.assertNotIn('id="clearSalesDateFrom"', html)
        self.assertNotIn('id="clearSalesDateTo"', html)
        self.assertNotIn('class="sales-date-input"', html)
        self.assertIn("📅 Период", html)
        self.assertIn("warehouse-calendar-popup", html)
        self.assertIn("warehouse-calendar-day", html)
        self.assertIn("data-calendar-apply", html)
        self.assertIn("displaySalesDate(salesDateFrom.value)", html)
        self.assertIn('url.searchParams.set(name, value)', html)
        self.assertIn('url.searchParams.delete(name)', html)
        self.assertIn('salesSearch.value = "";', html)
        self.assertIn('salesDateFrom.value = "";', html)
        self.assertIn('salesDateTo.value = "";', html)

    def test_sales_search_clear_matches_warehouse_search_clear(self):
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
        self.assertIn(
            'class="sales-search-wrap search-input-wrap erp-search-wrap"',
            html,
        )
        self.assertIn(
            'class="search-clear-button erp-search-clear is-hidden"',
            html,
        )
        self.assertIn("right: 14px", html)
        self.assertIn("width: 18px", html)
        self.assertIn("height: 18px", html)
        self.assertIn("font-size: 22px", html)
        self.assertIn("opacity: 0.72", html)
        self.assertIn('salesSearch.value = "";', html)
        self.assertIn(
            'clearSalesSearch?.addEventListener("click"',
            html,
        )

    def test_sales_and_warehouse_use_shared_design_components(self):
        manual_sale = {
            "id": "manual-shared-design",
            "created_at": "2026-07-22",
            "source": "Tictactoy",
            "product_id": PRODUCT_ID,
            "product_name": "Будильник Braun BC05B",
            "brand": "",
            "category": "",
            "quantity": 1,
            "unit_price": 1000,
            "order_status": "completed",
        }

        with mock.patch.object(
            web, "get_warehouse_items", return_value=[warehouse_item()]
        ), mock.patch.object(
            web, "load_stock_operations", return_value=[]
        ), mock.patch.object(
            web, "load_manual_sales", return_value=[manual_sale]
        ), mock.patch.object(
            web, "load_automatic_sales_overrides", return_value={}
        ), mock.patch.object(
            web,
            "get_russian_region_cities",
            return_value={"Москва": ["Москва"]},
        ):
            sales_page = self.client.get("/sales")
            warehouse_page = self.client.get("/warehouse")

        sales_html = sales_page.get_data(as_text=True)
        warehouse_html = warehouse_page.get_data(as_text=True)
        template_folder = (
            Path(web.app.root_path) / web.app.template_folder
        )
        sales_template = (
            template_folder / "sales.html"
        ).read_text(encoding="utf-8")
        warehouse_template = (
            template_folder / "warehouse.html"
        ).read_text(encoding="utf-8")
        shared_css = (
            Path(web.app.static_folder)
            / "css"
            / "erp-components.css"
        ).read_text(encoding="utf-8")

        self.assertEqual(sales_page.status_code, 200)
        self.assertEqual(warehouse_page.status_code, 200)
        self.assertIn("css/erp-components.css", sales_html)
        self.assertIn("css/erp-components.css", warehouse_html)
        for component_class in (
            "erp-stats",
            "erp-stat-card",
            "erp-toolbar-card",
            "erp-toolbar",
            "erp-search-input",
            "erp-search-clear",
            "erp-table-card",
            "erp-data-table",
            "erp-sort-label",
            "erp-sort-arrow",
        ):
            self.assertIn(component_class, sales_template)
            self.assertIn(component_class, warehouse_template)
            self.assertIn(f".{component_class}", shared_css)
        self.assertIn("erp-product-primary", sales_template)
        self.assertIn("erp-muted-value", sales_template)
        self.assertIn("overflow: hidden", sales_html)
        self.assertIn("text-overflow: ellipsis", sales_html)

    def test_sales_product_and_category_text_stays_inside_cells(self):
        manual_sale = {
            "id": "manual-long-values",
            "created_at": "2026-07-22",
            "source": "Tictactoy",
            "product_id": PRODUCT_ID,
            "product_name": (
                "Очень длинное название товара для проверки границ ячейки"
            ),
            "brand": "Brand",
            "category": "Очень длинная категория Будильник",
            "quantity": 1,
            "unit_price": 1000,
            "order_status": "completed",
        }

        with mock.patch.object(
            web, "get_warehouse_items", return_value=[warehouse_item()]
        ), mock.patch.object(
            web, "load_stock_operations", return_value=[]
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
        self.assertEqual(page.status_code, 200)
        self.assertIn("Очень длинное название товара", html)
        self.assertIn("Очень длинная категория Будильник", html)
        self.assertIn(
            ".sales-table td.col-product .manual-view-value",
            html,
        )
        self.assertIn(
            ".sales-table td.col-category .automatic-view-value",
            html,
        )
        self.assertIn("text-overflow: ellipsis", html)
        self.assertIn("white-space: nowrap", html)
        self.assertIn("overflow: hidden", html)

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
