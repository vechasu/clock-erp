import json
import unittest
from pathlib import Path

from app import web


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "characterization"
ALLOWED_STATUSES = {
    "NOT_STARTED",
    "IN_PROGRESS",
    "IMPLEMENTED",
    "TESTED",
    "VERIFIED",
    "BLOCKED",
}


def load_fixture(name):
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class RewriteRouteCharacterizationTest(unittest.TestCase):
    def test_legacy_route_manifest_is_unchanged(self):
        expected = load_fixture("legacy_routes.json")["routes"]
        actual = []
        for rule in sorted(web.app.url_map.iter_rules(), key=lambda item: item.rule):
            if rule.rule.startswith("/api/"):
                continue
            methods = sorted(
                method
                for method in rule.methods
                if method not in {"HEAD", "OPTIONS"}
            )
            actual.append({"rule": rule.rule, "methods": methods})

        self.assertEqual(actual, expected)

    def test_required_legacy_templates_remain_available(self):
        template_root = ROOT / "app" / "templates"
        required = load_fixture("legacy_routes.json")["required_templates"]

        missing = [
            template_name
            for template_name in required
            if not (template_root / template_name).is_file()
        ]
        self.assertEqual(missing, [])

    def test_critical_scenarios_reference_real_routes_and_evidence(self):
        route_manifest = load_fixture("legacy_routes.json")
        route_rules = {item["rule"] for item in route_manifest["routes"]}
        scenario_manifest = load_fixture("critical_scenarios.json")

        self.assertEqual(
            scenario_manifest["external_write_policy"],
            "fake_or_read_only",
        )
        self.assertGreaterEqual(len(scenario_manifest["scenarios"]), 19)

        for scenario in scenario_manifest["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                self.assertIn(scenario["migration_status"], ALLOWED_STATUSES)
                self.assertIn(
                    scenario["characterization_status"],
                    ALLOWED_STATUSES,
                )
                self.assertEqual(scenario["migration_status"], "NOT_STARTED")
                self.assertTrue(set(scenario["routes"]).issubset(route_rules))
                self.assertTrue(scenario["evidence"])
                for evidence_path in scenario["evidence"]:
                    self.assertTrue((ROOT / evidence_path).is_file())

    def test_all_six_audit_documents_are_versioned_for_the_rewrite(self):
        names = {
            "full-react-rewrite-audit.md",
            "full-react-rewrite-feature-matrix.md",
            "full-react-rewrite-api-map.md",
            "full-react-rewrite-ui-map.md",
            "full-react-rewrite-roadmap.md",
            "full-react-rewrite-risk-register.md",
        }
        docs_root = ROOT / "docs"

        for name in names:
            with self.subTest(document=name):
                document = docs_root / name
                self.assertTrue(document.is_file())
                self.assertGreater(document.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
