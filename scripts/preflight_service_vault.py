#!/usr/bin/env python3
"""Validate the Services vault without disclosing keys or credentials."""

from __future__ import print_function

import argparse
import base64
import json
import os
import sqlite3
import sys
from pathlib import Path

from cryptography.fernet import Fernet

from app.services.service_vault import ServiceVault


def validate(database, allow_missing=False):
    raw_key = str(os.getenv("SERVICE_VAULT_KEY") or "").strip()
    decoded = base64.urlsafe_b64decode(raw_key.encode("ascii"))
    if len(decoded) != 32:
        raise RuntimeError("vault key format is invalid")
    cipher = Fernet(raw_key.encode("ascii"))
    probe = b"services-vault-preflight"
    if cipher.decrypt(cipher.encrypt(probe)) != probe:
        raise RuntimeError("vault roundtrip failed")

    path = Path(database)
    if not path.is_file():
        if not allow_missing:
            raise RuntimeError("services database is missing")
        return {
            "database": "missing",
            "encrypted_fields": 0,
            "roundtrip": "pass",
            "services": 0,
        }

    vault = ServiceVault(path)
    encrypted_fields = 0
    with sqlite3.connect(str(path)) as connection:
        service_count = int(
            connection.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        )
        rows = connection.execute(
            "SELECT login_encrypted,password_encrypted FROM service_accounts"
        ).fetchall()
        for login_encrypted, password_encrypted in rows:
            if login_encrypted is not None:
                vault.decrypt(login_encrypted)
                encrypted_fields += 1
            if password_encrypted is not None:
                vault.decrypt(password_encrypted)
                encrypted_fields += 1

    return {
        "database": "readable",
        "encrypted_fields": encrypted_fields,
        "roundtrip": "pass",
        "services": service_count,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--allow-missing", action="store_true")
    arguments = parser.parse_args()
    try:
        result = validate(arguments.database, arguments.allow_missing)
    except Exception as error:
        print(
            "SERVICES_VAULT_PREFLIGHT_FAILED={}".format(type(error).__name__),
            file=sys.stderr,
        )
        return 1
    print(
        "SERVICES_VAULT_PREFLIGHT_OK={}".format(
            json.dumps(result, sort_keys=True, separators=(",", ":"))
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
