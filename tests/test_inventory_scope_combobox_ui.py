from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InventoryScopeComboboxUiTest(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT / "app/templates/warehouse_inventory.html").read_text(
            encoding="utf-8"
        )
        self.component = (ROOT / "app/templates/_catalog_combobox.html").read_text(
            encoding="utf-8"
        )
        self.script = (ROOT / "app/static/js/catalog-combobox.js").read_text(
            encoding="utf-8"
        )
        self.styles = (ROOT / "app/static/css/catalog-combobox.css").read_text(
            encoding="utf-8"
        )

    def test_inventory_uses_one_searchable_floating_component(self):
        self.assertEqual(self.template.count("inventory-scope-combobox"), 3)
        self.assertEqual(self.template.count("floating_layer=true"), 3)
        for placeholder in (
            "Поиск бренда...",
            "Поиск категории...",
            "Поиск модели...",
        ):
            self.assertIn(placeholder, self.template)
        self.assertIn('data-floating-layer="viewport"', self.component)
        self.assertIn("position: fixed", self.styles)
        self.assertIn("z-index: 2400", self.styles)

    def test_component_exposes_real_listbox_state_and_keyboard_contract(self):
        self.assertIn('aria-controls="{{ component_id }}Listbox"', self.component)
        self.assertIn('role="listbox"', self.component)
        self.assertIn('aria-selected=', self.component)
        self.assertIn('option.setAttribute("tabindex", "-1")', self.script)
        self.assertIn('event.key === "Home"', self.script)
        self.assertIn('event.key === "End"', self.script)
        self.assertIn('event.key === "Escape"', self.script)
        self.assertIn('"aria-activedescendant"', self.script)

    def test_layout_counts_and_race_safety_are_explicit(self):
        self.assertIn("dropdownMaxHeight", self.script)
        self.assertIn('document.addEventListener("scroll"', self.script)
        self.assertIn('window.addEventListener("resize"', self.script)
        self.assertIn("ResizeObserver", self.script)
        self.assertIn("requestControllers.get(combobox) !== controller", self.script)
        self.assertIn("requestControllers.get(combobox)?.abort()", self.script)
        self.assertIn("white-space: nowrap", self.styles)
        self.assertIn("margin-left: auto", self.styles)
        self.assertIn("0 ед.", (ROOT / "tests/fixtures/inventory_scope_combobox_e2e.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
