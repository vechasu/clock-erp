import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import web


class NavigationSettingsTest(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_directory.name)
        self.navigation_path = (
            self.temp_path / "navigation_settings.json"
        )
        self.app_settings_path = self.temp_path / "settings.json"

        self.navigation_path_patcher = mock.patch.object(
            web,
            "get_navigation_settings_path",
            return_value=self.navigation_path,
        )
        self.app_settings_path_patcher = mock.patch.object(
            web,
            "get_app_settings_path",
            return_value=self.app_settings_path,
        )

        self.navigation_path_patcher.start()
        self.app_settings_path_patcher.start()

    def tearDown(self):
        web._load_navigation_settings_cached.cache_clear()
        web._LAST_SAFE_NAVIGATION_SETTINGS.pop(
            str(self.navigation_path),
            None,
        )
        self.navigation_path_patcher.stop()
        self.app_settings_path_patcher.stop()
        self.temp_directory.cleanup()

    def seed_navigation(self, **changes):
        settings = web.get_default_navigation_settings()

        settings["orders"]["position"] = 11
        settings["products"] = {
            "enabled": False,
            "position": 17,
        }

        for key, values in changes.items():
            settings[key].update(values)

        self.navigation_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return settings

    def read_navigation(self):
        return json.loads(
            self.navigation_path.read_text(encoding="utf-8")
        )

    def toggle_orders(self):
        return self.client.post(
            "/settings/navigation/orders/toggle"
        )

    @staticmethod
    def sidebar_html(response):
        html = response.get_data(as_text=True)
        return html.split('<aside', 1)[1].split('</aside>', 1)[0]

    @staticmethod
    def sidebar_keys(response):
        return re.findall(
            r'class="sidebar-link[^>]*?data-navigation-key="([^"]+)"',
            NavigationSettingsTest.sidebar_html(response),
            re.DOTALL,
        )

    def test_root_redirects_to_overview(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/overview")

    def test_overview_returns_200_and_is_active_in_sidebar(self):
        response = self.client.get("/overview")
        sidebar = self.sidebar_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            sidebar,
            r'class="sidebar-link active"[^>]*href="/overview"',
        )
        self.assertIn('aria-current="page"', sidebar)

    def test_overview_is_enabled_by_default_and_available_in_settings(self):
        defaults = web.get_default_navigation_settings()
        response = self.client.get("/settings")

        self.assertTrue(defaults["overview"]["enabled"])
        self.assertIn(
            'action="/settings/navigation/overview/toggle"',
            response.get_data(as_text=True),
        )

    def test_default_main_menu_order_matches_navigation_contract(self):
        with web.app.test_request_context("/overview"):
            keys = [
                item["key"]
                for item in web.get_navigation_items()
                if item["group"] == "main"
            ]

        self.assertEqual(
            keys,
            [
                "overview",
                "orders",
                "products",
                "catalog",
                "sales",
                "receipts",
                "analytics",
                "stock_operations",
                "repair",
            ],
        )

    def test_settings_sidebar_uses_enabled_navigation_on_get(self):
        self.seed_navigation(
            repair={"enabled": False, "position": 9},
            sales={"enabled": True, "position": 5},
        )

        response = self.client.get("/settings")
        keys = self.sidebar_keys(response)

        self.assertNotIn("repair", keys)
        self.assertIn("sales", keys)
        self.assertIn("settings", keys)

    def test_settings_sidebar_uses_saved_navigation_after_post(self):
        self.seed_navigation(
            repair={"enabled": False, "position": 9},
            sales={"enabled": True, "position": 5},
        )

        response = self.client.post(
            "/settings",
            data={
                "company_name": "Tictactoy",
                "erp_name": "Vechasu ERP",
                "low_stock_threshold": "3",
            },
            follow_redirects=True,
        )
        keys = self.sidebar_keys(response)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("repair", keys)
        self.assertIn("sales", keys)
        self.assertIn("settings", keys)

    def test_navigation_toggle_applies_immediately_and_survives_reload(self):
        self.seed_navigation(repair={"enabled": True, "position": 9})

        response = self.client.post(
            "/settings/navigation/repair/toggle",
            follow_redirects=True,
        )
        reloaded = self.client.get("/settings")

        self.assertNotIn("repair", self.sidebar_keys(response))
        self.assertNotIn("repair", self.sidebar_keys(reloaded))
        self.assertFalse(self.read_navigation()["repair"]["enabled"])

    def test_menu_order_is_shared_across_primary_jinja_pages(self):
        self.seed_navigation()
        expected = None

        for path in (
            "/warehouse",
            "/sales",
            "/receipts",
            "/repair",
            "/settings",
            "/overview",
        ):
            with self.subTest(path=path), web.app.test_request_context(path):
                keys = [item["key"] for item in web.get_navigation_items()]
                if expected is None:
                    expected = keys
                self.assertEqual(keys, expected)

    def test_primary_jinja_routes_do_not_redirect_to_react_app(self):
        endpoints = {
            rule.rule: rule.endpoint
            for rule in web.app.url_map.iter_rules()
        }

        for path in (
            "/overview",
            "/warehouse",
            "/sales",
            "/receipts",
            "/repair",
            "/settings",
        ):
            with self.subTest(path=path):
                self.assertNotEqual(endpoints[path], "react_app")

    def test_orders_toggle_disables_and_saves_immediately(self):
        self.seed_navigation()

        page = self.client.get("/settings")
        response = self.toggle_orders()
        saved = self.read_navigation()

        self.assertEqual(page.status_code, 200)
        self.assertIn(
            b'action="/settings/navigation/orders/toggle"',
            page.data,
        )
        self.assertNotIn(b'name="navigation_orders"', page.data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(saved["orders"]["enabled"])
        self.assertEqual(saved["orders"]["position"], 11)

    def test_orders_toggle_supports_inline_json_without_page_reload(self):
        self.seed_navigation()

        response = self.client.post(
            "/settings/navigation/orders/toggle",
            headers={"Accept": "application/json"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["data"],
            {
                "key": "orders",
                "label": "Заказы",
                "enabled": False,
            },
        )
        self.assertFalse(self.read_navigation()["orders"]["enabled"])

    def test_orders_toggle_preserves_other_navigation_items(self):
        before = self.seed_navigation(
            sales={"enabled": False, "position": 23},
            repair={"enabled": True, "position": 4},
        )

        self.toggle_orders()
        after = self.read_navigation()

        for key in before:
            if key != "orders":
                self.assertEqual(after[key], before[key])

    def test_settings_remains_enabled(self):
        self.seed_navigation(
            settings={"enabled": False, "position": 29},
        )

        self.toggle_orders()

        self.assertTrue(
            self.read_navigation()["settings"]["enabled"]
        )

    def test_disabled_orders_are_absent_from_navigation_items(self):
        self.seed_navigation()
        self.toggle_orders()

        with web.app.test_request_context("/"):
            keys = {
                item["key"]
                for item in web.get_navigation_items()
            }

        self.assertNotIn("orders", keys)
        self.assertIn("settings", keys)

    def test_second_orders_toggle_enables_section_again(self):
        self.seed_navigation()

        self.toggle_orders()
        self.toggle_orders()

        self.assertTrue(
            self.read_navigation()["orders"]["enabled"]
        )

    def test_company_settings_save_preserves_disabled_orders(self):
        before = self.seed_navigation(
            orders={"enabled": False, "position": 11},
            analytics={"enabled": False, "position": 31},
        )

        response = self.client.post(
            "/settings",
            data={
                "company_name": "Tictactoy Test",
                "erp_name": "Vechasu Test",
                "low_stock_threshold": "7",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.read_navigation(), before)
        self.assertEqual(
            json.loads(
                self.app_settings_path.read_text(
                    encoding="utf-8"
                )
            ),
            {
                "company_name": "Tictactoy Test",
                "erp_name": "Vechasu Test",
                "low_stock_threshold": 7,
            },
        )

    def test_company_settings_save_preserves_unknown_app_settings(self):
        self.app_settings_path.write_text(
            json.dumps(
                {
                    "company_name": "До изменения",
                    "erp_name": "Старая ERP",
                    "low_stock_threshold": 2,
                    "future_user_setting": {
                        "enabled": True,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        response = self.client.post(
            "/settings",
            data={
                "company_name": "После изменения",
                "erp_name": "Новая ERP",
                "low_stock_threshold": "8",
            },
        )
        saved = json.loads(
            self.app_settings_path.read_text(encoding="utf-8")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            saved["future_user_setting"],
            {"enabled": True},
        )
        self.assertEqual(saved["company_name"], "После изменения")

    def test_settings_api_saves_only_changed_fields_without_reload(self):
        self.app_settings_path.write_text(
            json.dumps(
                {
                    "company_name": "Tictactoy",
                    "erp_name": "Vechasu ERP",
                    "low_stock_threshold": 3,
                    "future_user_setting": {"enabled": True},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        response = self.client.patch(
            "/api/v1/settings",
            json={"low_stock_threshold": 7},
        )
        saved = json.loads(
            self.app_settings_path.read_text(encoding="utf-8")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["meta"]["changed_fields"],
            ["low_stock_threshold"],
        )
        self.assertEqual(saved["low_stock_threshold"], 7)
        self.assertEqual(saved["future_user_setting"], {"enabled": True})

    def test_disabled_primary_item_is_absent_from_shared_navigation(self):
        self.seed_navigation(
            products={"enabled": False, "position": 17},
        )

        with web.app.test_request_context("/sales"):
            items = web.get_navigation_items()

        self.assertNotIn(
            "products",
            {item["key"] for item in items},
        )
        self.assertTrue(
            next(
                item
                for item in items
                if item["key"] == "sales"
            )["mobile_primary"]
        )

    def test_visibility_is_shared_by_every_primary_legacy_route(self):
        self.seed_navigation(
            repair={"enabled": False, "position": 7},
            sales={"enabled": True, "position": 3},
        )

        for path in (
            "/warehouse",
            "/sales",
            "/receipts",
            "/repair",
            "/settings",
        ):
            with self.subTest(path=path), web.app.test_request_context(path):
                items = web.get_navigation_items()
                keys = {item["key"] for item in items}
                self.assertNotIn("repair", keys)
                self.assertIn("sales", keys)

    def test_invalid_navigation_json_is_logged_before_defaulting(self):
        self.navigation_path.write_text("{invalid", encoding="utf-8")
        web._load_navigation_settings_cached.cache_clear()

        with self.assertLogs(web.app.logger.name, level="WARNING") as logs:
            settings = web.load_navigation_settings()

        self.assertFalse(settings["products"]["enabled"])
        self.assertTrue(settings["settings"]["enabled"])
        self.assertTrue(
            any(
                "Failed to load navigation settings" in message
                and "last safe navigation settings will be used" in message
                for message in logs.output
            )
        )

    def test_invalid_navigation_json_keeps_last_safe_visibility(self):
        self.seed_navigation(
            repair={"enabled": False, "position": 9},
            sales={"enabled": True, "position": 5},
        )
        web._load_navigation_settings_cached.cache_clear()
        safe_settings = web.load_navigation_settings()

        self.navigation_path.write_text("{invalid", encoding="utf-8")
        web._load_navigation_settings_cached.cache_clear()
        with self.assertLogs(web.app.logger.name, level="WARNING"):
            fallback_settings = web.load_navigation_settings()

        self.assertFalse(safe_settings["repair"]["enabled"])
        self.assertFalse(fallback_settings["repair"]["enabled"])
        self.assertTrue(fallback_settings["sales"]["enabled"])


if __name__ == "__main__":
    unittest.main()
