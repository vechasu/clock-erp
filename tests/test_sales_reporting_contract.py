from pathlib import Path
import unittest
from unittest import mock

from flask import url_for

from app import web


class SalesReportingContractTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()

    def test_public_routes_keep_paths_methods_and_endpoint_identity(self):
        expected = {
            "sales_report_page": "/sales/report",
            "sales_report_excel": "/sales/report.xlsx",
            "sales_report_pdf": "/sales/report.pdf",
        }
        rules = {
            rule.endpoint: rule
            for rule in web.app.url_map.iter_rules()
            if rule.endpoint in expected
        }

        self.assertEqual(set(rules), set(expected))
        for endpoint, path in expected.items():
            self.assertEqual(rules[endpoint].rule, path)
            self.assertEqual(rules[endpoint].methods, {"GET", "HEAD", "OPTIONS"})

        with web.app.test_request_context():
            for endpoint, path in expected.items():
                self.assertEqual(url_for(endpoint), path)

    def test_query_normalization_and_defaults_are_stable(self):
        query = (
            "/sales/report?q=%20needle%20&date_from=2026-08-12"
            "&date_to=2026-08-01&source=unknown&status=unknown"
            "&order_status=unknown&brand_id=%20brand-1%20"
        )
        with web.app.test_request_context(query):
            filters = web.get_sales_report_filters()

        self.assertEqual(filters["q"], "needle")
        self.assertEqual(filters["date_from"], "2026-08-01")
        self.assertEqual(filters["date_to"], "2026-08-12")
        self.assertEqual(filters["source"], "all")
        self.assertEqual(filters["status"], "")
        self.assertEqual(filters["order_status"], "")
        self.assertEqual(filters["brand_id"], "brand-1")

    def test_html_xlsx_and_pdf_response_contract(self):
        with mock.patch.object(
            web, "build_sales_report_records", return_value=[]
        ):
            html = self.client.get("/sales/report")
            xlsx = self.client.get("/sales/report.xlsx")
            pdf = self.client.get("/sales/report.pdf")

        self.assertEqual(html.status_code, 200)
        self.assertEqual(html.mimetype, "text/html")
        self.assertIn("Отчёт по продажам", html.get_data(as_text=True))

        self.assertEqual(xlsx.status_code, 200)
        self.assertEqual(
            xlsx.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertTrue(xlsx.data.startswith(b"PK"))
        self.assertRegex(
            xlsx.headers["Content-Disposition"],
            r'^attachment; filename="sales-report-\d{4}-\d{2}-\d{2}\.xlsx"$',
        )

        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertRegex(
            pdf.headers["Content-Disposition"],
            r'^attachment; filename="sales-report-\d{4}-\d{2}-\d{2}\.pdf"$',
        )

    def test_report_routes_reject_post(self):
        for path in (
            "/sales/report",
            "/sales/report.xlsx",
            "/sales/report.pdf",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 405)

    def test_extracted_module_does_not_import_monolithic_web_module(self):
        module_directory = Path(web.PROJECT_ROOT) / "app" / "sales_reporting"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in module_directory.glob("*.py")
        )
        self.assertNotIn("from app import web", source)
        self.assertNotIn("from app.web import", source)
        self.assertNotIn("import app.web", source)


if __name__ == "__main__":
    unittest.main()
