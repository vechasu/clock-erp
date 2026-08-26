"""Deterministic URLs and cache policy for local static assets."""

import hashlib
from pathlib import Path

from flask import request, url_for


HTML_CACHE_CONTROL = "private, no-cache, max-age=0, must-revalidate"
UNVERSIONED_ASSET_CACHE_CONTROL = "no-cache, max-age=0, must-revalidate"
VERSIONED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class StaticAssetManifest:
    """Resolve content versions once per worker from the deployed release."""

    def __init__(self, static_root):
        self.static_root = Path(static_root).resolve()
        self._versions = {}

    def version(self, filename):
        normalized = str(filename).replace("\\", "/").lstrip("/")
        if normalized not in self._versions:
            path = (self.static_root / normalized).resolve()
            try:
                path.relative_to(self.static_root)
                payload = path.read_bytes()
            except (OSError, ValueError):
                payload = ("missing:" + normalized).encode("utf-8")
            self._versions[normalized] = hashlib.sha256(payload).hexdigest()
        return self._versions[normalized]


def register_static_asset_versioning(app):
    manifest = StaticAssetManifest(app.static_folder)
    app.extensions["static_asset_manifest"] = manifest

    def static_asset_url(filename):
        return url_for(
            "static",
            filename=filename,
            v=manifest.version(filename),
        )

    app.jinja_env.globals["static_asset_url"] = static_asset_url

    @app.after_request
    def apply_cache_policy(response):
        if request.endpoint == "static":
            filename = (request.view_args or {}).get("filename", "")
            expected = manifest.version(filename)
            if request.args.get("v") == expected:
                response.headers["Cache-Control"] = VERSIONED_ASSET_CACHE_CONTROL
            else:
                response.headers["Cache-Control"] = (
                    UNVERSIONED_ASSET_CACHE_CONTROL
                )
            return response

        if (
            response.mimetype == "text/html"
            and not response.headers.get("Cache-Control")
        ):
            response.headers["Cache-Control"] = HTML_CACHE_CONTROL
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    return manifest
