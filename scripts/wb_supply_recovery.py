#!/usr/bin/env python3
"""Read-only WB supply preview. Deliberately has no --apply option."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.clients.wildberries_orders import WildberriesOrdersReadOnlyClient
from app.services.wildberries_recovery import WildberriesRecovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('supply_id')
    parser.add_argument('--orders-db', default=os.getenv('ORDERS_DATABASE_PATH', 'instance/orders.db'))
    parser.add_argument('--catalog-db', default=os.getenv('CATALOG_DATABASE_PATH', 'instance/catalog.db'))
    parser.add_argument('--expected-count', type=int)
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()
    token = os.getenv('WB_API_TOKEN', '')
    service = WildberriesRecovery(WildberriesOrdersReadOnlyClient(token), args.orders_db, args.catalog_db)
    report = service.preview_supply(args.supply_id, args.expected_count, args.days)
    print(json.dumps(report, ensure_ascii=True, indent=2).replace(token, '[REDACTED]') if token else json.dumps(report, ensure_ascii=True))
    return 2 if report['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
