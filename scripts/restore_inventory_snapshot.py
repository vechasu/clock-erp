#!/usr/bin/env python3
"""Preview or apply an idempotent completed-inventory snapshot restoration."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.inventory_restoration import InventorySnapshotRestoration  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--reason", default="")
    parser.add_argument("--user", default="Система")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    service = InventorySnapshotRestoration()
    if args.apply:
        result = service.apply(
            args.brand, args.session_id, reason=args.reason, user_name=args.user
        )
    else:
        result = service.plan(args.brand, args.session_id)
        result["applied"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
