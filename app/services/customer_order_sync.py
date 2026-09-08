"""Publish persisted Bitrix orders to the canonical customer card registry."""

import logging
import os
import sqlite3
from pathlib import Path

from app.services.customer_registry import CustomerRegistry, normalize_email, normalize_phone


LOGGER = logging.getLogger(__name__)


def publish_orders(orders_path, orders):
    # Use the same adapter as the historical importer, without running its CLI.
    from scripts.backfill_customers import order_operation

    configured_orders = Path(os.getenv("ORDERS_DATABASE_PATH") or "instance/orders.db")
    configured_registry = os.getenv("CUSTOMERS_DATABASE_PATH")
    path = (Path(configured_registry) if configured_registry and
            Path(orders_path).resolve() == configured_orders.resolve()
            else Path(orders_path).with_name("customers.db"))
    if not path.exists():
        LOGGER.warning("Customer order publication deferred: registry migration required")
        return
    registry = CustomerRegistry(path)
    try:
        with registry.connection() as connection:
            added = False
            for order in orders:
                operation = order_operation(order, "erp")
                # Empty summaries cannot prove identity. A later detailed import
                # retries even if the snapshot itself did not change.
                if not (operation.get("external_customer_id") or
                        normalize_phone(operation.get("phone")) or
                        normalize_email(operation.get("email"))):
                    continue
                exists = connection.execute(
                    "SELECT 1 FROM customer_operations WHERE operation_type='order' "
                    "AND source=? AND external_id=?",
                    (operation["source"], operation["external_id"]),
                ).fetchone()
                if exists:
                    continue
                if operation.get("external_customer_id") and not (
                        normalize_phone(operation.get("phone")) or
                        normalize_email(operation.get("email"))):
                    owners = connection.execute(
                        "SELECT COUNT(DISTINCT customer_id) FROM customer_operations "
                        "WHERE source=? AND external_customer_id=?",
                        (operation["source"], str(operation["external_customer_id"])),
                    ).fetchone()[0]
                    if owners > 1:
                        LOGGER.warning("Shared external customer ID without contacts: order %s deferred",
                                       operation["external_id"])
                        continue
                registry.upsert_operation(connection, operation)
                added = True
            if added:
                registry.recompute(connection)
    except (OSError, sqlite3.Error):
        # Snapshot is already durable. The next import retries these same IDs;
        # a registry outage must not discard a successfully received order.
        LOGGER.exception("Customer order publication failed; next import will retry")
