import hashlib
import tempfile
import unittest
from pathlib import Path

from flask import Flask, make_response, render_template_string

from app.static_assets import (
    HTML_CACHE_CONTROL,
    UNVERSIONED_ASSET_CACHE_CONTROL,
    VERSIONED_ASSET_CACHE_CONTROL,
    StaticAssetManifest,
    register_static_asset_versioning,
)


class StaticAssetVersioningTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.static_root = Path(self.temp.name) / "static"
        (self.static_root / "js").mkdir(parents=True)
        self.asset = self.static_root / "js" / "products-tabs.js"
        self.asset.write_text("window.release = 'one';\n", encoding="utf-8")
        self.app = Flask(__name__, static_folder=str(self.static_root))
        self.app.config.update(TESTING=True, SECRET_KEY="asset-test")
        self.manifest = register_static_asset_versioning(self.app)

        @self.app.get("/page")
        def page():
            return render_template_string(
                '<script src="{{ static_asset_url('
                "'js/products-tabs.js'"
                ') }}"></script>'
            )

        @self.app.get("/partial")
        def partial():
            response = make_response("<section>partial</section>")
            response.mimetype = "text/html"
            response.headers["X-ERP-Partial"] = "products-v1"
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            return response

        @self.app.get("/thumbnail-miss")
        def thumbnail_miss():
            response = make_response("", 204)
            response.headers["Cache-Control"] = "private, max-age=300"
            return response

        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_content_hash_is_stable_and_changes_only_with_content(self):
        expected = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.assertEqual(self.manifest.version("js/products-tabs.js"), expected)
        self.assertEqual(self.manifest.version("js/products-tabs.js"), expected)

        self.asset.write_text("window.release = 'two';\n", encoding="utf-8")
        next_release = StaticAssetManifest(self.static_root)
        self.assertNotEqual(next_release.version("js/products-tabs.js"), expected)

        self.asset.write_text("window.release = 'one';\n", encoding="utf-8")
        rollback = StaticAssetManifest(self.static_root)
        self.assertEqual(rollback.version("js/products-tabs.js"), expected)

    def test_workers_and_missing_file_fallback_are_deterministic(self):
        first = StaticAssetManifest(self.static_root)
        second = StaticAssetManifest(self.static_root)
        self.assertEqual(
            first.version("js/products-tabs.js"),
            second.version("js/products-tabs.js"),
        )
        self.assertEqual(
            first.version("js/missing.js"), second.version("js/missing.js")
        )

    def test_html_contains_escaped_versioned_asset_url(self):
        response = self.client.get("/page")
        version = self.manifest.version("js/products-tabs.js")
        html = response.get_data(as_text=True)
        self.assertIn(
            '/static/js/products-tabs.js?v={}'.format(version), html
        )
        self.assertNotIn("Date.now", html)
        self.assertNotIn("random", html.lower())

    def test_versioned_and_unversioned_assets_have_distinct_cache_policy(self):
        version = self.manifest.version("js/products-tabs.js")
        versioned = self.client.get(
            "/static/js/products-tabs.js?v={}".format(version)
        )
        unversioned = self.client.get("/static/js/products-tabs.js")
        invalid = self.client.get("/static/js/products-tabs.js?v=stale")

        self.assertEqual(versioned.status_code, 200)
        self.assertEqual(
            versioned.headers["Cache-Control"], VERSIONED_ASSET_CACHE_CONTROL
        )
        self.assertEqual(
            unversioned.headers["Cache-Control"],
            UNVERSIONED_ASSET_CACHE_CONTROL,
        )
        self.assertEqual(
            invalid.headers["Cache-Control"],
            UNVERSIONED_ASSET_CACHE_CONTROL,
        )
        self.assertNotIn("immutable", unversioned.headers["Cache-Control"])
        self.assertNotIn("immutable", invalid.headers["Cache-Control"])
        versioned.close()
        unversioned.close()
        invalid.close()

    def test_html_is_private_and_existing_no_store_policy_is_preserved(self):
        page = self.client.get("/page")
        self.assertEqual(page.headers["Cache-Control"], HTML_CACHE_CONTROL)
        self.assertNotIn("public", page.headers["Cache-Control"])
        self.assertEqual(page.headers["Pragma"], "no-cache")
        self.assertEqual(page.headers["Expires"], "0")

        partial = self.client.get("/partial")
        self.assertIn("no-store", partial.headers["Cache-Control"])
        self.assertNotIn("public", partial.headers["Cache-Control"])

        thumbnail = self.client.get("/thumbnail-miss")
        self.assertEqual(
            thumbnail.headers["Cache-Control"], "private, max-age=300"
        )


if __name__ == "__main__":
    unittest.main()
