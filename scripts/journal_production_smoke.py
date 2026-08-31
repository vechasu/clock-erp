#!/usr/bin/env python3
"""Authenticated read-only production smoke for the ERP journal."""

from __future__ import print_function

import json
import os
import sys


ROUTES = (
    "/app/journal",
    "/api/v1/journal",
    "/api/v1/journal?entity_type=order",
)


def main():
    if os.getenv("ERP_PRODUCTION_JOURNAL_SMOKE") != "confirmed":
        print("JOURNAL_SMOKE_FAILED=confirmation", file=sys.stderr)
        return 2

    stage = "bootstrap"
    try:
        from app.auth import get_auth_store
        from app.web import app

        app.config.update(TESTING=True, AUTH_TESTING=True)
        with app.app_context():
            users = get_auth_store().list_team_presence()
        owner = next((item for item in users if item.get("role") == "owner"), None)
        if not owner:
            raise RuntimeError("owner missing")

        statuses = {}
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = owner["id"]

            for route in ROUTES:
                stage = route
                response = client.get(route)
                statuses[route] = response.status_code
                if response.status_code != 200:
                    raise RuntimeError("unexpected HTTP status")
                if route.startswith("/api/"):
                    payload = response.get_json()
                    if not isinstance(payload, dict) or "data" not in payload:
                        raise RuntimeError("invalid journal JSON")
    except Exception as error:
        print(
            "JOURNAL_SMOKE_FAILED={} type={}".format(
                stage, type(error).__name__
            ),
            file=sys.stderr,
        )
        return 1

    print("JOURNAL_SMOKE_OK={}".format(json.dumps(
        statuses, sort_keys=True, separators=(",", ":")
    )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
