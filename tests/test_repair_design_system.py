import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepairDesignSystemContractTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_repair_uses_shared_components_without_inline_css(self):
        template = self.source("app/templates/repair.html")
        self.assertNotIn("<style", template)
        for contract in (
            "repair-header erp-workspace-header",
            "repair-tabs erp-section-tabs",
            "repair-tab erp-section-tab",
            "repair-toolbar erp-toolbar",
            "repair-control erp-control",
            "repair-table erp-data-table",
            "repair-table-wrap erp-table-scroll",
            "repair-empty erp-empty-state",
            "repair-error erp-error-state",
            "repair-loading erp-loading-state",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, template)

    def test_repair_component_css_is_tokenized_and_responsive(self):
        css = self.source("app/static/css/erp-components.css")
        contract = css.split("Repair workspace: shared ERP component contract.", 1)[1]
        for token in (
            "var(--erp-canvas)",
            "var(--erp-surface)",
            "var(--erp-border)",
            "var(--erp-primary)",
            "var(--erp-focus-ring)",
            "var(--erp-z-drawer)",
            "var(--erp-z-notification)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, contract)
        self.assertIn(".repair-table-wrap {\n    max-width: 100%;\n    overflow-x: auto;", contract)
        self.assertIn("@media (max-width: 900px)", contract)
        self.assertIn("@media (max-width: 600px)", contract)
        self.assertIn("@media (max-width: 370px)", contract)
        self.assertIn("min-width: 0; flex: 1 1 calc(33.333% - 6px)", contract)

    def test_repair_states_and_keyboard_contract_are_explicit(self):
        template = self.source("app/templates/repair.html")
        for marker in (
            'aria-current="page"',
            'aria-modal="true"',
            'role="alert"',
            'role="status"',
            "event.key !== 'Tab'",
            "drawerReturnFocus",
            "button.textContent = 'Выполняем…'",
            "submit.textContent='Сохраняем…'",
            "window.VechasuNotify",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, template)


if __name__ == "__main__":
    unittest.main()
