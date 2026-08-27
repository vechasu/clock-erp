import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WarehouseStockToggleTest(unittest.TestCase):
    def test_active_segment_uses_solid_primary_fill_on_hover_too(self):
        css = (ROOT / "app/static/css/erp-components.css").read_text(
            encoding="utf-8"
        )
        active_rule = css.split(
            ".warehouse-availability-segment.is-active,", 1
        )[1].split("}", 1)[0]

        self.assertIn(
            ".warehouse-availability-segment.is-active:hover",
            active_rule,
        )
        self.assertIn("background: var(--erp-primary)", active_rule)
        self.assertIn("color: #ffffff", active_rule)
        self.assertIn("box-shadow: none", active_rule)

    def test_segment_state_updates_before_loading_and_on_history_navigation(self):
        source = (ROOT / "app/templates/warehouse.html").read_text(
            encoding="utf-8"
        )
        submit = source.split(
            "function submitWarehouseStockState(stockState)", 1
        )[1].split("function resetWarehouseTableFilters", 1)[0]
        popstate = source.split(
            'window.addEventListener("popstate", function()', 1
        )[1].split("const stockForm", 1)[0]

        self.assertLess(
            submit.index("syncWarehouseStockStateControls(stockState)"),
            submit.index("navigateWithWarehouseParams(url)"),
        )
        self.assertIn(
            'url.searchParams.get("stock_state") || "all"',
            popstate,
        )
        self.assertLess(
            popstate.index("syncWarehouseStockStateControls"),
            popstate.index("loadWarehouseResultsUrl"),
        )


if __name__ == "__main__":
    unittest.main()
