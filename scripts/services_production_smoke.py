#!/usr/bin/env python3
"""Destructive-only-to-self production smoke for the isolated Services vault."""

from __future__ import print_function

import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def counts(database):
    with sqlite3.connect(database) as connection:
        return {
            "accounts": int(connection.execute(
                "SELECT COUNT(*) FROM service_accounts"
            ).fetchone()[0]),
            "services": int(connection.execute(
                "SELECT COUNT(*) FROM services"
            ).fetchone()[0]),
        }


def cleanup(database, service_id, expected_name):
    if not service_id:
        return
    with sqlite3.connect(database, timeout=15, isolation_level=None) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT name FROM services WHERE id=?", (int(service_id),)
        ).fetchone()
        if row is not None and row[0] == expected_name:
            connection.execute("DELETE FROM services WHERE id=?", (int(service_id),))
        connection.commit()


def main():
    if os.getenv("ERP_PRODUCTION_SERVICES_SMOKE") != "confirmed":
        print("SERVICES_SMOKE_FAILED=confirmation", file=sys.stderr)
        return 2

    started_at = int(time.time()) - 5
    service_id = None
    stage = "bootstrap"
    secret_values = []
    database = "instance/services.db"
    test_name = "Codex Services smoke {}".format(secrets.token_hex(6))
    before = counts(database)

    try:
        from app.auth import get_auth_store
        from app.web import app

        app.config.update(TESTING=True, AUTH_TESTING=True)
        with app.app_context():
            users = get_auth_store().list_team_presence()
        owner = next((item for item in users if item.get("role") == "owner"), None)
        if not owner:
            raise RuntimeError("owner missing")

        csrf = secrets.token_urlsafe(24)
        login_one = secrets.token_urlsafe(24)
        password_one = secrets.token_urlsafe(32)
        login_two = secrets.token_urlsafe(24)
        password_two = secrets.token_urlsafe(32)
        secret_values.extend((login_one, password_one, login_two, password_two))
        payload = {
            "accounts": [{
                "id": 0,
                "label": "Smoke account",
                "login": login_one,
                "password": password_one,
            }],
            "category": "infrastructure",
            "description": "Temporary production verification",
            "favorite": False,
            "icon": "lock",
            "name": test_name,
            "permissions": [],
            "url": "https://services-smoke.invalid/initial",
        }

        with app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = owner["id"]
                session["_csrf_token"] = csrf

            stage = "page"
            page = client.get("/app/services")
            if page.status_code != 200 or "Рабочие сервисы".encode("utf-8") not in page.data:
                raise RuntimeError("services page failed")

            stage = "create"
            created = client.post(
                "/api/services", json=payload,
                headers={"X-CSRF-Token": csrf},
            )
            if created.status_code != 201:
                raise RuntimeError("create failed")
            service_id = int(created.get_json()["id"])

            stage = "masked-list"
            listing_response = client.get("/api/services")
            if listing_response.status_code != 200:
                raise RuntimeError("list failed")
            listing_bytes = listing_response.data
            if any(value.encode("utf-8") in listing_bytes for value in secret_values):
                raise RuntimeError("plaintext in masked response")
            item = next(
                value for value in listing_response.get_json()["services"]
                if int(value["id"]) == service_id
            )
            account = item["accounts"][0]
            if not account["has_login"] or not account["has_password"]:
                raise RuntimeError("masked flags missing")

            stage = "reveal"
            login_response = client.get(
                "/api/service-accounts/{}/login".format(account["id"])
            )
            password_response = client.get(
                "/api/service-accounts/{}/password".format(account["id"])
            )
            if login_response.get_json().get("value") != login_one:
                raise RuntimeError("login reveal failed")
            if password_response.get_json().get("value") != password_one:
                raise RuntimeError("password reveal failed")

            stage = "update"
            payload.update({
                "accounts": [{
                    "id": account["id"],
                    "label": "Smoke account updated",
                    "login": login_two,
                    "password": password_two,
                }],
                "url": "https://services-smoke.invalid/updated",
                "version": item["version"],
            })
            updated = client.put(
                "/api/services/{}".format(service_id), json=payload,
                headers={"X-CSRF-Token": csrf},
            )
            if updated.status_code != 200:
                raise RuntimeError("update failed")
            updated_listing_response = client.get("/api/services")
            if updated_listing_response.status_code != 200:
                raise RuntimeError("updated list failed")
            if any(
                value.encode("utf-8") in updated_listing_response.data
                for value in secret_values
            ):
                raise RuntimeError("plaintext in updated masked response")
            updated_item = next(
                value for value in updated_listing_response.get_json()["services"]
                if int(value["id"]) == service_id
            )
            if updated_item["url"] != payload["url"]:
                raise RuntimeError("updated URL missing")
            updated_login = client.get(
                "/api/service-accounts/{}/login".format(account["id"])
            )
            updated_password = client.get(
                "/api/service-accounts/{}/password".format(account["id"])
            )
            if updated_login.get_json().get("value") != login_two:
                raise RuntimeError("updated login reveal failed")
            if updated_password.get_json().get("value") != password_two:
                raise RuntimeError("updated password reveal failed")

            stage = "archive"
            archived = client.post(
                "/api/services/{}/archive".format(service_id),
                json={"archived": True},
                headers={"X-CSRF-Token": csrf},
            )
            if archived.status_code != 200:
                raise RuntimeError("archive failed")

        stage = "encrypted-storage"
        database_bytes = b"".join(
            path.read_bytes()
            for path in Path("instance").glob("services.db*")
            if path.is_file()
        )
        if any(value.encode("utf-8") in database_bytes for value in secret_values):
            raise RuntimeError("plaintext in database")

        with sqlite3.connect("instance/catalog.db") as connection:
            audit_rows = connection.execute(
                "SELECT object_label,object_secondary,metadata_json "
                "FROM erp_audit_events WHERE entity_type='service' AND entity_id=?",
                (str(service_id),),
            ).fetchall()
        audit_bytes = json.dumps(audit_rows, ensure_ascii=False).encode("utf-8")
        if any(value.encode("utf-8") in audit_bytes for value in secret_values):
            raise RuntimeError("plaintext in audit journal")

        stage = "log-security"
        journal = subprocess.check_output([
            "journalctl", "-u", "clock-erp", "--since", "@{}".format(started_at),
            "--no-pager", "--quiet",
        ])
        if any(value.encode("utf-8") in journal for value in secret_values):
            raise RuntimeError("plaintext in service log")
    except Exception as error:
        print(
            "SERVICES_SMOKE_FAILED={} type={}".format(stage, type(error).__name__),
            file=sys.stderr,
        )
        return 1
    finally:
        try:
            cleanup(database, service_id, test_name)
        except Exception as cleanup_error:
            print(
                "SERVICES_SMOKE_FAILED=cleanup type={}".format(
                    type(cleanup_error).__name__
                ),
                file=sys.stderr,
            )
            return 1

    after = counts(database)
    if after != before:
        print("SERVICES_SMOKE_FAILED=data-restore", file=sys.stderr)
        return 1
    print("SERVICES_SMOKE_OK={}".format(json.dumps({
        "create": "pass",
        "data_after": after,
        "data_before": before,
        "delete": "pass",
        "log_security": "pass",
        "masked": "pass",
        "page": "pass",
        "reveal": "pass",
        "update": "pass",
    }, sort_keys=True, separators=(",", ":"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
