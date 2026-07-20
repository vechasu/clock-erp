#!/usr/bin/env python3
"""Incrementally synchronize Bitrix content; never synchronize inventory."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.catalog_db import CatalogDatabase  # noqa: E402
from app.clients.bitrix_catalog import BitrixCatalogReadOnlyClient  # noqa: E402
from app.services.bitrix_catalog_importer import BitrixCatalogImporter  # noqa: E402


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def last_successful_cursor(database):
    if not database.exists():
        return None
    with database.connect() as connection:
        row = connection.execute(
            "SELECT cursor_to FROM catalog_sync_runs "
            "WHERE mode = ? AND status = ? AND cursor_to IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            ("incremental_sync", "success"),
        ).fetchone()
        if row:
            return row["cursor_to"]
        row = connection.execute(
            "SELECT cursor_from FROM catalog_sync_runs "
            "WHERE mode = ? AND cursor_from IS NOT NULL ORDER BY id DESC LIMIT 1",
            ("incremental_sync",),
        ).fetchone()
        if row:
            return row["cursor_from"]
        row = connection.execute(
            "SELECT MAX(external_updated_at) FROM catalog_products"
        ).fetchone()
        return row[0] if row and row[0] else None


def _create_run(database, cursor_from, started_at):
    database.initialize()
    with database.transaction() as connection:
        return connection.execute(
            "INSERT INTO catalog_sync_runs (mode, status, started_at, cursor_from) "
            "VALUES (?, ?, ?, ?)",
            ("incremental_sync", "running", started_at, cursor_from),
        ).lastrowid


def _save_progress(database, run_id, pages, received, totals):
    with database.transaction() as connection:
        connection.execute(
            "UPDATE catalog_sync_runs SET pages_processed=?, products_received=?, "
            "products_created=?, products_updated=?, products_unchanged=?, "
            "products_conflicted=? WHERE id=?",
            (
                pages, received, totals["created"], totals["updated"],
                totals["unchanged"], totals["conflicts"], run_id,
            ),
        )


def _finish_run(database, run_id, cursor_to, pages, received, totals):
    details = {
        "inventory_operations": 0,
        "moysklad_writes": 0,
        "cursor_advanced": True,
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE catalog_sync_runs SET status=?, finished_at=?, cursor_to=?, "
            "pages_processed=?, products_received=?, products_created=?, "
            "products_updated=?, products_unchanged=?, products_conflicted=?, "
            "details_json=? WHERE id=?",
            (
                "success", utc_now(), cursor_to, pages, received,
                totals["created"], totals["updated"], totals["unchanged"],
                totals["conflicts"], json.dumps(details, ensure_ascii=False), run_id,
            ),
        )


def _fail_run(database, run_id, error, pages, received, totals):
    with database.transaction() as connection:
        connection.execute(
            "UPDATE catalog_sync_runs SET status=?, finished_at=?, cursor_to=NULL, "
            "pages_processed=?, products_received=?, products_created=?, "
            "products_updated=?, products_unchanged=?, products_conflicted=?, "
            "errors_count=1, error_summary=?, details_json=? WHERE id=?",
            (
                "failed", utc_now(), pages, received, totals["created"], totals["updated"],
                totals["unchanged"], totals["conflicts"], type(error).__name__,
                json.dumps({"cursor_advanced": False}, ensure_ascii=False), run_id,
            ),
        )


def sync_catalog(client, database, page_size=200, cursor_from=None, progress_callback=None):
    page_size = max(1, min(int(page_size), 200))
    cursor_from = cursor_from or last_successful_cursor(database)
    if not cursor_from:
        raise RuntimeError("Initial catalog import is required before incremental sync")
    started_at = utc_now()
    run_id = _create_run(database, cursor_from, started_at)
    totals = {"created": 0, "updated": 0, "unchanged": 0, "conflicts": 0}
    pages = received = 0
    cursor_to = started_at
    importer = BitrixCatalogImporter(database)
    try:
        page = 1
        while True:
            payload = client.get_products_page(
                page=page,
                limit=page_size,
                updated_from=cursor_from,
                include_inactive=True,
            )
            pages += 1
            if pages == 1 and payload.get("generated_at"):
                cursor_to = payload["generated_at"]
            products = payload["products"]
            received += len(products)
            if products:
                result = importer.import_products(products, "full_sync")
                for key in totals:
                    totals[key] += result[key]
            _save_progress(database, run_id, pages, received, totals)
            if progress_callback:
                progress_callback({"page": page, "received": received})
            if not payload["has_more"] or not products:
                break
            page += 1
        _finish_run(database, run_id, cursor_to, pages, received, totals)
    except Exception as error:
        _fail_run(database, run_id, error, pages, received, totals)
        raise
    return {
        "status": "success",
        "sync_run_id": run_id,
        "cursor_from": cursor_from,
        "cursor_to": cursor_to,
        "cursor_advanced": True,
        "pages_processed": pages,
        "products_received": received,
        "created": totals["created"],
        "updated": totals["updated"],
        "unchanged": totals["unchanged"],
        "conflicts": totals["conflicts"],
        "inventory_operations": 0,
        "moysklad_writes": 0,
    }


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--from-cursor", default=None)
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        report = sync_catalog(
            client=BitrixCatalogReadOnlyClient(
                export_url=os.getenv("BITRIX_CATALOG_URL"),
                token=os.getenv("BITRIX_CATALOG_TOKEN"),
                max_retries=int(os.getenv("BITRIX_API_MAX_RETRIES", "3")),
            ),
            database=CatalogDatabase(),
            page_size=args.page_size,
            cursor_from=args.from_cursor,
            progress_callback=lambda state: print(
                json.dumps({"progress": state}, ensure_ascii=False), file=sys.stderr
            ),
        )
    except Exception as error:
        print("Catalog sync failed: {}".format(type(error).__name__), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
