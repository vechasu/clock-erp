import unittest
from pathlib import Path


class WildberriesFbsSalesSmokeContractTest(unittest.TestCase):
    def test_smoke_is_get_only_idempotent_and_checks_sales_workspace(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "wb_fbs_sales_smoke.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('item.get("method") != "GET"', source)
        self.assertIn('second["added"] != 0', source)
        self.assertIn("GROUP BY external_order_id HAVING COUNT(*) > 1", source)
        self.assertIn('_application_body("/sales?view=assembly")', source)
        self.assertIn('app.config["TESTING"] = True', source)
        self.assertIn('app.config["AUTH_TESTING"] = False', source)
        self.assertEqual(
            source.count('_http_body("http://127.0.0.1:5000/'), 2
        )
        for method in (".post(", ".put(", ".patch(", ".delete("):
            self.assertNotIn(method, source.casefold())

    def test_deploy_runs_fbs_smoke_after_stable_data_guard(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "deploy.sh").read_text(
            encoding="utf-8"
        )
        guard = source.rindex('printf \'DATA_AFTER=%s\\n\' "$DATA_SNAPSHOT_AFTER"')
        smoke = source.rindex("scripts/wb_fbs_sales_smoke.py")
        self.assertGreater(smoke, guard)


if __name__ == "__main__":
    unittest.main()
