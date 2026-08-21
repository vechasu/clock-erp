import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SettingsDesignSystemContractTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_settings_uses_shared_components_without_inline_css(self):
        template = self.source("app/templates/settings.html")
        invitations = self.source("app/templates/_employee_invitations.html")
        self.assertNotIn("<style", template)
        self.assertNotIn("<style", invitations)
        for contract in (
            "settings-header erp-workspace-header",
            "settings-card settings-form",
            "control erp-control",
            "button erp-primary-action",
            "notice erp-notice",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, template)

    def test_one_settings_form_keeps_fields_csrf_and_primary_action(self):
        template = self.source("app/templates/settings.html")
        self.assertEqual(template.count('id="settingsForm"'), 1)
        settings_form = template.split('id="settingsForm"', 1)[1].split("</form>", 1)[0]
        self.assertEqual(settings_form.count('type="submit"'), 1)
        self.assertIn('name="csrf_token"', settings_form)
        for field in ("company_name", "erp_name", "low_stock_threshold"):
            with self.subTest(field=field):
                self.assertIn('name="{}"'.format(field), settings_form)
                self.assertIn('data-settings-error="{}"'.format(field), settings_form)
        self.assertIn('min="0"', settings_form)
        self.assertIn('max="999"', settings_form)

    def test_theme_names_storage_aria_and_keyboard_contract_are_preserved(self):
        template = self.source("app/templates/settings.html")
        theme_script = self.source("app/static/js/theme.js")
        for theme in ("classic", "klok-green", "bn0024-white"):
            self.assertIn('data-theme-option="{}"'.format(theme), template)
        self.assertEqual(template.count('role="radio"'), 3)
        self.assertEqual(template.count('aria-checked="false"'), 3)
        self.assertIn('const STORAGE_KEY = "vechasu-erp-theme-v1";', theme_script)
        self.assertIn('option.tabIndex = selected ? 0 : -1;', theme_script)
        self.assertIn('"ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"', theme_script)
        self.assertIn("applyTheme(nextOption.dataset.themeOption)", theme_script)

    def test_save_states_errors_and_double_submit_guard_are_explicit(self):
        script = self.source("app/static/js/settings.js")
        for marker in (
            "var saving = false;",
            "if (saving)",
            'submit.disabled = true;',
            'form.setAttribute("aria-busy", "true")',
            'submit.textContent = "Сохраняем…"',
            'showNotice(form, "Настройки сохранены", "success")',
            'showNotice(form, error.message, "error")',
            'input.setAttribute("aria-invalid", "true")',
            "payload.fields || []",
            'response.json().catch(function ()',
            "Сервер не смог сохранить настройки. Повторите позже.",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, script)

    def test_settings_css_is_tokenized_responsive_and_overflow_safe(self):
        css = self.source("app/static/css/erp-components.css")
        contract = css.split("Settings workspace: shared ERP component contract.", 1)[1]
        for token in (
            "var(--erp-canvas)",
            "var(--erp-surface)",
            "var(--erp-border)",
            "var(--erp-primary)",
            "var(--erp-focus-ring)",
            "var(--erp-danger)",
        ):
            self.assertIn(token, contract)
        self.assertIn("overflow-x: hidden", contract)
        self.assertIn("overflow-wrap: anywhere", contract)
        self.assertIn("@media (max-width: 900px)", contract)
        self.assertIn("@media (max-width: 767px)", contract)
        self.assertIn("@media (max-width: 370px)", contract)
        self.assertIn("position: sticky", contract)
        self.assertIn("width: 100%", contract)


if __name__ == "__main__":
    unittest.main()
