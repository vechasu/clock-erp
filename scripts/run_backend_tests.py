#!/usr/bin/env python3
"""Run the unittest suite with external network name resolution disabled."""

from __future__ import print_function

import argparse
import ipaddress
import socket
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


ORIGINAL_GETADDRINFO = socket.getaddrinfo


def local_host(host):
    value = str(host or "").strip().strip("[]")
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def guarded_getaddrinfo(host, *args, **kwargs):
    if not local_host(host):
        raise OSError("external network disabled during backend tests")
    return ORIGINAL_GETADDRINFO(host, *args, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--verbosity", type=int, default=2)
    arguments = parser.parse_args()
    socket.getaddrinfo = guarded_getaddrinfo
    suite = unittest.defaultTestLoader.discover(
        arguments.start_directory,
        pattern=arguments.pattern,
    )
    result = unittest.TextTestRunner(
        verbosity=arguments.verbosity,
    ).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
