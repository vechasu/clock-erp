import unittest
from pathlib import Path


class CatalogCascadeUnificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def source(self, relative_path):
        return (self.root / relative_path).read_text(encoding="utf-8")

    def test_visible_sections_use_one_physical_combobox_component(self):
        macro = self.source("app/templates/_catalog_combobox.html")
        shared_script = self.source(
            "app/static/js/catalog-combobox.js"
        )

        self.assertEqual(
            macro.count("{% macro render_catalog_combobox"),
            1,
        )
        self.assertEqual(
            macro.count("{% macro render_catalog_create_modal"),
            1,
        )
        self.assertEqual(
            shared_script.count(
                "(function initializeSharedCatalogCascades()"
            ),
            1,
        )
        self.assertEqual(
            shared_script.count(
                '"/api/v1/catalog/options?"'
            ),
            1,
        )

        for page in (
            "app/templates/warehouse.html",
            "app/templates/sales.html",
            "app/templates/receipts.html",
        ):
            source = self.source(page)
            self.assertIn(
                'filename=\'js/catalog-combobox.js\'',
                source,
            )
            self.assertIn("render_catalog_combobox(", source)
            self.assertIn("data-shared-catalog-scope", source)
            self.assertIn('shared_catalog_kind="brand"', source)
            self.assertIn('shared_catalog_kind="category"', source)

        for page in (
            "app/templates/sales.html",
            "app/templates/receipts.html",
        ):
            self.assertIn(
                'shared_catalog_kind="product"',
                self.source(page),
            )

    def test_receipts_and_sales_have_no_local_catalog_arrays(self):
        for page in (
            "app/templates/sales.html",
            "app/templates/receipts.html",
        ):
            source = self.source(page)
            self.assertNotIn("receiptProducts", source)
            self.assertNotIn("receiptCatalogCategories", source)
            self.assertNotIn("const warehouseItems", source)
            self.assertIn(
                "window.restoreSharedCatalogCascade",
                source,
            )

    def test_shared_component_owns_search_cascade_and_creation(self):
        component = self.source(
            "app/static/js/catalog-combobox.js"
        )
        styles = self.source(
            "app/static/css/catalog-combobox.css"
        )
        macro = self.source(
            "app/templates/_catalog_combobox.html"
        )

        self.assertIn("window.queueSharedCatalogSearch", component)
        self.assertIn("window.loadSharedCatalogOptions", component)
        self.assertIn("const searchTimers = new WeakMap()", component)
        self.assertIn("const requestControllers = new WeakMap()", component)
        self.assertIn("resetCascadeAfter", component)
        self.assertIn("shared-catalog:selected", component)
        self.assertIn("Загрузка…", component)
        self.assertIn("Ничего не найдено", component)
        self.assertIn("ArrowDown", component)
        self.assertIn("ArrowUp", component)
        self.assertIn("data-catalog-create-action", macro)
        self.assertIn(".catalog-create-modal", styles)

    def test_visible_section_routes_keep_the_approved_legacy_interface(self):
        web = self.source("app/web.py")
        products_route = web.split('@app.route("/products")', 1)[1].split(
            "@app.route(", 1
        )[0]
        sales_route = web.split('@app.route("/sales")', 1)[1].split(
            "@app.route(", 1
        )[0]
        receipts_route = web.split('@app.route("/receipts")', 1)[1].split(
            "@app.route(", 1
        )[0]
        self.assertIn('url_for("warehouse_page")', products_route)
        self.assertIn('render_template(\n        "sales.html"', sales_route)
        self.assertIn('render_template(\n        "receipts.html"', receipts_route)
        self.assertNotIn("react_application(", products_route)
        self.assertNotIn("react_application(", sales_route)
        self.assertNotIn("react_application(", receipts_route)

    def test_receipts_describe_erp_as_the_stock_system(self):
        react_page = self.source(
            "frontend/src/features/receipts/ReceiptsPage.tsx"
        )
        legacy_page = self.source("app/templates/receipts.html")
        stale_text = "После проведения остатки увеличатся в МойСклад"
        self.assertNotIn(stale_text, react_page)
        self.assertNotIn(stale_text, legacy_page)
        self.assertIn(
            "После проведения остатки изменятся в единой ERP",
            react_page,
        )
        self.assertIn(
            "После проведения остатки изменятся в единой ERP",
            legacy_page,
        )

    def test_receipt_form_uses_multipart_and_inline_api_feedback(self):
        page = self.source("app/templates/receipts.html")
        submit_script = self.source(
            "app/static/js/receipt-submit.js"
        )

        self.assertIn(
            "filename='js/receipt-submit.js'",
            page,
        )
        self.assertIn(
            "VechasuReceiptSubmit.buildCreatePayload",
            page,
        )
        self.assertNotIn("alert(", page)
        self.assertNotIn("receiptImagePayload", page)
        self.assertIn("new global.FormData(form)", submit_script)
        self.assertNotIn('"Content-Type": "application/json"', submit_script)
        self.assertIn("payload.message", submit_script)
        self.assertIn('query.set("open_receipt_modal", "1")', submit_script)


if __name__ == "__main__":
    unittest.main()
