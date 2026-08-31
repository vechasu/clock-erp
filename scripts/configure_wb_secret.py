#!/usr/bin/env python3
"""Interactively store WB_API_TOKEN in the existing protected systemd env file."""

from __future__ import print_function

import argparse
import getpass
import os
import stat
import tempfile
from pathlib import Path


KEY = "WB_API_TOKEN"


def update_secret(environment_file, token, expected_uid=0):
    path = Path(environment_file)
    details = path.stat()
    if details.st_uid != int(expected_uid) or stat.S_IMODE(details.st_mode) != 0o600:
        raise RuntimeError("protected EnvironmentFile must be root-owned mode 0600")
    token = str(token or "").strip()
    if not token or any(character in token for character in "\r\n\0"):
        raise RuntimeError("WB token is empty or malformed")
    lines = path.read_text(encoding="utf-8").splitlines()
    replacement = KEY + "=" + token
    found = False
    updated = []
    for line in lines:
        if line.startswith(KEY + "="):
            if not found:
                updated.append(replacement)
                found = True
            continue
        updated.append(line)
    if not found:
        updated.append(replacement)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".clock-erp.env.", dir=str(path.parent)
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(updated) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, details.st_uid, details.st_gid)
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--environment-file", default="/etc/clock-erp/clock-erp.env"
    )
    arguments = parser.parse_args()
    first = getpass.getpass("WB API token: ")
    second = getpass.getpass("Repeat WB API token: ")
    if first != second:
        raise SystemExit("WB_SECRET_FAILED=values-do-not-match")
    try:
        update_secret(arguments.environment_file, first)
    except Exception as error:
        raise SystemExit("WB_SECRET_FAILED={}".format(type(error).__name__))
    print("WB_SECRET_OK=stored")


if __name__ == "__main__":
    main()
