#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.legacy_repair_import import (
    LegacyRepairImportError,
    import_legacy_repair_file,
    load_import_dataset,
)


ORDER_URL = "https://tictactoy.ru/api/order.php?id="
DEFAULT_DATASET = (
    PROJECT_ROOT / "scripts" / "data" / "legacy_repairs_2026.json"
)
ACTION_LABELS = {
    "create": "СОЗДАТЬ",
    "update": "ОБНОВИТЬ",
    "skip": "ПРОПУСТИТЬ",
    "manual": "РУЧНАЯ ПРОВЕРКА",
}


def _text(value):
    return str(value or "").strip()


def fetch_order_snapshot(order_number):
    try:
        response = requests.get(
            ORDER_URL + _text(order_number),
            timeout=(3.05, 10),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        return {
            "ok": False,
            "error": f"ошибка чтения заказа ({type(error).__name__})",
        }
    order = payload.get("order") if isinstance(payload, dict) else None
    if payload.get("status") != "ok" or not isinstance(order, dict):
        return {
            "ok": False,
            "error": _text(payload.get("message")) or "заказ не найден",
        }

    properties = {}
    for item in order.get("properties", []):
        if not isinstance(item, dict):
            continue
        code = _text(item.get("code")).upper()
        if code:
            properties[code] = _text(item.get("value"))
    user = order.get("user") if isinstance(order.get("user"), dict) else {}
    products = [
        {"id": _text(item.get("id")), "name": _text(item.get("name"))}
        for item in order.get("products", [])
        if isinstance(item, dict) and _text(item.get("name"))
    ]
    return {
        "ok": True,
        "order_number": _text(order.get("number") or order.get("id")),
        "order_date": _text(order.get("date"))[:10],
        "client": {
            "client_name": properties.get("FIO") or _text(user.get("name")),
            "client_phone": properties.get("PHONE") or _text(user.get("phone")),
            "client_email": properties.get("EMAIL") or _text(user.get("email")),
        },
        "products": products,
    }


def print_report(report):
    print(
        f"Режим: {'APPLY' if report.get('applied') else 'DRY-RUN'} | "
        f"пакет: {report.get('batch_key')}"
    )
    print(
        "Действие          | Карточка     | Проверка | Источник"
    )
    print("-" * 88)
    for operation in report.get("operations", []):
        action = ACTION_LABELS.get(
            operation.get("action"),
            _text(operation.get("action")).upper(),
        )
        repair_number = _text(operation.get("repair_number")) or "—"
        review = "ДА" if operation.get("requires_review") else "НЕТ"
        print(
            f"{action:<17} | {repair_number:<12} | "
            f"{review:<8} | {_text(operation.get('label'))}"
        )
    counts = report.get("counts", {})
    print("-" * 88)
    print(
        "ИТОГО: "
        f"создать={counts.get('create', 0)}, "
        f"обновить={counts.get('update', 0)}, "
        f"пропустить={counts.get('skip', 0)}, "
        f"неоднозначные={counts.get('manual', 0)}, "
        f"требуют проверки={counts.get('requires_review', 0)}"
    )
    review_operations = [
        operation
        for operation in report.get("operations", [])
        if operation.get("requires_review")
    ]
    if review_operations:
        print("ТРЕБУЮТ РУЧНОЙ ПРОВЕРКИ:")
        for operation in review_operations:
            notes = "; ".join(
                _text(note)
                for note in operation.get("review_notes", [])
                if _text(note)
            )
            print(f"- {_text(operation.get('label'))}: {notes}")
    if report.get("backup_path"):
        print(f"BACKUP_PATH={report['backup_path']}")
        print(f"BACKUP_SHA256={report['backup_sha256']}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Идемпотентный перенос ремонтов из старого Google Sheets "
            "в JSON-хранилище ERP"
        ),
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("instance/repair_cases.json"),
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATASET,
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--allow-read-only-network",
        action="store_true",
        help="Прочитать заказы Tictactoy для заполнения известных данных",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Создать backup и применить импорт",
    )
    args = parser.parse_args()
    try:
        dataset = load_import_dataset(args.data)
        report = import_legacy_repair_file(
            args.path,
            dataset,
            apply=args.apply,
            backup_dir=args.backup_dir,
            order_resolver=(
                fetch_order_snapshot
                if args.allow_read_only_network
                else None
            ),
        )
    except LegacyRepairImportError as error:
        print(f"IMPORT_ERROR: {error}", file=sys.stderr)
        return 1

    report["order_lookup_enabled"] = bool(
        args.allow_read_only_network
    )
    print_report(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"REPORT_PATH={args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
