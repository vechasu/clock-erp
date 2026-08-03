import unittest
import re
from pathlib import Path

from app import web


class SidebarComponentTest(unittest.TestCase):
    def setUp(self):
        self.app_root = Path(web.app.root_path)
        self.templates = self.app_root / "templates"
        self.static = self.app_root / "static"

    def active_key_for(self, path):
        return web.get_active_navigation_key(path)

    def test_active_section_is_centralized_for_parent_and_child_routes(self):
        routes = {
            "/": "overview",
            "/overview": "overview",
            "/orders": "orders",
            "/order/42": "orders",
            "/products": "products",
            "/products/42": "products",
            "/warehouse": "products",
            "/warehouse/edit": "products",
            "/sales": "sales",
            "/sales/report": "sales",
            "/receipts": "receipts",
            "/receipts/report": "receipts",
            "/products/receipts/new": "receipts",
            "/products/receipts/drafts/example": "receipts",
            "/analytics": "analytics",
            "/stock-operations": "stock_operations",
            "/repair": "repair",
            "/settings": "settings",
            "/settings/navigation/orders/toggle": "settings",
        }

        for path, expected_key in routes.items():
            with self.subTest(path=path):
                self.assertEqual(
                    self.active_key_for(path),
                    expected_key,
                )

    def test_navigation_definition_drives_desktop_and_mobile_items(self):
        definitions = {
            item["key"]: item
            for item in web.NAVIGATION_DEFINITIONS
        }

        self.assertEqual(
            {
                key
                for key, item in definitions.items()
                if item["mobile_primary"]
            },
            {"products", "sales", "receipts"},
        )
        self.assertEqual(
            definitions["products"]["mobile_href"],
            "/warehouse",
        )
        self.assertTrue(
            all(
                item["group"] in {"main", "system"}
                for item in definitions.values()
            )
        )
        self.assertTrue(
            all(item["icon"] for item in definitions.values())
        )

    def test_primary_sidebar_links_stay_on_legacy_routes(self):
        definitions = {
            item["key"]: item
            for item in web.NAVIGATION_DEFINITIONS
        }

        self.assertEqual(definitions["overview"]["href"], "/overview")
        self.assertEqual(definitions["orders"]["href"], "/orders")
        self.assertEqual(definitions["products"]["href"], "/warehouse")
        self.assertEqual(definitions["sales"]["href"], "/sales")
        self.assertEqual(definitions["receipts"]["href"], "/receipts")
        self.assertEqual(definitions["repair"]["href"], "/repair")
        self.assertEqual(definitions["settings"]["href"], "/settings")
        self.assertFalse(
            any(
                item["href"].startswith("/app/")
                for item in definitions.values()
            )
        )

    def test_all_operational_templates_use_the_shared_component(self):
        operational_templates = {
            "base.html",
            "overview.html",
            "orders.html",
            "warehouse.html",
            "excel_products.html",
            "excel_product_detail.html",
            "excel_receipt_upload.html",
            "excel_receipt_preview.html",
            "excel_receipt_detail.html",
            "sales.html",
            "sales_report.html",
            "receipts.html",
            "receipts_report.html",
            "analytics.html",
            "catalog.html",
            "catalog_detail.html",
            "catalog_import_preview.html",
            "catalog_mapping.html",
            "stock_operations.html",
            "repair.html",
            "settings.html",
        }

        for template_name in operational_templates:
            with self.subTest(template=template_name):
                source = (
                    self.templates / template_name
                ).read_text(encoding="utf-8")
                self.assertEqual(
                    source.count('{% include "_sidebar.html" %}'),
                    1,
                )

    def test_sidebar_has_no_parallel_inline_styles_or_handlers(self):
        sidebar = (
            self.templates / "_sidebar.html"
        ).read_text(encoding="utf-8")

        self.assertNotIn("<style", sidebar)
        self.assertNotIn("addEventListener", sidebar)
        self.assertNotIn("onclick=", sidebar)
        self.assertIn("css/sidebar.css", sidebar)
        self.assertIn("js/sidebar.js", sidebar)
        self.assertIn("_navigation_icons.html", sidebar)

        for template in self.templates.glob("*.html"):
            if template.name == "_sidebar.html":
                continue

            source = template.read_text(encoding="utf-8")
            self.assertNotIn(
                ".sidebar {",
                source,
                f"Локальный sidebar CSS остался в {template.name}",
            )

    def test_shared_css_and_javascript_define_sidebar_behavior(self):
        css = (
            self.static / "css" / "sidebar.css"
        ).read_text(encoding="utf-8")
        javascript = (
            self.static / "js" / "sidebar.js"
        ).read_text(encoding="utf-8")

        for token in (
            "--sidebar-width: 228px",
            "--sidebar-collapsed-width: 72px",
            "--sidebar-item-height: 46px",
            "position: fixed",
            "@media (max-width: 767px)",
            ".mobile-erp-navigation",
        ):
            self.assertIn(token, css)

        self.assertIn(
            'const storageKey = "ttt-erp-sidebar-collapsed"',
            javascript,
        )
        self.assertIn("localStorage.getItem", javascript)
        self.assertIn("localStorage.setItem", javascript)
        self.assertIn('aria-expanded', javascript)
        self.assertIn("initializeMobileMenu", javascript)

    def test_collapsed_sidebar_tooltip_cannot_remain_stuck(self):
        sidebar = (
            self.templates / "_sidebar.html"
        ).read_text(encoding="utf-8")
        css = (
            self.static / "css" / "sidebar.css"
        ).read_text(encoding="utf-8")
        javascript = (
            self.static / "js" / "sidebar.js"
        ).read_text(encoding="utf-8")

        self.assertIn('id="sidebarTooltip"', sidebar)
        self.assertIn('role="tooltip"', sidebar)
        self.assertIn('data-tooltip="{{ item.label }}"', sidebar)
        self.assertNotRegex(
            sidebar,
            re.compile(
                r'class="sidebar-link[^"]*"[^>]*\btitle=',
                re.DOTALL,
            ),
        )
        self.assertIn(".erp-tooltip[hidden]", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn("initializeSidebarTooltip", javascript)
        self.assertIn('root.classList.contains("sidebar-collapsed")', javascript)
        for event_name in (
            "pointerenter",
            "pointerleave",
            "focus",
            "blur",
            "keydown",
            "scroll",
            "pagehide",
            "visibilitychange",
        ):
            self.assertIn(event_name, javascript)
        self.assertIn('event.key === "Escape"', javascript)
        self.assertIn('tooltip.hidden = true', javascript)


if __name__ == "__main__":
    unittest.main()
