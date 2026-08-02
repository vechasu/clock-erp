import json
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

        self.assertTrue(settings["products"]["enabled"])
        self.assertTrue(
            any(
                "Failed to load navigation settings" in message
                and "default navigation will be used" in message
                for message in logs.output
            )
        )


if __name__ == "__main__":
    unittest.main()
