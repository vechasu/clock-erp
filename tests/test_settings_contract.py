import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from flask import url_for

from app import web


class SettingsContractTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(web.app.config)
        web.app.config.update(TESTING=True, AUTH_TESTING=False)
        self.temp_directory = tempfile.TemporaryDirectory()
        self.settings_path = (
            Path(self.temp_directory.name) / "settings.json"
        )
        self.path_patch = mock.patch.object(
            web,
            "get_app_settings_path",
            return_value=self.settings_path,
        )
        self.path_patch.start()
        web._load_app_settings_cached.cache_clear()
        self.client = web.app.test_client()

    def tearDown(self):
        web._load_app_settings_cached.cache_clear()
        self.path_patch.stop()
        web.app.config.clear()
        web.app.config.update(self.original_config)
        self.temp_directory.cleanup()

    def write_settings(self, **values):
        payload = {
            "company_name": "Current Company",
            "erp_name": "Current ERP",
            "low_stock_threshold": 3,
            "legacy_marker": {"preserve": True},
            **values,
        }
        self.settings_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        web._load_app_settings_cached.cache_clear()
        return payload

    def read_settings(self):
        return json.loads(self.settings_path.read_text(encoding="utf-8"))

    def test_routes_keep_aliases_methods_and_endpoint_identity(self):
        expected = {
            "/settings": ("settings_page", {"GET", "HEAD", "POST", "OPTIONS"}),
            "/app/settings": (
                "settings_page",
                {"GET", "HEAD", "POST", "OPTIONS"},
            ),
            "/api/v1/settings": (
                "api_settings_resource",
                {"GET", "HEAD", "PATCH", "OPTIONS"},
            ),
        }
        adapter = web.app.url_map.bind("")
        for path, (endpoint, methods) in expected.items():
            with self.subTest(path=path):
                self.assertEqual(adapter.match(path)[0], endpoint)
                rule = next(
                    rule
                    for rule in web.app.url_map.iter_rules()
                    if rule.rule == path and rule.endpoint == endpoint
                )
                self.assertEqual(rule.methods, methods)

        with web.app.test_request_context():
            self.assertEqual(url_for("settings_page"), "/app/settings")
            self.assertEqual(url_for("api_settings_resource"), "/api/v1/settings")

    def test_get_displays_stored_values_and_current_client_only_themes(self):
        self.write_settings()

        response = self.client.get("/settings")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn('value="Current Company"', body)
        self.assertIn('value="Current ERP"', body)
        self.assertIn('value="3"', body)
        for theme in ("classic", "klok-green", "bn0024-white"):
            self.assertIn('data-theme-option="{}"'.format(theme), body)
        self.assertNotIn('name="theme"', body)
        self.assertIn('name="csrf_token"', body)

    def test_post_saves_all_fields_clamps_threshold_and_preserves_unknown_keys(self):
        self.write_settings()

        response = self.client.post(
            "/settings",
            data={
                "company_name": "  New Company  ",
                "erp_name": "  New ERP  ",
                "low_stock_threshold": "1001",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        with web.app.test_request_context():
            expected_location = url_for(
                "settings_page",
                notice="success",
                message="Настройки сохранены",
            )
        self.assertEqual(response.headers["Location"], expected_location)
        self.assertEqual(
            self.read_settings(),
            {
                "company_name": "New Company",
                "erp_name": "New ERP",
                "low_stock_threshold": 999,
                "legacy_marker": {"preserve": True},
            },
        )
        subsequent = self.client.get("/app/settings").get_data(as_text=True)
        self.assertIn('value="New Company"', subsequent)
        self.assertIn('value="New ERP"', subsequent)
        self.assertIn('value="999"', subsequent)

    def test_post_missing_and_malformed_values_use_existing_fallbacks(self):
        self.write_settings()
        response = self.client.post(
            "/app/settings",
            data={"low_stock_threshold": "not-an-integer"},
        )

        self.assertEqual(response.status_code, 302)
        saved = self.read_settings()
        self.assertEqual(saved["company_name"], "Tictactoy")
        self.assertEqual(saved["erp_name"], "Vechasu ERP")
        self.assertEqual(saved["low_stock_threshold"], 0)
        self.assertEqual(saved["legacy_marker"], {"preserve": True})

    def test_api_get_and_partial_patch_preserve_storage_contract(self):
        self.write_settings()
        current = self.client.get("/api/v1/settings")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.get_json()["data"]["legacy_marker"], {"preserve": True})

        changed = self.client.patch(
            "/api/v1/settings",
            json={"erp_name": "  API ERP  ", "low_stock_threshold": -4},
        )
        payload = changed.get_json()

        self.assertEqual(changed.status_code, 200)
        self.assertEqual(payload["meta"]["changed_fields"], [
            "erp_name",
            "low_stock_threshold",
        ])
        self.assertEqual(payload["data"]["company_name"], "Current Company")
        self.assertEqual(payload["data"]["erp_name"], "API ERP")
        self.assertEqual(payload["data"]["low_stock_threshold"], 0)
        self.assertEqual(payload["data"]["legacy_marker"], {"preserve": True})
        self.assertEqual(self.read_settings(), payload["data"])

    def test_api_validation_errors_and_noop_do_not_write(self):
        self.write_settings()
        original = self.settings_path.read_bytes()

        unknown = self.client.patch(
            "/api/v1/settings",
            json={"theme": "classic"},
        )
        invalid = self.client.patch(
            "/api/v1/settings",
            json={"low_stock_threshold": "invalid"},
        )
        noop = self.client.patch(
            "/api/v1/settings",
            json={"erp_name": "Current ERP"},
        )

        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(unknown.get_json()["code"], "SETTINGS_VALIDATION_FAILED")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["code"], "SETTINGS_VALIDATION_FAILED")
        self.assertEqual(noop.status_code, 200)
        self.assertEqual(noop.get_json()["meta"]["changed_fields"], [])
        self.assertEqual(self.settings_path.read_bytes(), original)

    def test_storage_failure_propagates_without_redirect(self):
        self.write_settings()
        with mock.patch.object(
            web,
            "save_app_settings",
            side_effect=OSError("simulated storage failure"),
        ):
            with web.app.test_request_context(
                "/settings",
                method="POST",
                data={
                    "company_name": "New Company",
                    "erp_name": "New ERP",
                    "low_stock_threshold": "4",
                },
            ):
                with self.assertRaisesRegex(OSError, "simulated storage failure"):
                    web.settings_page()

    def test_each_settings_request_loads_storage_once(self):
        self.write_settings()
        with mock.patch.object(
            web,
            "load_app_settings",
            wraps=web.load_app_settings,
        ) as loader:
            self.client.get("/settings")
            # Branding in the shared sidebar is static; the page owns the only load.
            self.assertEqual(loader.call_count, 1)

            loader.reset_mock()
            self.client.post(
                "/settings",
                data={
                    "company_name": "New Company",
                    "erp_name": "New ERP",
                    "low_stock_threshold": "5",
                },
            )
            self.assertEqual(loader.call_count, 1)

            loader.reset_mock()
            self.client.patch(
                "/api/v1/settings",
                json={"erp_name": "Another ERP"},
            )
            self.assertEqual(loader.call_count, 1)

    def test_extracted_module_does_not_import_monolithic_web_module(self):
        module_directory = Path(web.PROJECT_ROOT) / "app" / "system_settings"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in module_directory.glob("*.py")
        )
        self.assertNotIn("from app import web", source)
        self.assertNotIn("from app.web import", source)
        self.assertNotIn("import app.web", source)


if __name__ == "__main__":
    unittest.main()
