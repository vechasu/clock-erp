import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GlobalNotificationSystemTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_every_erp_page_loads_one_global_manager(self):
        sidebar = self.source("app/templates/_sidebar.html")
        self.assertEqual(sidebar.count("js/notifications.js"), 1)
        self.assertEqual(sidebar.count("css/notifications.css"), 1)
        for template in (
            "warehouse.html",
            "sales.html",
            "receipts.html",
            "settings.html",
            "sales_report.html",
            "receipts_report.html",
            "excel_receipt_upload.html",
            "excel_receipt_preview.html",
            "excel_receipt_detail.html",
        ):
            with self.subTest(template=template):
                self.assertIn(
                    '{% include "_sidebar.html" %}',
                    self.source("app/templates/" + template),
                )

    def test_manager_exposes_required_lifecycle_api(self):
        script = self.source("app/static/js/notifications.js")
        for member in (
            "success:",
            "error:",
            "warning:",
            "info:",
            "loading:",
            "update,",
            "dismiss,",
        ):
            self.assertIn(member, script)
        self.assertIn("const MAX_VISIBLE = 3", script)
        self.assertIn("DEFAULT_LIFETIME", script)
        self.assertIn("action: settings.action", script)
        self.assertIn("sessionStorage", script)

    def test_mutations_only_report_success_after_backend_response(self):
        script = self.source("app/static/js/notifications.js")
        loading_index = script.index('show("loading"')
        fetch_index = script.index("response = await originalFetch")
        success_index = script.index('update(notificationId, {kind: "success"')
        self.assertLess(loading_index, fetch_index)
        self.assertLess(fetch_index, success_index)
        self.assertIn("if (!response.ok || (payload && payload.error))", script)
        self.assertIn("response.status === 207", script)
        self.assertIn("controller.abort()", script)
        self.assertIn('activeButton.disabled = true', script)
        self.assertIn('activeButton.disabled = priorDisabled', script)

    def test_errors_are_humanized_and_internal_details_are_filtered(self):
        script = self.source("app/static/js/notifications.js")
        for status in (401, 403, 404, 409, 413, 422, 400):
            self.assertIn("response.status === {}".format(status), script)
        self.assertIn("response.status >= 500", script)
        for marker in ("traceback", "integrityerror", "sqlite", "sqlalchemy"):
            self.assertIn(marker, script.lower())
        web = self.source("app/web.py")
        self.assertIn("def api_internal_server_error(error):", web)
        self.assertIn('"INTERNAL_ERROR"', web)

    def test_accessibility_modal_layer_and_mobile_contract(self):
        script = self.source("app/static/js/notifications.js")
        css = self.source("app/static/css/notifications.css")
        self.assertIn('toast.setAttribute("role", item.kind === "error" ? "alert" : "status")', script)
        self.assertIn('close.setAttribute("aria-label", "Закрыть уведомление")', script)
        self.assertIn("z-index: 10050", css)
        self.assertIn("@media (max-width: 600px)", css)
        self.assertIn("calc(100vw - 36px)", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn("prefers-reduced-motion: reduce", css)

    def test_redirect_notices_are_migrated_without_parallel_banner(self):
        for template in (
            "warehouse.html",
            "sales.html",
            "receipts.html",
            "settings.html",
        ):
            self.assertIn(
                "data-erp-notification-source",
                self.source("app/templates/" + template),
            )
        css = self.source("app/static/css/notifications.css")
        self.assertIn('data-erp-notification-consumed="true"', css)


if __name__ == "__main__":
    unittest.main()
