import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from app import web


PRODUCT_ID = "11111111-1111-1111-1111-111111111111"


def product_item(**changes):
    item = {
        "id": PRODUCT_ID,
        "name": "Часы Test",
        "article": "ART-1",
        "code": "CODE-1",
        "brand": "Brand",
        "category": "Коллекция",
        "stock": 3,
        "stock_display": "3",
        "has_images": False,
    }
    item.update(changes)
    return item


def sale_record(source, note="Длинное примечание к продаже"):
    return {
        "id": source.lower(),
        "sale_type": "manual",
        "sale_type_label": "Ручная",
        "is_manual": True,
        "created_at": "2026-07-20",
        "source": source,
        "source_key": web.normalize_sales_source_key(source),
        "barcode": "CODE-1",
        "brand": "Brand",
        "category": "Коллекция",
        "product_id": PRODUCT_ID,
        "product_name": "Часы Test",
        "quantity_value": 1,
        "quantity_display": "1",
        "unit_price": 1000,
        "unit_price_display": "1 000 ₽",
        "total_amount": 1000,
        "total_amount_display": "1 000 ₽",
        "order_number": "ORDER-1",
        "track_number": "TRACK-1",
        "delivery_method": "СДЭК",
        "delivery_cost": 0,
        "delivery_cost_display": "",
        "country": "",
        "region": "",
        "city": "",
        "payment_method": "",
        "recipient_name": "",
        "platform": "",
        "invoice_number": "",
        "sticker_number": "",
        "commission": "",
        "commission_amount": 0,
        "commission_display": "",
        "order_status": "completed",
        "order_status_label": "Завершён",
        "is_cancelled": False,
        "cancelled_at": "",
        "note": note,
    }


class FakeMoySkladClient:
    def __init__(self):
        self.folders = []
        self.created_products = []
        self.created_receipts = []
        self.updated_receipts = []

    def get_or_create_product_folder(self, path):
        self.folders.append(path)
        return {"meta": {"href": "folder://" + path}}

    def create_product(self, **payload):
        self.created_products.append(payload)
        return {
            "id": "new-moysklad-product",
            "name": payload["name"],
            "code": payload["code"],
        }

    def create_stock_enter_many(self, positions, reason=None, moment=None):
        self.created_receipts.append({
            "positions": positions,
            "reason": reason,
            "moment": moment,
        })
        return {
            "id": "enter-1",
            "name": "ПР-0001",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }

    def update_stock_enter_many(
        self,
        document_id,
        positions,
        reason=None,
        moment=None,
    ):
        self.updated_receipts.append({
            "document_id": document_id,
            "positions": positions,
            "reason": reason,
            "moment": moment,
        })
        return {
            "id": document_id,
            "name": "ПР-0001",
            "meta": {"uuidHref": "https://example.test/enter-1"},
        }


class FakeExcelCatalog:
    def __init__(self):
        self.created = []
        self.archived = []
        self.updated = []

    def create_product(self, **payload):
        self.created.append(payload)
        return {"id": 77, **payload}

    def archive_product(self, product_id):
        self.archived.append(product_id)

    def update_product(self, product_id, **payload):
        self.updated.append((product_id, payload))
        return {"id": product_id, **payload}


class SalesReceiptsEnhancementsTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.taxonomy_path = (
            Path(self.temp_directory.name)
            / "catalog_taxonomy.json"
        )
        self.taxonomy_patch = mock.patch.object(
            web,
            "CATALOG_TAXONOMY_PATH",
            self.taxonomy_path,
        )
        self.taxonomy_patch.start()

    def tearDown(self):
        self.taxonomy_patch.stop()
        self.temp_directory.cleanup()

    def test_old_sale_note_is_rendered_and_exported_as_empty(self):
        from reportlab.platypus import Paragraph

        old_sale = sale_record("Tictactoy", note=None)
        pdf_paragraphs = []

        def capture_pdf_paragraph(text, style):
            pdf_paragraphs.append(text)
            return Paragraph(text, style)

        with mock.patch.object(
            web,
            "build_sales_report_records",
            return_value=[old_sale],
        ), mock.patch.object(
            web,
            "get_warehouse_items",
            return_value=[],
        ), mock.patch.object(
            web,
            "get_excel_warehouse_items",
            return_value=[],
        ), mock.patch(
            "reportlab.platypus.Paragraph",
            side_effect=capture_pdf_paragraph,
        ):
            page = self.client.get(
                "/sales?source=tictactoy"
            ).get_data(as_text=True)
            excel_report = self.client.get(
                "/sales/report.xlsx?source=tictactoy"
            )
            pdf_report = self.client.get(
                "/sales/report.pdf?source=tictactoy"
            )

        self.assertNotIn(">None<", page)
        self.assertNotIn(">null<", page)
        workbook = load_workbook(
            BytesIO(excel_report.data),
            read_only=True,
        )
        sheet = workbook.active
        headers = [cell.value for cell in sheet[4]]
        note_column = headers.index("Примечание") + 1
        self.assertIsNone(sheet.cell(5, note_column).value)
        self.assertEqual(pdf_report.status_code, 200)
        report_columns = [
            *web.get_sales_columns("tictactoy"),
            {"key": "returned_quantity_display"},
            {"key": "returned_at"},
            {"key": "return_reason"},
        ]
        note_index = next(
            index
            for index, column in enumerate(report_columns)
            if column["key"] == "note"
        )
        self.assertEqual(
            pdf_paragraphs[-len(report_columns) + note_index],
            "",
        )

    def test_receipt_inline_product_creation_is_retired(self):
        with mock.patch.object(web,'MoySkladClient') as remote:
            response=web.app.test_client().post('/receipts/catalog/create',json={'kind':'product','name':'Manual'})
        self.assertEqual(response.status_code,410)
        remote.assert_not_called()

    def test_receipt_ui_uses_supply_documents(self):
        page=web.app.test_client().get('/receipts')
        text=page.get_data(as_text=True)
        self.assertEqual(page.status_code,200)
        self.assertIn('Новая поставка',text)
        self.assertIn('Добавить товар в поставку',text)
        self.assertIn('Сохраните черновик',text)
        self.assertNotIn('Добавить из Bitrix',text)
        self.assertNotIn('Цена закупки',text)



if __name__ == "__main__":
    unittest.main()
