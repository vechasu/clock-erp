#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.repair_cases import migrate_repair_file


def main():
    parser = argparse.ArgumentParser(
        description="Безопасная миграция JSON-хранилища ремонтов",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("instance/repair_cases.json"),
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Создать backup и записать мигрированные данные",
    )
    args = parser.parse_args()
    report = migrate_repair_file(
        args.path,
        apply=args.apply,
        backup_dir=args.backup_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
