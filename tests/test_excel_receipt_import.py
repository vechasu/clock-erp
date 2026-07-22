import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from app.catalog_db import CatalogDatabase
from app.services.excel_receipt_import import (
    ExcelDraftBlockedError,
    ExcelReceiptImportService,
)


def workbook_bytes(headers, rows, title="Импорт", preface=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    if preface is not None:
        sheet.append([preface])
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    target = BytesIO()
    workbook.save(target)
    workbook.close()
    return target.getvalue()


class ExcelReceiptImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        self.database = CatalogDatabase(self.path)
        self.database.initialize()
        self.service = ExcelReceiptImportService(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def valid_file(self):
        return workbook_bytes(
            ["Бренд", "Ячейка", "Количество", "Название", "Артикул", "Категория"],
            [
                ["Casio", "A-1", 2, "Watch A", "SKU-1", "Часы"],
                ["Other", "B-2", 3, "Watch B", "SKU-2", "Часы"],
            ],
            preface="Поставка июля",
        )

    def catalog_totals(self):
        with self.database.connect() as connection:
            return (
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_products"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COALESCE(SUM(stock), 0) FROM catalog_excel_products"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_receipt_operations"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_stock_operations"
                ).fetchone()[0],
            )

    def test_preview_does_not_change_cards_stock_or_operations(self):
        before = self.catalog_totals()
        draft = self.service.preview(self.valid_file(), "receipt.xlsx")
        after = self.catalog_totals()
        self.assertEqual(before, after)
        self.assertEqual(draft["status"], "ready")
        self.assertEqual((draft["valid_rows"], draft["total_quantity"]), (2, 5))
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_receipts"
                ).fetchone()[0],
                0,
            )

    def test_numeric_date_time_and_formula_names_are_blocking_errors(self):
        data = workbook_bytes(
            ["Название", "Бренд", "Остаток"],
            [
                [0.034027777777777775, "Wrong", 1],
                ["0.034027777777777775", "Wrong", 1],
                ["=TIME(0,49,0)", "Wrong", 1],
            ],
        )
        draft = self.service.preview(data, "bad-names.xlsx")
        self.assertEqual((draft["status"], draft["error_rows"], draft["valid_rows"]), (
            "blocked", 3, 0,
        ))
        self.assertEqual(
            {row["error_code"] for row in draft["rows"]},
            {"name_numeric", "name_formula"},
        )
        with self.assertRaises(ExcelDraftBlockedError):
            self.service.post(draft["id"])
        self.assertEqual(self.catalog_totals(), (0, 0, 0, 0))

    def test_headers_are_matched_by_name_and_do_not_shift_brand(self):
        data = workbook_bytes(
            ["Остаток", "Категория", "Название", "Артикул", "Бренд", "Ячейка"],
            [[1, "28th of MAY", "GA-2100", "GA-2100-1A", "Casio", "C-7"]],
        )
        draft = self.service.preview(data, "permuted.xlsx")
        row = draft["rows"][0]["data"]
        self.assertEqual(draft["status"], "ready")
        self.assertEqual(row["excel_brand"], "Casio")
        self.assertEqual(row["category"], "28th of MAY")

    def test_excel_date_label_is_not_accepted_as_a_brand(self):
        data = workbook_bytes(
            ["Название", "Бренд", "Остаток"],
            [["0.034027777777777775", "28th of MAY", 0]],
        )
        draft = self.service.preview(data, "date-brand.xlsx")
        row = draft["rows"][0]
        self.assertEqual(row["row_status"], "error")
        self.assertEqual(row["data"]["excel_brand"], "")
        self.assertIn("28th of MAY", row["raw_values"])

    def test_zero_quantity_is_explicitly_excluded_without_card(self):
        data = workbook_bytes(
            ["Название", "Бренд", "Количество"],
            [["Zero card", "Brand", 0], ["Live card", "Brand", 4]],
        )
        draft = self.service.preview(data, "zero.xlsx")
        self.assertEqual((draft["status"], draft["excluded_rows"], draft["valid_rows"]), (
            "ready", 1, 1,
        ))
        receipt = self.service.post(draft["id"])
        self.assertEqual((receipt["row_count"], receipt["total_quantity"]), (1, 4))
        with self.database.connect() as connection:
            names = [row[0] for row in connection.execute(
                "SELECT excel_name_raw FROM catalog_excel_products"
            ).fetchall()]
        self.assertEqual(names, ["Live card"])

    def test_explicit_post_creates_one_receipt_and_is_idempotent(self):
        draft = self.service.preview(self.valid_file(), "receipt.xlsx")
        first = self.service.post(draft["id"])
        second = self.service.post(draft["id"])
        self.assertFalse(first["already_posted"])
        self.assertTrue(second["already_posted"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual((first["row_count"], first["operation_rows"]), (2, 2))
        with self.database.connect() as connection:
            counts = (
                connection.execute("SELECT COUNT(*) FROM catalog_excel_receipts").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM catalog_excel_receipt_rows").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM catalog_excel_receipt_operations").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM catalog_excel_products").fetchone()[0],
                connection.execute("SELECT SUM(stock) FROM catalog_excel_products").fetchone()[0],
            )
        self.assertEqual(counts, (1, 2, 2, 2, 5))

    def test_failure_mid_post_rolls_back_every_working_table(self):
        draft = self.service.preview(self.valid_file(), "failure.xlsx")

        def fail_on_second(position, _result):
            if position == 2:
                raise RuntimeError("test failure")

        failing = ExcelReceiptImportService(self.database, fault_hook=fail_on_second)
        with self.assertRaises(RuntimeError):
            failing.post(draft["id"])
        with self.database.connect() as connection:
            counts = tuple(connection.execute(
                "SELECT COUNT(*) FROM {}".format(table)
            ).fetchone()[0] for table in (
                "catalog_excel_receipts",
                "catalog_excel_receipt_rows",
                "catalog_excel_receipt_operations",
                "catalog_excel_products",
                "catalog_excel_batches",
            ))
            status = connection.execute(
                "SELECT status FROM catalog_excel_import_drafts WHERE id = ?", (draft["id"],)
            ).fetchone()[0]
        self.assertEqual(counts, (0, 0, 0, 0, 0))
        self.assertEqual(status, "ready")

    def test_repeat_upload_reuses_the_same_draft(self):
        data = self.valid_file()
        first = self.service.preview(data, "receipt.xlsx")
        second = self.service.preview(data, "renamed.xlsx")
        self.assertEqual(first["id"], second["id"])
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_excel_import_drafts"
                ).fetchone()[0],
                1,
            )

    def test_web_preview_requires_a_separate_post_confirmation(self):
        from app import web

        web.app.config["TESTING"] = True
        before = self.catalog_totals()
        with mock.patch.dict("os.environ", {"CATALOG_DATABASE_PATH": str(self.path)}):
            client = web.app.test_client()
            response = client.post(
                "/products/receipts/preview",
                data={"file": (BytesIO(self.valid_file()), "receipt.xlsx")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        rendered = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Предпросмотр прихода", rendered)
        self.assertIn("Оформить приход", rendered)
        self.assertIn("Каталог и остатки ещё не изменены", rendered)
        self.assertEqual(before, self.catalog_totals())
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM catalog_excel_receipts").fetchone()[0],
                0,
            )

    def test_legacy_excel_payload_cannot_bypass_receipt_draft(self):
        from app import web

        web.app.config["TESTING"] = True
        with mock.patch.object(web, "MoySkladClient") as moysklad:
            response = web.app.test_client().post(
                "/receipts/create",
                data={"import_payload": '[{"name":"Bypass","quantity":1}]'},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("notice=error", response.headers["Location"])
        self.assertIn("open_receipt_modal=1", response.headers["Location"])
        moysklad.assert_not_called()


if __name__ == "__main__":
    unittest.main()
