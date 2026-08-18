import json
import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_SCRIPT = PROJECT_ROOT / "app/static/js/erp-table-layout.js"


class SalesTableLayoutUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")

    def compute(self, options):
        if not self.node:
            self.skipTest("Node.js is unavailable")
        program = """
const layout = require(process.argv[1]);
const options = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(layout.computeColumnWidths(options)));
"""
        completed = subprocess.run(
            [self.node, "-e", program, str(LAYOUT_SCRIPT), json.dumps(options)],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_free_space_uses_full_container_and_favors_text(self):
        result = self.compute({
            "keys": ["date", "product", "quantity"],
            "preferredWidths": {"date": 100, "product": 240, "quantity": 90},
            "minimumWidths": {"date": 90, "product": 180, "quantity": 80},
            "growWeights": {"date": 0.25, "product": 5, "quantity": 0.2},
            "containerWidth": 1000,
            "actionWidth": 78,
        })
        self.assertAlmostEqual(sum(result["widths"].values()) + 78, 1000)
        self.assertGreater(
            result["widths"]["product"] - 240,
            result["widths"]["date"] - 100,
        )
        self.assertFalse(result["overflow"])

    def test_sparse_view_fills_card_without_growing_actions(self):
        result = self.compute({
            "keys": ["brand", "status", "tracking"],
            "preferredWidths": {"brand": 104, "status": 118, "tracking": 124},
            "minimumWidths": {"brand": 90, "status": 104, "tracking": 108},
            "growWeights": {"brand": 2, "status": 0.5, "tracking": 3},
            "containerWidth": 1440,
            "actionWidth": 78,
        })
        self.assertAlmostEqual(sum(result["widths"].values()) + 78, 1440)
        self.assertEqual(result["tableWidth"], 1440)
        self.assertGreater(result["widths"]["tracking"], result["widths"]["status"])

    def test_columns_shrink_to_minimum_before_overflow(self):
        result = self.compute({
            "keys": ["a", "b"],
            "preferredWidths": {"a": 300, "b": 200},
            "minimumWidths": {"a": 160, "b": 100},
            "containerWidth": 378,
            "actionWidth": 78,
        })
        self.assertAlmostEqual(sum(result["widths"].values()), 300)
        self.assertGreaterEqual(result["widths"]["a"], 160)
        self.assertGreaterEqual(result["widths"]["b"], 100)
        self.assertFalse(result["overflow"])

    def test_narrow_layout_keeps_custom_width_and_scrolls(self):
        result = self.compute({
            "keys": ["a", "b", "c"],
            "preferredWidths": {"a": 250, "b": 180, "c": 160},
            "minimumWidths": {"a": 140, "b": 120, "c": 100},
            "customWidths": ["a"],
            "containerWidth": 350,
            "actionWidth": 78,
        })
        self.assertEqual(result["widths"], {"a": 250, "b": 120, "c": 100})
        self.assertTrue(result["overflow"])


class SalesTableLayoutContractTest(unittest.TestCase):
    def test_sales_reacts_to_container_and_focus_mode_changes(self):
        template = (PROJECT_ROOT / "app/templates/sales.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("window.ErpTableLayout.computeColumnWidths", template)
        self.assertIn('"erp:focus-mode-change"', template)
        self.assertIn('"erp:sidebar-change"', template)
        self.assertIn("customWidths", template)
        self.assertIn("getActualWidths", template)

        sidebar = (PROJECT_ROOT / "app/static/js/sidebar.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('new CustomEvent("erp:sidebar-change"', sidebar)


if __name__ == "__main__":
    unittest.main()
