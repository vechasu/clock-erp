import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ErpSystemStatesContractTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_shared_layer_is_loaded_once_in_erp_and_auth_shells(self):
        sidebar = self.source("app/templates/_sidebar.html")
        auth = self.source("app/templates/auth_base.html")
        for asset in ("css/erp-states.css", "js/erp-states.js"):
            self.assertEqual(sidebar.count(asset), 1)
            self.assertEqual(auth.count(asset), 1)

    def test_shared_visual_contract_covers_every_system_state(self):
        css = self.source("app/static/css/erp-states.css")
        for selector in (
            ".erp-loading-state",
            ".erp-empty-state",
            ".erp-no-results-state",
            ".erp-error-state",
            ".erp-success-state",
            ".is-erp-pending",
            ".erp-form-error",
            "button:disabled",
        ):
            self.assertIn(selector, css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("overflow-wrap: anywhere", css)

    def test_pending_is_explicit_and_does_not_intercept_normal_forms(self):
        states = self.source("app/static/js/erp-states.js")
        notifications = self.source("app/static/js/notifications.js")
        self.assertIn('[data-erp-pending], [data-single-submit]', states)
        self.assertIn('form.method.toUpperCase() === "GET"', states)
        self.assertIn("event.defaultPrevented", states)
        self.assertIn("event.preventDefault();", states)
        self.assertNotIn('document.addEventListener("submit"', notifications)
        self.assertNotIn("global.fetch =", states)

    def test_pending_lifecycle_restores_after_bfcache_and_async_errors(self):
        states = self.source("app/static/js/erp-states.js")
        self.assertIn('global.addEventListener("pageshow"', states)
        self.assertIn("finally {", states)
        self.assertIn("pendingForms.delete(form)", states)
        self.assertIn("delete form.dataset.submitting", states)
        self.assertIn('button.removeAttribute("aria-busy")', states)

    def test_mutating_workflows_opt_in_with_contextual_labels(self):
        expectations = {
            "orders.html": ("Сохраняем статус…", "Сопоставляем…", "Проводим продажу…"),
            "sales.html": ("Проводим продажу…", "Отменяем продажу…", "Проводим возврат…"),
            "receipts.html": ("Проводим приход…", "Сохраняем приход…", "Отменяем приход…"),
            "warehouse.html": ("Сохраняем товар…", "Удаляем товар…"),
            "settings.html": ("Сохраняем настройки…",),
            "login.html": ("Входим…",),
            "register.html": ("Создаём аккаунт…",),
        }
        for template, labels in expectations.items():
            source = self.source("app/templates/" + template)
            self.assertIn("data-erp-pending", source, template)
            for label in labels:
                self.assertIn(label, source, template)

    def test_empty_and_no_results_are_distinct_on_real_pages(self):
        for template in (
            "warehouse.html",
            "orders.html",
            "receipts.html",
            "repair.html",
            "journal.html",
            "sales.html",
        ):
            source = self.source("app/templates/" + template)
            self.assertIn("erp-empty-state", source, template)
            self.assertIn("erp-no-results-state", source, template)
        for template in ("warehouse.html", "orders.html", "receipts.html", "repair.html", "journal.html"):
            self.assertRegex(
                self.source("app/templates/" + template),
                r"Сбросить (фильтры|все)|Очистить поиск",
                template,
            )

    def test_read_only_failures_have_safe_retry_and_live_regions(self):
        for template in ("warehouse.html", "warehouse_brands.html", "warehouse_categories.html", "warehouse_inventory.html", "repair.html"):
            source = self.source("app/templates/" + template)
            self.assertIn("erp-error-state", source, template)
            self.assertRegex(source, r"Повтор(ить|ная)", template)
        self.assertIn('role="alert"', self.source("app/templates/repair.html"))
        self.assertIn('aria-live="polite"', self.source("app/templates/warehouse.html"))

    def test_inline_and_form_errors_remain_close_to_fields(self):
        register = self.source("app/templates/register.html")
        settings = self.source("app/templates/settings.html")
        warehouse = self.source("app/templates/warehouse.html")
        self.assertIn("aria-describedby=\"{{ field_id }}_error\"", register)
        self.assertIn("data-settings-error", settings)
        self.assertIn('id="addStockError"', warehouse)
        self.assertIn('id="warehouseAddError"', warehouse)

    def test_disabled_controls_explain_non_obvious_reason(self):
        register = self.source("app/templates/register.html")
        orders = self.source("app/templates/orders.html")
        self.assertIn('aria-describedby="registerDisabledReason"', register)
        self.assertIn('id="registerDisabledReason"', register)
        self.assertIn("Проведение недоступно:", orders)


if __name__ == "__main__":
    unittest.main()
