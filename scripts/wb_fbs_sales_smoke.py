#!/usr/bin/env python3
"""Production smoke for the GET-only WB FBS assembly workflow."""

from __future__ import print_function

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.wildberries_orders import WildberriesOrdersReadOnlyClient  # noqa: E402


ASSEMBLY_URL = "http://127.0.0.1:5000/sales?view=assembly"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        return None


def _http_body(url):
    with urlopen(url, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("HTTP {} returned {}".format(url, response.status))
        return response.read()


def _check_protected_assembly_route(url=ASSEMBLY_URL):
    """Verify the GET route is protected without pretending to be a user."""
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(url, timeout=15)
    except HTTPError as error:
        status = int(error.code)
        location = error.headers.get("Location", "")
    else:
        status = int(response.status)
        location = response.headers.get("Location", "")
        response.close()
    if status != 302:
        raise RuntimeError("Sales assembly route returned HTTP {}".format(status))
    parsed = urlsplit(location)
    if parsed.path != "/login":
        raise RuntimeError("Sales assembly route did not require login")
    next_values = parse_qs(parsed.query).get("next", [])
    if "/sales?view=assembly" not in next_values:
        raise RuntimeError("Sales assembly route lost its target after login")
    return status


def _check_assembly_contract():
    template = (PROJECT_ROOT / "app" / "templates" / "sales.html").read_text(
        encoding="utf-8"
    )
    required = (
        'data-wb-assembly-list',
        'fetch("/api/orders/wildberries/sync"',
        'method: "POST"',
    )
    if any(marker not in template for marker in required):
        raise RuntimeError("Sales assembly workspace contract is unavailable")


def main():
    if os.getenv("ERP_PRODUCTION_WB_FBS_SMOKE") != "confirmed":
        raise RuntimeError("production WB FBS smoke requires explicit confirmation")
    token = os.getenv("WB_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WB_API_TOKEN is unavailable")

    client = WildberriesOrdersReadOnlyClient(token)
    orders = client.get_new_orders()
    if any(item.get("method") != "GET" for item in client.request_audit):
        raise RuntimeError("non-GET Wildberries request detected")
    if not client.request_audit or any(
        item.get("service") != "marketplace"
        or item.get("path") != "/api/v3/orders/new"
        for item in client.request_audit
    ):
        raise RuntimeError("unexpected Wildberries FBS endpoint detected")

    login = _http_body("http://127.0.0.1:5000/login")
    register = _http_body("http://127.0.0.1:5000/register")
    assembly_status = _check_protected_assembly_route()
    _check_assembly_contract()
    token_bytes = token.encode("utf-8")
    if token_bytes in login or token_bytes in register:
        raise RuntimeError("WB token exposed over HTTP")

    result = {
        "received": len(orders),
        "api": {
            "method": "GET",
            "service": "marketplace",
            "path": "/api/v3/orders/new",
            "authorized": True,
        },
        "wb_writes": 0,
        "database_writes": 0,
        "http": {
            "login": 200,
            "register": 200,
            "sales_assembly": assembly_status,
            "sales_assembly_auth": "required",
        },
    }
    print("WB_FBS_SALES_SMOKE_OK={}".format(
        json.dumps(result, ensure_ascii=False, sort_keys=True)
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
