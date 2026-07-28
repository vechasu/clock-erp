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


if __name__ == "__main__":
    unittest.main()
