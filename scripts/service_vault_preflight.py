#!/usr/bin/env python3
"""Fail-closed validation for the existing production Services vault key."""

import argparse
import base64
import binascii
import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


KEY_NAME = "SERVICE_VAULT_KEY"


class VaultPreflightError(RuntimeError):
    pass


def load_key(environment_file):
    path = Path(environment_file)
    if not path.is_file():
        raise VaultPreflightError("protected Services EnvironmentFile is missing")
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(KEY_NAME + "="):
            entries.append(line.split("=", 1)[1])
    if len(entries) != 1 or not entries[0] or entries[0] != entries[0].strip():
        raise VaultPreflightError("SERVICE_VAULT_KEY must appear exactly once without surrounding whitespace")
    key = entries[0]
    try:
        decoded = base64.urlsafe_b64decode(key.encode("ascii"))
        if len(decoded) != 32:
            raise ValueError
        Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, ValueError, TypeError, binascii.Error):
        raise VaultPreflightError("SERVICE_VAULT_KEY has an invalid format")
    return key


def validate_permissions(environment_file, expected_uid=0, expected_mode=0o600):
    details = os.stat(str(environment_file))
    if details.st_uid != expected_uid:
        raise VaultPreflightError("Services EnvironmentFile has an unexpected owner")
    if stat.S_IMODE(details.st_mode) != expected_mode:
        raise VaultPreflightError("Services EnvironmentFile permissions must be 0600")


def encrypted_values(connection):
    rows = connection.execute(
        "SELECT login_encrypted,password_encrypted FROM service_accounts ORDER BY id"
    ).fetchall()
    return [bytes(value) for row in rows for value in row if value is not None]


def preflight(environment_file, database, expected_uid=0):
    validate_permissions(environment_file, expected_uid=expected_uid)
    key = load_key(environment_file)
    database = Path(database)
    if not database.is_file():
        raise VaultPreflightError("Services database is missing")

    connection = sqlite3.connect("file:{}?mode=ro".format(database), uri=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise VaultPreflightError("Services database copy failed quick_check")
        values_before = encrypted_values(connection)
        cipher = Fernet(key.encode("ascii"))
        roundtrip_plaintext = b"services-vault-preflight"
        if cipher.decrypt(cipher.encrypt(roundtrip_plaintext)) != roundtrip_plaintext:
            raise VaultPreflightError("Services key encrypt/decrypt roundtrip failed")
        for value in values_before:
            try:
                plaintext = cipher.decrypt(value).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError, TypeError):
                raise VaultPreflightError("existing Services data cannot be decrypted with the configured key")
            if not isinstance(plaintext, str):
                raise VaultPreflightError("decrypted Services data has an invalid structure")
        values_after = encrypted_values(connection)
    finally:
        connection.close()
    digest_before = hashlib.sha256(b"\0".join(values_before)).hexdigest()
    digest_after = hashlib.sha256(b"\0".join(values_after)).hexdigest()
    if digest_before != digest_after:
        raise VaultPreflightError("Services ciphertext changed during read-only preflight")
    return {
        "database_quick_check": "ok",
        "encrypted_fields": len(values_before),
        "ciphertext_sha256": digest_before,
        "key_fingerprint": hashlib.sha256(key.encode("ascii")).hexdigest()[:12],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment-file", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    try:
        report = preflight(args.environment_file, args.database)
    except VaultPreflightError as error:
        raise SystemExit("SERVICE_VAULT_PREFLIGHT_FAILED: {}".format(error))
    except Exception:
        raise SystemExit("SERVICE_VAULT_PREFLIGHT_FAILED: unexpected validation failure")
    if args.report:
        report_path = Path(args.report)
        report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(str(report_path), 0o600)
    print("SERVICE_VAULT_PREFLIGHT_OK fields={} roundtrip=ok".format(
        report["encrypted_fields"]
    ))


if __name__ == "__main__":
    main()
