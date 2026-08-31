import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductsCompactToolbarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        cls.focus_script = (ROOT / "app/static/js/erp-focus-mode.js").read_text(
            encoding="utf-8"
        )

    def test_default_state_hides_collection_controls(self):
        self.assertIn("[data-collection-mode-control] {", self.template)
        self.assertIn("display: none;", self.template)
        self.assertIn(
            ".app.is-collection-mode [data-collection-mode-control]",
            self.template,
        )
        self.assertIn(
            'id="productCollectionBulkBar" class="warehouse-collection-bulk-toolbar" hidden',
            self.template,
        )

    def test_more_menu_exposes_the_three_context_actions(self):
        toolbar = self.template.split('id="warehouseSearchForm"', 1)[1].split(
            "</form>", 1
        )[0]
        for control in (
            'id="warehouseCollectionModeTrigger"',
            'id="warehouseColumnSettingsTrigger"',
            'id="warehouseFocusModeToggle"',
        ):
            self.assertIn(control, toolbar)
        for label in (
            "Изменить подборки",
            "Настроить столбцы",
            "Развернуть таблицу",
        ):
            self.assertIn(label, toolbar)
        self.assertIn('event.key === "Escape" && !menu.hidden', self.template)
        self.assertIn('!event.target.closest(".warehouse-more")', self.template)

    def test_collection_mode_keeps_selection_on_error_and_resets_on_success(self):
        collection_script = self.template.split(
            "function selectedProductCollectionIds", 1
        )[1].split("let warehouseAddReturnFocus", 1)[0]
        self.assertIn("setProductCollectionMode(true)", collection_script)
        self.assertIn("setProductCollectionMode(false)", collection_script)
        self.assertIn("ids.length > 0", collection_script)
        self.assertIn("Boolean(target?.value)", collection_script)
        self.assertIn("productCollectionMutationPending", collection_script)
        self.assertIn("if (!response.ok) throw new Error", collection_script)
        error_block = collection_script.split("} catch (error) {", 1)[1].split(
            "} finally {", 1
        )[0]
        self.assertNotIn("setProductCollectionMode(false)", error_block)
        self.assertNotIn("success", error_block)
        self.assertIn("showWarehouseNotice(error.message, \"error\")", error_block)
        self.assertIn("loadWarehouseResultsUrl", collection_script)
        self.assertNotIn("window.location.reload", collection_script)

    def test_columns_and_focus_keep_existing_controllers(self):
        self.assertIn(
            "function initializeWarehouseColumnSettings", self.template
        )
        self.assertIn('reset.id = "warehouseTableReset"', self.template)
        self.assertIn("data-erp-focus-mode-toggle", self.template)
        self.assertIn("label.textContent = action + labelSuffix", self.focus_script)
        self.assertIn('data-focus-mode-label-suffix=" таблицу"', self.template)

    def test_results_navigation_cancels_local_collection_mode(self):
        self.assertIn(
            'document.addEventListener("warehouse:results-updated"',
            self.template,
        )
        self.assertIn('window.addEventListener("pagehide"', self.template)


if __name__ == "__main__":
    unittest.main()
