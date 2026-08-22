import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class JournalDesignSystemContractTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_journal_uses_shared_components_without_inline_css(self):
        template = self.source("app/templates/journal.html")
        self.assertNotIn("<style", template)
        for contract in (
            "journal-header erp-workspace-header",
            "journal-tabs erp-section-tabs",
            "journal-tab erp-section-tab",
            "journal-toolbar erp-toolbar",
            "journal-control erp-control",
            "journal-empty {{ 'erp-no-results-state' if has_query_state else 'erp-empty-state' }}",
            "journal-loading erp-loading-state",
            "journal-error erp-error-state",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, template)

    def test_journal_component_css_is_tokenized_and_responsive(self):
        css = self.source("app/static/css/erp-components.css")
        contract = css.split("Journal workspace: shared ERP component contract.", 1)[1]
        for token in (
            "var(--erp-canvas)",
            "var(--erp-surface)",
            "var(--erp-border)",
            "var(--erp-primary)",
            "var(--erp-focus-ring)",
            "var(--erp-z-drawer)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, contract)
        self.assertIn("overflow-x: hidden", contract)
        self.assertIn("overflow-wrap: anywhere", contract)
        self.assertIn("@media (max-width: 900px)", contract)
        self.assertIn("@media (max-width: 600px)", contract)
        self.assertIn("@media (max-width: 370px)", contract)
        self.assertIn("grid-row: 1", contract)
        self.assertIn("grid-row: 2", contract)

    def test_filters_pagination_states_and_keyboard_contract_are_explicit(self):
        template = self.source("app/templates/journal.html")
        for marker in (
            "journal-active-filters",
            "data-journal-clear",
            'url.searchParams.delete("cursor")',
            "new URLSearchParams(window.location.search)",
            'url.searchParams.set("cursor", more.dataset.cursor)',
            'more.textContent = "Загружаем…"',
            'aria-modal="true"',
            'role="alert"',
            'role="status"',
            'event.key !== "Tab"',
            "drawerReturnFocus",
            "window.VechasuNotify",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, template)


if __name__ == "__main__":
    unittest.main()
