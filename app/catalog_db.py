import os
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_DATABASE_PATH = PROJECT_ROOT / "instance" / "catalog.db"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS catalog_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_source TEXT NOT NULL DEFAULT 'bitrix',
    external_category_id TEXT NOT NULL,
    external_xml_id TEXT,
    code TEXT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES catalog_categories(id) ON DELETE SET NULL,
    sort INTEGER NOT NULL DEFAULT 500,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    path_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (external_source, external_category_id)
);

CREATE TABLE IF NOT EXISTS catalog_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT,
    article TEXT,
    barcode TEXT,
    brand TEXT,
    preview_text TEXT,
    detail_text TEXT,
    preview_text_format TEXT,
    detail_text_format TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    primary_category_id INTEGER REFERENCES catalog_categories(id) ON DELETE SET NULL,
    source_url TEXT,
    external_source TEXT NOT NULL,
    external_product_id TEXT NOT NULL,
    external_xml_id TEXT,
    external_created_at TEXT,
    external_updated_at TEXT,
    payload_hash TEXT NOT NULL,
    normalized_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    first_synced_at TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    last_sync_mode TEXT NOT NULL DEFAULT 'full_sync',
    UNIQUE (external_source, external_product_id)
);

CREATE INDEX IF NOT EXISTS idx_catalog_products_xml_id
    ON catalog_products(external_xml_id);
CREATE INDEX IF NOT EXISTS idx_catalog_products_article
    ON catalog_products(article);
CREATE INDEX IF NOT EXISTS idx_catalog_products_name
    ON catalog_products(name);
CREATE INDEX IF NOT EXISTS idx_catalog_products_barcode
    ON catalog_products(barcode COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_catalog_products_brand
    ON catalog_products(brand COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS catalog_product_categories (
    product_id INTEGER NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    sort INTEGER NOT NULL DEFAULT 500,
    PRIMARY KEY (product_id, category_id)
);

CREATE TABLE IF NOT EXISTS catalog_properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_source TEXT NOT NULL DEFAULT 'bitrix',
    external_property_id TEXT NOT NULL,
    code TEXT,
    name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    multiple INTEGER NOT NULL DEFAULT 0 CHECK (multiple IN (0, 1)),
    sort INTEGER NOT NULL DEFAULT 500,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (external_source, external_property_id)
);

CREATE TABLE IF NOT EXISTS catalog_product_property_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
    property_id INTEGER NOT NULL REFERENCES catalog_properties(id) ON DELETE CASCADE,
    value_json TEXT,
    display_value_json TEXT,
    enum_id_json TEXT,
    sort INTEGER NOT NULL DEFAULT 500,
    UNIQUE (product_id, property_id)
);

CREATE TABLE IF NOT EXISTS catalog_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
    external_source TEXT NOT NULL DEFAULT 'bitrix',
    external_offer_id TEXT NOT NULL,
    external_xml_id TEXT,
    code TEXT,
    name TEXT,
    article TEXT,
    barcode TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    external_updated_at TEXT,
    payload_hash TEXT NOT NULL,
    normalized_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (external_source, external_offer_id)
);

CREATE TABLE IF NOT EXISTS catalog_offer_property_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES catalog_offers(id) ON DELETE CASCADE,
    property_id INTEGER NOT NULL REFERENCES catalog_properties(id) ON DELETE CASCADE,
    value_json TEXT,
    display_value_json TEXT,
    enum_id_json TEXT,
    sort INTEGER NOT NULL DEFAULT 500,
    UNIQUE (offer_id, property_id)
);

CREATE TABLE IF NOT EXISTS catalog_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES catalog_products(id) ON DELETE CASCADE,
    offer_id INTEGER REFERENCES catalog_offers(id) ON DELETE CASCADE,
    external_source TEXT NOT NULL DEFAULT 'bitrix',
    external_file_id TEXT,
    image_type TEXT NOT NULL,
    original_url TEXT NOT NULL,
    filename TEXT,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    file_size INTEGER,
    sort INTEGER NOT NULL DEFAULT 500,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((product_id IS NOT NULL) != (offer_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_catalog_product_images
    ON catalog_images(product_id, original_url);
CREATE INDEX IF NOT EXISTS idx_catalog_offer_images
    ON catalog_images(offer_id, original_url);

CREATE TABLE IF NOT EXISTS catalog_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES catalog_products(id) ON DELETE CASCADE,
    offer_id INTEGER REFERENCES catalog_offers(id) ON DELETE CASCADE,
    external_source TEXT NOT NULL DEFAULT 'bitrix',
    external_price_id TEXT,
    price_type TEXT NOT NULL,
    price_name TEXT,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    is_base INTEGER NOT NULL DEFAULT 0 CHECK (is_base IN (0, 1)),
    old_amount TEXT,
    old_amount_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((product_id IS NOT NULL) != (offer_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_catalog_product_prices
    ON catalog_prices(product_id, price_type, currency);
CREATE INDEX IF NOT EXISTS idx_catalog_offer_prices
    ON catalog_prices(offer_id, price_type, currency);

CREATE TABLE IF NOT EXISTS catalog_moysklad_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
    moysklad_product_id TEXT,
    match_status TEXT NOT NULL,
    match_method TEXT,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (product_id),
    UNIQUE (moysklad_product_id)
);

CREATE TABLE IF NOT EXISTS catalog_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    cursor_from TEXT,
    cursor_to TEXT,
    pages_processed INTEGER NOT NULL DEFAULT 0,
    products_received INTEGER NOT NULL DEFAULT 0,
    products_created INTEGER NOT NULL DEFAULT 0,
    products_updated INTEGER NOT NULL DEFAULT 0,
    products_unchanged INTEGER NOT NULL DEFAULT 0,
    products_conflicted INTEGER NOT NULL DEFAULT 0,
    errors_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS catalog_excel_batches (
    id TEXT PRIMARY KEY,
    file_sha256 TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    sheet_name TEXT NOT NULL DEFAULT 'Импорт',
    source_type TEXT NOT NULL DEFAULT 'excel',
    operation_type TEXT NOT NULL DEFAULT 'initial_excel_balances',
    row_count INTEGER NOT NULL,
    total_stock REAL NOT NULL,
    positive_rows INTEGER NOT NULL,
    zero_rows INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'rolled_back')),
    previous_batch_id TEXT REFERENCES catalog_excel_batches(id) ON DELETE SET NULL,
    moysklad_sync_status TEXT NOT NULL DEFAULT 'not_linked',
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    rolled_back_at TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_batches_status
    ON catalog_excel_batches(status, applied_at);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_batches_file_sha256
    ON catalog_excel_batches(file_sha256);

CREATE TABLE IF NOT EXISTS erp_brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS erp_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES erp_brands(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (brand_id, normalized_name)
);

CREATE INDEX IF NOT EXISTS idx_erp_categories_brand
    ON erp_categories(brand_id, active, normalized_name);

CREATE TABLE IF NOT EXISTS erp_brand_categories (
    brand_id INTEGER NOT NULL REFERENCES erp_brands(id) ON DELETE RESTRICT,
    category_id INTEGER NOT NULL REFERENCES erp_categories(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (brand_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_erp_brand_categories_category
    ON erp_brand_categories(category_id, brand_id);

CREATE TABLE IF NOT EXISTS erp_catalog_normalization_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('brand', 'category', 'product')),
    normalized_name TEXT NOT NULL,
    canonical_id TEXT,
    canonical_name TEXT NOT NULL,
    variant_name TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    resolution TEXT NOT NULL CHECK (resolution IN ('linked', 'ambiguous')),
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (entity_type, normalized_name, canonical_id, variant_name)
);

CREATE TABLE IF NOT EXISTS erp_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS erp_legacy_catalog_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('sale', 'receipt')),
    entity_id TEXT NOT NULL,
    position_index INTEGER NOT NULL DEFAULT 0,
    product_id INTEGER NOT NULL
        REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,
    match_method TEXT NOT NULL,
    snapshot_product_id TEXT,
    snapshot_name TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, position_index)
);

CREATE INDEX IF NOT EXISTS idx_erp_legacy_catalog_links_product
    ON erp_legacy_catalog_links(product_id, entity_type);

CREATE TABLE IF NOT EXISTS erp_legacy_catalog_ambiguities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('sale', 'receipt')),
    entity_id TEXT NOT NULL,
    position_index INTEGER NOT NULL DEFAULT 0,
    snapshot_product_id TEXT,
    snapshot_name TEXT,
    candidate_product_ids_json TEXT NOT NULL DEFAULT '[]',
    resolution TEXT NOT NULL DEFAULT 'manual_review',
    created_at TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, position_index)
);

CREATE TABLE IF NOT EXISTS catalog_excel_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    created_batch_id TEXT NOT NULL REFERENCES catalog_excel_batches(id),
    current_batch_id TEXT NOT NULL REFERENCES catalog_excel_batches(id),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    raw_excel_json TEXT NOT NULL,
    excel_row INTEGER NOT NULL,
    excel_name_raw TEXT NOT NULL,
    model TEXT,
    normalized_name TEXT NOT NULL,
    excel_article TEXT,
    article_quality TEXT NOT NULL,
    excel_brand TEXT NOT NULL,
    excel_category TEXT,
    brand_id INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT,
    category_id INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT,
    stock REAL NOT NULL,
    cell TEXT,
    stock_source TEXT NOT NULL DEFAULT 'excel',
    file_sha256 TEXT NOT NULL,
    match_status TEXT NOT NULL,
    match_method TEXT NOT NULL,
    match_confidence REAL NOT NULL DEFAULT 0,
    match_decision TEXT NOT NULL,
    candidates_json TEXT NOT NULL DEFAULT '[]',
    bitrix_link_cardinality TEXT NOT NULL DEFAULT 'unlinked',
    shared_bitrix_row_count INTEGER NOT NULL DEFAULT 0,
    bitrix_catalog_product_id INTEGER REFERENCES catalog_products(id) ON DELETE SET NULL,
    moysklad_product_id TEXT,
    bitrix_external_product_id TEXT,
    bitrix_xml_id TEXT,
    bitrix_name TEXT,
    bitrix_brand TEXT,
    bitrix_category TEXT,
    bitrix_source_url TEXT,
    bitrix_primary_image_url TEXT,
    bitrix_thumbnail_url TEXT,
    bitrix_gallery_json TEXT NOT NULL DEFAULT '[]',
    bitrix_price_amount TEXT,
    bitrix_price_currency TEXT,
    bitrix_description TEXT,
    bitrix_properties_json TEXT NOT NULL DEFAULT '[]',
    bitrix_active INTEGER CHECK (bitrix_active IN (0, 1)),
    moysklad_sync_status TEXT NOT NULL DEFAULT 'not_linked',
    deleted_at TEXT,
    deleted_by TEXT,
    deleted_stock REAL,
    delete_mode TEXT CHECK (delete_mode IN ('normal', 'force')),
    deleted_source_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS erp_out_of_stock_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL
        REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_erp_out_of_stock_cycles_product
    ON erp_out_of_stock_cycles(product_id, started_at DESC);

CREATE TABLE IF NOT EXISTS erp_out_of_stock_checks (
    cycle_id INTEGER NOT NULL
        REFERENCES erp_out_of_stock_cycles(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN ('ziiiro', 'wildberries', 'tictactoy')),
    checked INTEGER NOT NULL DEFAULT 0 CHECK (checked IN (0, 1)),
    changed_at TEXT NOT NULL,
    changed_by TEXT,
    PRIMARY KEY (cycle_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_active
    ON catalog_excel_products(active, current_batch_id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_match_status
    ON catalog_excel_products(match_status);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_bitrix
    ON catalog_excel_products(bitrix_catalog_product_id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_bitrix_external
    ON catalog_excel_products(bitrix_external_product_id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_bitrix_xml
    ON catalog_excel_products(bitrix_xml_id COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_article
    ON catalog_excel_products(excel_article COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_normalized_name
    ON catalog_excel_products(normalized_name);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_listing_name
    ON catalog_excel_products(active, excel_name_raw COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_listing_brand
    ON catalog_excel_products(active, excel_brand COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_listing_category
    ON catalog_excel_products(active, excel_category COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_listing_stock
    ON catalog_excel_products(active, stock, id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_listing_cell
    ON catalog_excel_products(active, cell COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_listing_created
    ON catalog_excel_products(active, created_at, id);

CREATE TABLE IF NOT EXISTS catalog_product_classification_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    product_id INTEGER NOT NULL
        REFERENCES catalog_excel_products(id) ON DELETE CASCADE,
    bitrix_catalog_product_id INTEGER
        REFERENCES catalog_products(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('updated', 'ambiguous')),
    reason TEXT NOT NULL,
    previous_brand TEXT,
    new_brand TEXT,
    previous_category TEXT,
    new_category TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalog_product_classification_audit_product
    ON catalog_product_classification_audit(product_id, created_at);

CREATE TABLE IF NOT EXISTS catalog_excel_batch_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES catalog_excel_batches(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES catalog_excel_products(id) ON DELETE SET NULL,
    source_key TEXT NOT NULL,
    excel_row INTEGER,
    row_kind TEXT NOT NULL CHECK (row_kind IN ('excel_row', 'deactivated')),
    created_product INTEGER NOT NULL DEFAULT 0 CHECK (created_product IN (0, 1)),
    previous_state_json TEXT,
    applied_state_json TEXT NOT NULL,
    stock_before REAL NOT NULL,
    stock_after REAL NOT NULL,
    stock_difference REAL NOT NULL,
    match_status TEXT NOT NULL,
    bitrix_link_cardinality TEXT NOT NULL DEFAULT 'unlinked',
    shared_bitrix_row_count INTEGER NOT NULL DEFAULT 0,
    bitrix_xml_id TEXT,
    operation_result TEXT NOT NULL CHECK (
        operation_result IN ('adjusted', 'already_at_target')
    ),
    created_at TEXT NOT NULL,
    UNIQUE (batch_id, source_key)
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_batch_rows_product
    ON catalog_excel_batch_rows(product_id, batch_id);

CREATE TABLE IF NOT EXISTS catalog_excel_stock_operations (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES catalog_excel_batches(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES catalog_excel_products(id) ON DELETE SET NULL,
    operation_type TEXT NOT NULL CHECK (
        operation_type IN ('initial_excel_adjustment', 'excel_batch_rollback')
    ),
    stock_before REAL NOT NULL,
    stock_after REAL NOT NULL,
    stock_difference REAL NOT NULL,
    reversal_of TEXT REFERENCES catalog_excel_stock_operations(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_stock_operations_batch
    ON catalog_excel_stock_operations(batch_id, created_at);

CREATE TABLE IF NOT EXISTS catalog_excel_match_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL REFERENCES catalog_excel_batches(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('confirm_bitrix', 'not_in_bitrix', 'unlink', 'undo')),
    previous_state_json TEXT NOT NULL,
    new_state_json TEXT NOT NULL,
    reverses_audit_id INTEGER REFERENCES catalog_excel_match_audit(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_match_audit_product
    ON catalog_excel_match_audit(product_id, created_at);

CREATE TABLE IF NOT EXISTS catalog_excel_import_drafts (
    id TEXT PRIMARY KEY,
    file_sha256 TEXT NOT NULL UNIQUE,
    source_filename TEXT NOT NULL,
    source_file BLOB NOT NULL,
    sheet_name TEXT NOT NULL,
    header_row INTEGER NOT NULL,
    parser_version INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL CHECK (status IN ('ready', 'blocked', 'posted')),
    row_count INTEGER NOT NULL,
    valid_rows INTEGER NOT NULL,
    error_rows INTEGER NOT NULL,
    excluded_rows INTEGER NOT NULL,
    positive_rows INTEGER NOT NULL DEFAULT 0,
    zero_rows INTEGER NOT NULL DEFAULT 0,
    new_rows INTEGER NOT NULL,
    matched_rows INTEGER NOT NULL,
    total_quantity REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_import_drafts_status
    ON catalog_excel_import_drafts(status, created_at);

CREATE TABLE IF NOT EXISTS catalog_excel_import_draft_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL REFERENCES catalog_excel_import_drafts(id) ON DELETE CASCADE,
    excel_row INTEGER NOT NULL,
    row_status TEXT NOT NULL CHECK (row_status IN ('valid', 'error', 'excluded')),
    raw_values_json TEXT NOT NULL,
    data_json TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    match_status TEXT,
    match_method TEXT,
    match_confidence REAL,
    catalog_product_id INTEGER REFERENCES catalog_products(id) ON DELETE SET NULL,
    candidates_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (draft_id, excel_row)
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_import_draft_rows_status
    ON catalog_excel_import_draft_rows(draft_id, row_status, excel_row);

CREATE TABLE IF NOT EXISTS catalog_excel_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT UNIQUE,
    draft_id TEXT NOT NULL UNIQUE REFERENCES catalog_excel_import_drafts(id),
    source_filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL UNIQUE,
    sheet_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status = 'posted'),
    row_count INTEGER NOT NULL,
    total_quantity REAL NOT NULL,
    new_cards INTEGER NOT NULL,
    matched_cards INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_receipts_posted
    ON catalog_excel_receipts(posted_at, id);

CREATE TABLE IF NOT EXISTS catalog_excel_receipt_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES catalog_excel_receipts(id) ON DELETE CASCADE,
    draft_row_id INTEGER NOT NULL REFERENCES catalog_excel_import_draft_rows(id),
    product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id),
    excel_row INTEGER NOT NULL,
    excel_name TEXT NOT NULL,
    excel_article TEXT,
    excel_brand TEXT NOT NULL,
    excel_category TEXT,
    brand_id INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT,
    category_id INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT,
    cell TEXT,
    quantity REAL NOT NULL CHECK (quantity >= 0),
    stock_before REAL NOT NULL,
    stock_after REAL NOT NULL,
    created_product INTEGER NOT NULL CHECK (created_product IN (0, 1)),
    match_status TEXT NOT NULL,
    bitrix_catalog_product_id INTEGER REFERENCES catalog_products(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE (receipt_id, draft_row_id)
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_receipt_rows_product
    ON catalog_excel_receipt_rows(product_id, receipt_id);

CREATE TABLE IF NOT EXISTS catalog_excel_receipt_operations (
    id TEXT PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES catalog_excel_receipts(id) ON DELETE CASCADE,
    receipt_row_id INTEGER NOT NULL UNIQUE REFERENCES catalog_excel_receipt_rows(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id),
    stock_before REAL NOT NULL,
    stock_after REAL NOT NULL,
    stock_difference REAL NOT NULL CHECK (stock_difference > 0),
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_receipt_operations_receipt
    ON catalog_excel_receipt_operations(receipt_id, created_at);

CREATE TABLE IF NOT EXISTS catalog_excel_manual_stock_operations (
    id TEXT PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id),
    stock_before REAL NOT NULL,
    stock_after REAL NOT NULL,
    stock_difference REAL NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_manual_stock_product
    ON catalog_excel_manual_stock_operations(product_id, created_at);

CREATE TABLE IF NOT EXISTS erp_sales (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_order_id TEXT,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'completed' CHECK (
        status IN ('completed', 'partially_returned', 'returned')
    ),
    created_at TEXT NOT NULL,
    returned_at TEXT,
    return_reason TEXT,
    cancelled_at TEXT,
    cancellation_reason TEXT,
    cancellation_comment TEXT,
    cancelled_by TEXT,
    deleted_at TEXT,
    deleted_by TEXT,
    archived_at TEXT,
    archived_by TEXT,
    user_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    inserted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_erp_sales_status_created
    ON erp_sales(status, created_at);

CREATE TABLE IF NOT EXISTS erp_sale_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id TEXT NOT NULL REFERENCES erp_sales(id) ON DELETE RESTRICT,
    product_id INTEGER NOT NULL
        REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,
    brand_id INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT,
    category_id INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT,
    quantity REAL NOT NULL CHECK (quantity > 0),
    original_unit_price TEXT,
    discount_type TEXT NOT NULL DEFAULT 'none' CHECK (
        discount_type IN ('none', 'percent', 'fixed')
    ),
    discount_value TEXT NOT NULL DEFAULT '0.00',
    discount_amount TEXT NOT NULL DEFAULT '0.00',
    discount_reason TEXT,
    unit_price REAL CHECK (unit_price >= 0),
    returned_quantity REAL NOT NULL DEFAULT 0 CHECK (
        returned_quantity >= 0 AND returned_quantity <= quantity
    ),
    status TEXT NOT NULL DEFAULT 'completed' CHECK (
        status IN ('completed', 'partially_returned', 'returned')
    ),
    created_at TEXT NOT NULL,
    returned_at TEXT,
    return_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_erp_sale_items_sale
    ON erp_sale_items(sale_id, id);
CREATE INDEX IF NOT EXISTS idx_erp_sale_items_product
    ON erp_sale_items(product_id, created_at);

CREATE TABLE IF NOT EXISTS erp_receipts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    number TEXT,
    comment TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'posted', 'cancelled')
    ),
    receipt_date TEXT NOT NULL,
    user_name TEXT,
    idempotency_key TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancelled_at TEXT,
    cancelled_by TEXT,
    cancellation_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_erp_receipts_status_date
    ON erp_receipts(status, receipt_date);

CREATE TABLE IF NOT EXISTS erp_receipt_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id TEXT NOT NULL REFERENCES erp_receipts(id) ON DELETE RESTRICT,
    product_id INTEGER NOT NULL
        REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,
    brand_id INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT,
    category_id INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT,
    quantity REAL NOT NULL CHECK (quantity > 0),
    purchase_price REAL CHECK (purchase_price >= 0),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_erp_receipt_items_receipt
    ON erp_receipt_items(receipt_id, id);
CREATE INDEX IF NOT EXISTS idx_erp_receipt_items_product
    ON erp_receipt_items(product_id, created_at);

CREATE TABLE IF NOT EXISTS erp_receipt_recovery_audit (
    id TEXT PRIMARY KEY,
    receipt_id TEXT,
    receipt_number TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('apply')),
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_erp_receipt_recovery_audit_receipt
    ON erp_receipt_recovery_audit(receipt_number, created_at);

CREATE TABLE IF NOT EXISTS catalog_stock_movements (
    id TEXT PRIMARY KEY,
    product_id INTEGER NOT NULL
        REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,
    movement_type TEXT NOT NULL CHECK (
        movement_type IN (
            'initial_stock',
            'receipt',
            'sale',
            'return',
            'cancellation',
            'manual_adjustment',
            'inventory_adjustment'
        )
    ),
    quantity_delta REAL NOT NULL CHECK (quantity_delta != 0),
    stock_before REAL,
    stock_after REAL NOT NULL CHECK (stock_after >= 0),
    sale_id TEXT REFERENCES erp_sales(id) ON DELETE RESTRICT,
    sale_item_id INTEGER
        REFERENCES erp_sale_items(id) ON DELETE RESTRICT,
    receipt_id TEXT REFERENCES erp_receipts(id) ON DELETE RESTRICT,
    receipt_item_id INTEGER
        REFERENCES erp_receipt_items(id) ON DELETE RESTRICT,
    idempotency_key TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    source_type TEXT,
    source_id TEXT,
    source_line_id TEXT,
    operation_kind TEXT,
    source_number TEXT,
    source TEXT,
    user_name TEXT,
    comment TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalog_stock_movements_product
    ON catalog_stock_movements(product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_catalog_stock_movements_sale
    ON catalog_stock_movements(sale_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_stock_movements_idempotency
    ON catalog_stock_movements(idempotency_key);

CREATE TABLE IF NOT EXISTS erp_inventory_sessions (
    id TEXT PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES erp_brands(id) ON DELETE RESTRICT,
    active_brand_id INTEGER UNIQUE REFERENCES erp_brands(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'completed', 'cancelled')),
    started_by TEXT,
    completed_by TEXT,
    cancelled_by TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    cancelled_at TEXT,
    cancelled_reason TEXT,
    start_positions INTEGER NOT NULL DEFAULT 0,
    checked_positions INTEGER NOT NULL DEFAULT 0,
    adjusted_positions INTEGER NOT NULL DEFAULT 0,
    added_positions INTEGER NOT NULL DEFAULT 0,
    missing_positions INTEGER NOT NULL DEFAULT 0,
    total_delta INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_erp_inventory_brand_status
    ON erp_inventory_sessions(brand_id, status);
CREATE INDEX IF NOT EXISTS idx_erp_inventory_sessions_status
    ON erp_inventory_sessions(status, started_at DESC);

CREATE TABLE IF NOT EXISTS erp_inventory_items (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES erp_inventory_sessions(id) ON DELETE RESTRICT,
    product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,
    snapshot_stock INTEGER NOT NULL CHECK (snapshot_stock >= 0),
    actual_stock INTEGER CHECK (actual_stock >= 0),
    final_stock INTEGER CHECK (final_stock >= 0),
    quantity_delta INTEGER,
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'confirmed', 'adjusted', 'conflict', 'added', 'missing', 'error'
    )),
    appearance TEXT NOT NULL CHECK (appearance IN ('snapshot', 'existing', 'new')),
    snapshot_at TEXT NOT NULL,
    snapshot_movement_rowid INTEGER NOT NULL DEFAULT 0,
    confirmed_by TEXT,
    confirmed_at TEXT,
    movement_id TEXT REFERENCES catalog_stock_movements(id) ON DELETE RESTRICT,
    idempotency_key TEXT UNIQUE,
    error_message TEXT,
    reactivated INTEGER NOT NULL DEFAULT 0 CHECK (reactivated IN (0, 1)),
    UNIQUE (session_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_erp_inventory_items_queue
    ON erp_inventory_items(session_id, status, id);
CREATE INDEX IF NOT EXISTS idx_erp_inventory_items_product
    ON erp_inventory_items(product_id, snapshot_at);

CREATE TABLE IF NOT EXISTS erp_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK (
        entity_type IN ('product', 'sale', 'receipt', 'brand', 'category', 'inventory')
    ),
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id TEXT,
    actor_type TEXT NOT NULL DEFAULT 'user' CHECK (
        actor_type IN ('user', 'system', 'external')
    ),
    actor_display_name_snapshot TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    object_label_snapshot TEXT NOT NULL,
    object_secondary_snapshot TEXT NOT NULL DEFAULT '',
    changes_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    search_text TEXT NOT NULL DEFAULT '',
    status_snapshot TEXT NOT NULL DEFAULT '',
    source_snapshot TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_erp_audit_occurred
    ON erp_audit_events(occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_erp_audit_entity_occurred
    ON erp_audit_events(entity_type, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_erp_audit_entity_object
    ON erp_audit_events(entity_type, entity_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_erp_audit_actor
    ON erp_audit_events(actor_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_erp_audit_action
    ON erp_audit_events(action, occurred_at DESC);
"""


class CatalogDatabase:
    _schema_cache = {}
    _schema_cache_lock = threading.Lock()

    def __init__(self, path=None, cache_initialization=False):
        configured_path = path or os.getenv("CATALOG_DATABASE_PATH")
        self.path = Path(configured_path) if configured_path else DEFAULT_CATALOG_DATABASE_PATH
        self.cache_initialization = bool(cache_initialization)
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def _schema_cache_identity(self):
        if str(self.path) == ":memory:":
            return None
        try:
            resolved = self.path.resolve()
            stat = resolved.stat()
        except OSError:
            return None
        return (str(resolved), stat.st_dev, stat.st_ino)

    def connect(self):
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self):
        if not self.cache_initialization:
            return self._initialize_schema()
        if self._initialized:
            return None
        with self._initialize_lock:
            if self._initialized:
                return None
            cache_path = (
                str(self.path.resolve())
                if str(self.path) != ":memory:"
                else None
            )
            with self._schema_cache_lock:
                identity = self._schema_cache_identity()
                if (
                    cache_path is not None
                    and identity is not None
                    and self._schema_cache.get(cache_path) == identity
                ):
                    self._initialized = True
                    return None
                self._initialize_schema()
                identity = self._schema_cache_identity()
                if cache_path is not None and identity is not None:
                    self._schema_cache[cache_path] = identity
                self._initialized = True
        return None

    def _initialize_schema(self):
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_audit_entity_constraints(connection)
            self._ensure_excel_receipt_constraints(connection)
            self._ensure_excel_cardinality_columns(connection)
            self._ensure_product_deletion_columns(connection)
            self._ensure_product_workflow_columns(connection)
            self._ensure_receipt_constraints(connection)
            self._ensure_optional_price_constraints(connection)
            self._ensure_shared_catalog(connection)
            self._ensure_brand_category_relations(connection)
            self._ensure_inventory_constraints(connection)
            self._ensure_stock_movement_constraints(connection)

    @staticmethod
    def _ensure_inventory_constraints(connection):
        """Keep active-session uniqueness compatible with production SQLite 3.7."""
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(erp_inventory_sessions)"
            ).fetchall()
        }
        if "active_brand_id" not in columns:
            connection.execute(
                "ALTER TABLE erp_inventory_sessions ADD COLUMN active_brand_id INTEGER "
                "REFERENCES erp_brands(id) ON DELETE RESTRICT"
            )
        item_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(erp_inventory_items)"
            ).fetchall()
        }
        if "reactivated" not in item_columns:
            connection.execute(
                "ALTER TABLE erp_inventory_items ADD COLUMN reactivated INTEGER "
                "NOT NULL DEFAULT 0 CHECK (reactivated IN (0, 1))"
            )
        connection.execute(
            "UPDATE erp_inventory_sessions SET active_brand_id = brand_id "
            "WHERE status = 'active' AND active_brand_id IS NULL"
        )
        connection.execute(
            "UPDATE erp_inventory_sessions SET active_brand_id = NULL "
            "WHERE status <> 'active' AND active_brand_id IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_erp_inventory_one_active_brand "
            "ON erp_inventory_sessions(active_brand_id)"
        )

    @staticmethod
    def _ensure_audit_entity_constraints(connection):
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'erp_audit_events'"
        ).fetchone()
        if row is None or "'inventory'" in (row["sql"] or ""):
            return
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ALTER TABLE erp_audit_events RENAME TO erp_audit_events_old")
            connection.execute(
                "CREATE TABLE erp_audit_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "entity_type TEXT NOT NULL CHECK (entity_type IN "
                "('product','sale','receipt','brand','category','inventory')), "
                "entity_id TEXT NOT NULL, action TEXT NOT NULL, actor_id TEXT, "
                "actor_type TEXT NOT NULL DEFAULT 'user' CHECK (actor_type IN "
                "('user','system','external')), "
                "actor_display_name_snapshot TEXT NOT NULL, occurred_at TEXT NOT NULL, "
                "object_label_snapshot TEXT NOT NULL, "
                "object_secondary_snapshot TEXT NOT NULL DEFAULT '', "
                "changes_json TEXT NOT NULL DEFAULT '{}', "
                "metadata_json TEXT NOT NULL DEFAULT '{}', "
                "search_text TEXT NOT NULL DEFAULT '', "
                "status_snapshot TEXT NOT NULL DEFAULT '', "
                "source_snapshot TEXT NOT NULL DEFAULT '')"
            )
            connection.execute(
                "INSERT INTO erp_audit_events SELECT * FROM erp_audit_events_old"
            )
            connection.execute("DROP TABLE erp_audit_events_old")
            connection.execute(
                "CREATE INDEX idx_erp_audit_occurred ON "
                "erp_audit_events(occurred_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX idx_erp_audit_entity_occurred ON "
                "erp_audit_events(entity_type, occurred_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX idx_erp_audit_entity_object ON "
                "erp_audit_events(entity_type, entity_id, occurred_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX idx_erp_audit_actor ON "
                "erp_audit_events(actor_id, occurred_at DESC)"
            )
            connection.execute(
                "CREATE INDEX idx_erp_audit_action ON "
                "erp_audit_events(action, occurred_at DESC)"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _ensure_brand_category_relations(connection):
        version = "2026-08-12-brand-category-relations-v2-no-zero"
        if connection.execute(
            "SELECT 1 FROM erp_schema_migrations WHERE version = ?",
            (version,),
        ).fetchone() is not None:
            return
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        connection.execute(
            "DELETE FROM erp_brand_categories WHERE category_id = 0"
        )
        connection.execute(
            "INSERT OR IGNORE INTO erp_brand_categories "
            "(brand_id, category_id, created_at) "
            "SELECT brand_id, id, ? FROM erp_categories WHERE id <> 0",
            (now,),
        )
        connection.execute(
            "INSERT OR IGNORE INTO erp_brand_categories "
            "(brand_id, category_id, created_at) "
            "SELECT DISTINCT brand_id, category_id, ? "
            "FROM catalog_excel_products WHERE active = 1 AND brand_id IS NOT NULL "
            "AND category_id IS NOT NULL AND category_id <> 0",
            (now,),
        )
        connection.execute(
            "INSERT INTO erp_schema_migrations "
            "(version, applied_at, details_json) VALUES (?, ?, ?)",
            (version, now, '{"backfill": "erp_brand_categories"}'),
        )

    @staticmethod
    def _ensure_excel_receipt_constraints(connection):
        batch_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'catalog_excel_batches'"
        ).fetchone()
        receipt_row_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'catalog_excel_receipt_rows'"
        ).fetchone()
        batch_sql = " ".join((batch_sql_row[0] or "").lower().split())
        receipt_row_sql = " ".join((receipt_row_sql_row[0] or "").lower().split())
        migrate_batches = "file_sha256 text not null unique" in batch_sql
        migrate_receipt_rows = "check (quantity > 0)" in receipt_row_sql
        if not migrate_batches and not migrate_receipt_rows:
            return

        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if migrate_batches:
                connection.execute("""
                    CREATE TABLE catalog_excel_batches_migrating (
                        id TEXT PRIMARY KEY,
                        file_sha256 TEXT NOT NULL,
                        source_filename TEXT NOT NULL,
                        sheet_name TEXT NOT NULL DEFAULT 'Импорт',
                        source_type TEXT NOT NULL DEFAULT 'excel',
                        operation_type TEXT NOT NULL DEFAULT 'initial_excel_balances',
                        row_count INTEGER NOT NULL,
                        total_stock REAL NOT NULL,
                        positive_rows INTEGER NOT NULL,
                        zero_rows INTEGER NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('active', 'superseded', 'rolled_back')
                        ),
                        previous_batch_id TEXT REFERENCES catalog_excel_batches(id)
                            ON DELETE SET NULL,
                        moysklad_sync_status TEXT NOT NULL DEFAULT 'not_linked',
                        created_at TEXT NOT NULL,
                        applied_at TEXT NOT NULL,
                        rolled_back_at TEXT,
                        details_json TEXT NOT NULL DEFAULT '{}'
                    )
                """)
                connection.execute(
                    "INSERT INTO catalog_excel_batches_migrating "
                    "SELECT * FROM catalog_excel_batches"
                )
                connection.execute("DROP TABLE catalog_excel_batches")
                connection.execute(
                    "ALTER TABLE catalog_excel_batches_migrating "
                    "RENAME TO catalog_excel_batches"
                )
                connection.execute(
                    "CREATE INDEX idx_catalog_excel_batches_status "
                    "ON catalog_excel_batches(status, applied_at)"
                )
                connection.execute(
                    "CREATE INDEX idx_catalog_excel_batches_file_sha256 "
                    "ON catalog_excel_batches(file_sha256)"
                )

            if migrate_receipt_rows:
                connection.execute("""
                    CREATE TABLE catalog_excel_receipt_rows_migrating (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        receipt_id INTEGER NOT NULL REFERENCES catalog_excel_receipts(id)
                            ON DELETE CASCADE,
                        draft_row_id INTEGER NOT NULL
                            REFERENCES catalog_excel_import_draft_rows(id),
                        product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id),
                        excel_row INTEGER NOT NULL,
                        excel_name TEXT NOT NULL,
                        excel_article TEXT,
                        excel_brand TEXT NOT NULL,
                        excel_category TEXT,
                        cell TEXT,
                        quantity REAL NOT NULL CHECK (quantity >= 0),
                        stock_before REAL NOT NULL,
                        stock_after REAL NOT NULL,
                        created_product INTEGER NOT NULL CHECK (
                            created_product IN (0, 1)
                        ),
                        match_status TEXT NOT NULL,
                        bitrix_catalog_product_id INTEGER
                            REFERENCES catalog_products(id) ON DELETE SET NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE (receipt_id, draft_row_id)
                    )
                """)
                connection.execute(
                    "INSERT INTO catalog_excel_receipt_rows_migrating "
                    "SELECT * FROM catalog_excel_receipt_rows"
                )
                connection.execute("DROP TABLE catalog_excel_receipt_rows")
                connection.execute(
                    "ALTER TABLE catalog_excel_receipt_rows_migrating "
                    "RENAME TO catalog_excel_receipt_rows"
                )
                connection.execute(
                    "CREATE INDEX idx_catalog_excel_receipt_rows_product "
                    "ON catalog_excel_receipt_rows(product_id, receipt_id)"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                "Excel receipt schema migration created foreign key violations"
            )

    @staticmethod
    def _ensure_excel_cardinality_columns(connection):
        migrations = {
            "catalog_excel_products": (
                ("bitrix_link_cardinality", "TEXT NOT NULL DEFAULT 'unlinked'"),
                ("shared_bitrix_row_count", "INTEGER NOT NULL DEFAULT 0"),
            ),
            "catalog_excel_batch_rows": (
                ("bitrix_link_cardinality", "TEXT NOT NULL DEFAULT 'unlinked'"),
                ("shared_bitrix_row_count", "INTEGER NOT NULL DEFAULT 0"),
            ),
            "catalog_excel_import_drafts": (
                ("parser_version", "INTEGER NOT NULL DEFAULT 1"),
                ("positive_rows", "INTEGER NOT NULL DEFAULT 0"),
                ("zero_rows", "INTEGER NOT NULL DEFAULT 0"),
            ),
        }
        migrated = False
        for table, columns in migrations.items():
            existing = {
                row[1] for row in connection.execute("PRAGMA table_info({})".format(table))
            }
            for column, definition in columns:
                if column not in existing:
                    connection.execute(
                        "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, definition)
                    )

                    migrated = True
        if migrated:
            linked = connection.execute(
                "SELECT bitrix_catalog_product_id, COUNT(*) AS row_count "
                "FROM catalog_excel_products WHERE active = 1 "
                "AND bitrix_catalog_product_id IS NOT NULL "
                "GROUP BY bitrix_catalog_product_id"
            ).fetchall()
            for row in linked:
                connection.execute(
                    "UPDATE catalog_excel_products SET bitrix_link_cardinality = ?, "
                    "shared_bitrix_row_count = ? WHERE active = 1 "
                    "AND bitrix_catalog_product_id = ?",
                    (
                        "many_to_one" if row["row_count"] > 1 else "one_to_one",
                        row["row_count"],
                        row["bitrix_catalog_product_id"],
                    ),
                )

    @staticmethod
    def _ensure_product_deletion_columns(connection):
        """Add irreversible catalog tombstone metadata without rewriting rows."""
        definitions = (
            ("deleted_at", "TEXT"),
            ("deleted_by", "TEXT"),
            ("deleted_stock", "REAL"),
            (
                "delete_mode",
                "TEXT CHECK (delete_mode IN ('normal', 'force'))",
            ),
            ("deleted_source_key", "TEXT"),
        )
        existing = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(catalog_excel_products)"
            )
        }
        for column, definition in definitions:
            if column not in existing:
                connection.execute(
                    "ALTER TABLE catalog_excel_products ADD COLUMN {} {}".format(
                        column,
                        definition,
                    )
                )

    @staticmethod
    def _ensure_product_workflow_columns(connection):
        """Add optional catalog fields without rewriting existing product rows."""
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(catalog_excel_products)"
            )
        }
        if "model" not in columns:
            connection.execute(
                "ALTER TABLE catalog_excel_products ADD COLUMN model TEXT"
            )

    @staticmethod
    def _ensure_receipt_constraints(connection):
        """Add drafts and tenant-scoped receipt idempotency without data loss."""
        table = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'erp_receipts'"
        ).fetchone()
        if table is None:
            return
        table_sql = " ".join((table["sql"] or "").lower().split())
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(erp_receipts)"
            )
        }
        migrate = (
            "tenant_id" not in columns
            or "'draft'" not in table_sql
            or "idempotency_key text unique" in table_sql
        )
        if migrate:
            connection.commit()
            connection.execute("PRAGMA foreign_keys = OFF")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE erp_receipts_migrating ("
                    "id TEXT PRIMARY KEY, "
                    "tenant_id TEXT NOT NULL DEFAULT 'default', "
                    "number TEXT, "
                    "status TEXT NOT NULL DEFAULT 'draft' CHECK ("
                    "status IN ('draft', 'posted', 'cancelled')), "
                    "receipt_date TEXT NOT NULL, user_name TEXT, "
                    "idempotency_key TEXT, "
                    "metadata_json TEXT NOT NULL DEFAULT '{}', "
                    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                    "cancelled_at TEXT)"
                )
                tenant_expression = (
                    "COALESCE(NULLIF(trim(tenant_id), ''), 'default')"
                    if "tenant_id" in columns
                    else "'default'"
                )
                connection.execute(
                    "INSERT INTO erp_receipts_migrating "
                    "(id, tenant_id, number, status, receipt_date, user_name, "
                    "idempotency_key, metadata_json, created_at, updated_at, "
                    "cancelled_at) "
                    "SELECT id, {}, number, status, receipt_date, user_name, "
                    "idempotency_key, metadata_json, created_at, updated_at, "
                    "cancelled_at FROM erp_receipts".format(tenant_expression)
                )
                connection.execute("DROP TABLE erp_receipts")
                connection.execute(
                    "ALTER TABLE erp_receipts_migrating "
                    "RENAME TO erp_receipts"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("PRAGMA foreign_keys = ON")

            violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    "Receipt schema migration created foreign key violations"
                )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_erp_receipts_status_date "
            "ON erp_receipts(status, receipt_date)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_erp_receipts_tenant_idempotency "
            "ON erp_receipts(tenant_id, idempotency_key)"
        )

    @staticmethod
    def _ensure_optional_price_constraints(connection):
        """Allow an unknown price while preserving real zero values."""
        definitions = {
            "erp_sale_items": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "sale_id TEXT NOT NULL REFERENCES erp_sales(id) ON DELETE RESTRICT, "
                "product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE RESTRICT, "
                "brand_id INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT, "
                "category_id INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT, "
                "quantity REAL NOT NULL CHECK (quantity > 0), "
                "unit_price REAL CHECK (unit_price >= 0), "
                "returned_quantity REAL NOT NULL DEFAULT 0 CHECK (returned_quantity >= 0 AND returned_quantity <= quantity), "
                "status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('completed', 'partially_returned', 'returned')), "
                "created_at TEXT NOT NULL, returned_at TEXT, return_reason TEXT"
            ),
            "erp_receipt_items": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "receipt_id TEXT NOT NULL REFERENCES erp_receipts(id) ON DELETE RESTRICT, "
                "product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE RESTRICT, "
                "brand_id INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT, "
                "category_id INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT, "
                "quantity REAL NOT NULL CHECK (quantity > 0), "
                "purchase_price REAL CHECK (purchase_price >= 0), "
                "active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)), "
                "created_at TEXT NOT NULL"
            ),
        }
        columns = {
            "erp_sale_items": "unit_price",
            "erp_receipt_items": "purchase_price",
        }
        migrated = False
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            for table, definition in definitions.items():
                info = connection.execute(
                    "PRAGMA table_info({})".format(table)
                ).fetchall()
                price_column = columns[table]
                price_info = next(
                    (row for row in info if row["name"] == price_column),
                    None,
                )
                if price_info is None or not price_info["notnull"]:
                    continue
                migrated = True
                names = [row["name"] for row in info]
                quoted = ", ".join('"{}"'.format(name) for name in names)
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE {}_migrating ({})".format(table, definition)
                )
                connection.execute(
                    "INSERT INTO {0}_migrating ({1}) SELECT {1} FROM {0}".format(
                        table,
                        quoted,
                    )
                )
                connection.execute("DROP TABLE {}".format(table))
                connection.execute(
                    "ALTER TABLE {}_migrating RENAME TO {}".format(table, table)
                )
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
        if migrated and connection.execute("PRAGMA foreign_key_check").fetchall():
            raise sqlite3.IntegrityError(
                "Optional price migration created foreign key violations"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_erp_sale_items_sale ON erp_sale_items(sale_id, id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_erp_sale_items_product ON erp_sale_items(product_id, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_erp_receipt_items_receipt ON erp_receipt_items(receipt_id, id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_erp_receipt_items_product ON erp_receipt_items(product_id, created_at)"
        )

    @staticmethod
    def _ensure_shared_catalog(connection):
        """Add stable taxonomy IDs and safely backfill exact normalized matches."""
        migrations = {
            "catalog_excel_products": (
                ("brand_id", "INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT"),
                ("category_id", "INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT"),
                ("moysklad_product_id", "TEXT"),
            ),
            "catalog_excel_receipt_rows": (
                ("brand_id", "INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT"),
                ("category_id", "INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT"),
            ),
            "erp_sale_items": (
                ("brand_id", "INTEGER REFERENCES erp_brands(id) ON DELETE RESTRICT"),
                ("category_id", "INTEGER REFERENCES erp_categories(id) ON DELETE RESTRICT"),
                ("original_unit_price", "TEXT"),
                ("discount_type", "TEXT NOT NULL DEFAULT 'none'"),
                ("discount_value", "TEXT NOT NULL DEFAULT '0.00'"),
                ("discount_amount", "TEXT NOT NULL DEFAULT '0.00'"),
                ("discount_reason", "TEXT"),
            ),
            "erp_receipt_items": (
                ("active", "INTEGER NOT NULL DEFAULT 1"),
            ),
            "erp_receipts": (
                ("comment", "TEXT NOT NULL DEFAULT ''"),
                ("cancelled_by", "TEXT"),
                ("cancellation_reason", "TEXT"),
            ),
            "erp_sales": (
                ("external_order_id", "TEXT"),
                ("idempotency_key", "TEXT"),
                ("cancelled_at", "TEXT"),
                ("cancellation_reason", "TEXT"),
                ("cancellation_comment", "TEXT"),
                ("cancelled_by", "TEXT"),
                ("deleted_at", "TEXT"),
                ("deleted_by", "TEXT"),
                ("archived_at", "TEXT"),
                ("archived_by", "TEXT"),
            ),
            "catalog_stock_movements": (
                ("receipt_id", "TEXT REFERENCES erp_receipts(id) ON DELETE RESTRICT"),
                (
                    "receipt_item_id",
                    "INTEGER REFERENCES erp_receipt_items(id) ON DELETE RESTRICT",
                ),
                ("idempotency_key", "TEXT"),
                ("stock_before", "REAL"),
                ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
                ("source_type", "TEXT"),
                ("source_id", "TEXT"),
                ("source_line_id", "TEXT"),
                ("operation_kind", "TEXT"),
                ("source_number", "TEXT"),
            ),
        }
        for table, columns in migrations.items():
            existing = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info({})".format(table)
                )
            }
            for column, definition in columns:
                if column not in existing:
                    connection.execute(
                        "ALTER TABLE {} ADD COLUMN {} {}".format(
                            table,
                            column,
                            definition,
                        )
                    )

        legacy_sales = connection.execute(
            "SELECT id, metadata_json, returned_at, updated_at, "
            "cancelled_at, deleted_at FROM erp_sales "
            "WHERE cancelled_at IS NULL OR deleted_at IS NULL"
        ).fetchall()
        for sale in legacy_sales:
            try:
                metadata = json.loads(sale["metadata_json"] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            cancelled_at = sale["cancelled_at"]
            if (
                not cancelled_at
                and str(metadata.get("order_status") or "") == "cancelled"
            ):
                cancelled_at = (
                    metadata.get("cancelled_at")
                    or sale["returned_at"]
                    or sale["updated_at"]
                )
            deleted_at = sale["deleted_at"] or metadata.get("deleted_at")
            if (
                cancelled_at != sale["cancelled_at"]
                or deleted_at != sale["deleted_at"]
            ):
                connection.execute(
                    "UPDATE erp_sales SET cancelled_at = ?, deleted_at = ? "
                    "WHERE id = ?",
                    (cancelled_at, deleted_at, sale["id"]),
                )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_brand_id "
            "ON catalog_excel_products(brand_id, active, id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_category_id "
            "ON catalog_excel_products(category_id, active, id)"
        )
        connection.execute(
            "UPDATE catalog_excel_products SET moysklad_product_id = NULL "
            "WHERE trim(COALESCE(moysklad_product_id, '')) = ''"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_excel_products_moysklad "
            "ON catalog_excel_products(moysklad_product_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalog_receipt_rows_taxonomy "
            "ON catalog_excel_receipt_rows(brand_id, category_id, product_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_erp_sale_items_taxonomy "
            "ON erp_sale_items(brand_id, category_id, product_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_erp_receipt_items_active "
            "ON erp_receipt_items(receipt_id, active, id)"
        )
        connection.execute(
            "UPDATE erp_sales SET idempotency_key = NULL "
            "WHERE trim(COALESCE(idempotency_key, '')) = ''"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_erp_sales_idempotency "
            "ON erp_sales(idempotency_key)"
        )
        connection.execute(
            "UPDATE erp_sales SET external_order_id = NULL "
            "WHERE trim(COALESCE(external_order_id, '')) = ''"
        )
        external_index = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_erp_sales_source_external'"
        ).fetchone()
        if external_index is not None and "UNIQUE" in (
            external_index["sql"] or ""
        ).upper():
            connection.execute(
                "DROP INDEX IF EXISTS idx_erp_sales_source_external"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_erp_sales_source_external "
            "ON erp_sales(source, external_order_id)"
        )
        # Production still runs SQLite 3.7, which has no partial indexes.
        # Triggers provide the same active source+external-order uniqueness
        # while still allowing a new sale after a previous one is cancelled.
        connection.execute(
            "DROP INDEX IF EXISTS idx_erp_sales_tictactoy_active_order"
        )
        connection.execute(
            "CREATE TRIGGER IF NOT EXISTS "
            "trg_erp_sales_tictactoy_active_insert "
            "BEFORE INSERT ON erp_sales "
            "WHEN NEW.source = 'tictactoy' "
            "AND NEW.external_order_id IS NOT NULL "
            "AND NEW.cancelled_at IS NULL AND NEW.deleted_at IS NULL "
            "BEGIN SELECT RAISE(ABORT, 'duplicate active tictactoy order') "
            "WHERE EXISTS (SELECT 1 FROM erp_sales existing "
            "WHERE existing.source = NEW.source "
            "AND existing.external_order_id = NEW.external_order_id "
            "AND existing.cancelled_at IS NULL "
            "AND existing.deleted_at IS NULL); END"
        )
        connection.execute(
            "CREATE TRIGGER IF NOT EXISTS "
            "trg_erp_sales_tictactoy_active_update "
            "BEFORE UPDATE OF source, external_order_id, cancelled_at, "
            "deleted_at ON erp_sales "
            "WHEN NEW.source = 'tictactoy' "
            "AND NEW.external_order_id IS NOT NULL "
            "AND NEW.cancelled_at IS NULL AND NEW.deleted_at IS NULL "
            "BEGIN SELECT RAISE(ABORT, 'duplicate active tictactoy order') "
            "WHERE EXISTS (SELECT 1 FROM erp_sales existing "
            "WHERE existing.id <> NEW.id AND existing.source = NEW.source "
            "AND existing.external_order_id = NEW.external_order_id "
            "AND existing.cancelled_at IS NULL "
            "AND existing.deleted_at IS NULL); END"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalog_stock_movements_receipt "
            "ON catalog_stock_movements(receipt_id, created_at)"
        )
        connection.execute(
            "UPDATE catalog_stock_movements SET idempotency_key = NULL "
            "WHERE trim(COALESCE(idempotency_key, '')) = ''"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_catalog_stock_movements_idempotency "
            "ON catalog_stock_movements(idempotency_key)"
        )
        connection.execute(
            "UPDATE catalog_stock_movements "
            "SET stock_before = stock_after - quantity_delta "
            "WHERE stock_before IS NULL"
        )
        connection.execute(
            "UPDATE catalog_stock_movements SET "
            "tenant_id = COALESCE(("
            "SELECT r.tenant_id FROM erp_receipts r "
            "WHERE r.id = catalog_stock_movements.receipt_id"
            "), 'default'), "
            "source_type = 'receipt', "
            "source_id = receipt_id, "
            "source_line_id = CASE "
            "WHEN receipt_item_id IS NOT NULL THEN CAST(receipt_item_id AS TEXT) "
            "ELSE 'product:' || CAST(product_id AS TEXT) END, "
            "operation_kind = CASE movement_type "
            "WHEN 'receipt' THEN 'post' "
            "WHEN 'cancellation' THEN 'cancel' "
            "WHEN 'manual_adjustment' THEN 'adjust' END, "
            "source_number = COALESCE(("
            "SELECT r.number FROM erp_receipts r "
            "WHERE r.id = catalog_stock_movements.receipt_id"
            "), source_number) "
            "WHERE receipt_id IS NOT NULL "
            "AND movement_type IN ('receipt', 'cancellation', 'manual_adjustment') "
            "AND source_type IS NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_catalog_stock_movements_operation "
            "ON catalog_stock_movements("
            "tenant_id, source_type, source_id, source_line_id, operation_kind"
            ")"
        )

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        def cleaned(value):
            return " ".join(str(value or "").split())

        def normalized(value):
            return cleaned(value).casefold()

        def ensure_brand(name):
            display_name = cleaned(name)
            key = normalized(display_name)
            if not key:
                return None
            row = connection.execute(
                "SELECT id, name FROM erp_brands WHERE normalized_name = ?",
                (key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO erp_brands "
                    "(name, normalized_name, active, created_at, updated_at) "
                    "VALUES (?, ?, 1, ?, ?)",
                    (display_name, key, now, now),
                )
                row = connection.execute(
                    "SELECT id, name FROM erp_brands WHERE normalized_name = ?",
                    (key,),
                ).fetchone()
            elif row["name"] != display_name:
                connection.execute(
                    "INSERT OR IGNORE INTO erp_catalog_normalization_audit "
                    "(entity_type, normalized_name, canonical_id, canonical_name, "
                    "variant_name, occurrence_count, resolution, created_at) "
                    "VALUES ('brand', ?, ?, ?, ?, 1, 'linked', ?)",
                    (key, str(row["id"]), row["name"], display_name, now),
                )
            return row

        def ensure_category(brand_id, name):
            display_name = cleaned(name)
            key = normalized(display_name)
            if not brand_id or not key:
                return None
            row = connection.execute(
                "SELECT id, name FROM erp_categories "
                "WHERE normalized_name = ? ORDER BY id LIMIT 1",
                (key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO erp_categories "
                    "(brand_id, name, normalized_name, active, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (int(brand_id), display_name, key, now, now),
                )
                row = connection.execute(
                    "SELECT id, name FROM erp_categories "
                    "WHERE normalized_name = ? ORDER BY id LIMIT 1",
                    (key,),
                ).fetchone()
            elif row["name"] != display_name:
                connection.execute(
                    "INSERT OR IGNORE INTO erp_catalog_normalization_audit "
                    "(entity_type, normalized_name, canonical_id, canonical_name, "
                    "variant_name, occurrence_count, resolution, details_json, created_at) "
                    "VALUES ('category', ?, ?, ?, ?, 1, 'linked', ?, ?)",
                    (
                        key,
                        str(row["id"]),
                        row["name"],
                        display_name,
                        '{"brand_id": %d}' % int(brand_id),
                        now,
                    ),
                )
            return row

        products = connection.execute(
            "SELECT id, excel_brand, excel_category "
            "FROM catalog_excel_products "
            "WHERE (trim(COALESCE(excel_brand, '')) <> '' AND brand_id IS NULL) "
            "OR (trim(COALESCE(excel_category, '')) <> '' AND category_id IS NULL) "
            "ORDER BY id"
        ).fetchall()
        for product in products:
            brand = ensure_brand(product["excel_brand"])
            category = ensure_category(
                brand["id"] if brand else None,
                product["excel_category"],
            )
            connection.execute(
                "UPDATE catalog_excel_products SET "
                "brand_id = ?, category_id = ?, excel_brand = ?, excel_category = ? "
                "WHERE id = ?",
                (
                    brand["id"] if brand else None,
                    category["id"] if category else None,
                    brand["name"] if brand else cleaned(product["excel_brand"]),
                    category["name"] if category else (
                        cleaned(product["excel_category"]) or None
                    ),
                    product["id"],
                ),
            )

        connection.execute(
            "UPDATE erp_sale_items SET "
            "brand_id = (SELECT p.brand_id FROM catalog_excel_products p "
            "WHERE p.id = erp_sale_items.product_id), "
            "category_id = (SELECT p.category_id FROM catalog_excel_products p "
            "WHERE p.id = erp_sale_items.product_id) "
            "WHERE brand_id IS NULL OR category_id IS NULL"
        )
        connection.execute(
            "UPDATE catalog_excel_receipt_rows SET "
            "brand_id = (SELECT p.brand_id FROM catalog_excel_products p "
            "WHERE p.id = catalog_excel_receipt_rows.product_id), "
            "category_id = (SELECT p.category_id FROM catalog_excel_products p "
            "WHERE p.id = catalog_excel_receipt_rows.product_id) "
            "WHERE brand_id IS NULL OR category_id IS NULL"
        )

    @staticmethod
    def _ensure_stock_movement_constraints(connection):
        """Allow explicit cancellation movements without losing history."""
        table = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'catalog_stock_movements'"
        ).fetchone()
        if table is None:
            return
        table_sql = table["sql"] or ""
        if "'cancellation'" in table_sql and "'inventory_adjustment'" in table_sql:
            return

        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE catalog_stock_movements_migrating ("
                "id TEXT PRIMARY KEY, "
                "product_id INTEGER NOT NULL REFERENCES "
                "catalog_excel_products(id) ON DELETE RESTRICT, "
                "movement_type TEXT NOT NULL CHECK (movement_type IN ("
                "'initial_stock', 'receipt', 'sale', 'return', "
                "'cancellation', 'manual_adjustment', "
                "'inventory_adjustment')), "
                "quantity_delta REAL NOT NULL CHECK (quantity_delta != 0), "
                "stock_before REAL, "
                "stock_after REAL NOT NULL CHECK (stock_after >= 0), "
                "sale_id TEXT REFERENCES erp_sales(id) ON DELETE RESTRICT, "
                "sale_item_id INTEGER REFERENCES erp_sale_items(id) "
                "ON DELETE RESTRICT, "
                "receipt_id TEXT REFERENCES erp_receipts(id) ON DELETE RESTRICT, "
                "receipt_item_id INTEGER REFERENCES erp_receipt_items(id) "
                "ON DELETE RESTRICT, "
                "idempotency_key TEXT, "
                "tenant_id TEXT NOT NULL DEFAULT 'default', "
                "source_type TEXT, source_id TEXT, source_line_id TEXT, "
                "operation_kind TEXT, source_number TEXT, "
                "source TEXT, user_name TEXT, "
                "comment TEXT, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO catalog_stock_movements_migrating "
                "(id, product_id, movement_type, quantity_delta, stock_before, "
                "stock_after, "
                "sale_id, sale_item_id, receipt_id, receipt_item_id, "
                "idempotency_key, tenant_id, source_type, source_id, "
                "source_line_id, operation_kind, source_number, source, "
                "user_name, comment, created_at) "
                "SELECT id, product_id, movement_type, quantity_delta, "
                "stock_before, stock_after, sale_id, sale_item_id, receipt_id, "
                "receipt_item_id, idempotency_key, tenant_id, source_type, "
                "source_id, source_line_id, operation_kind, source_number, "
                "source, user_name, comment, created_at "
                "FROM catalog_stock_movements"
            )
            connection.execute("DROP TABLE catalog_stock_movements")
            connection.execute(
                "ALTER TABLE catalog_stock_movements_migrating "
                "RENAME TO catalog_stock_movements"
            )
            connection.execute(
                "CREATE INDEX idx_catalog_stock_movements_product "
                "ON catalog_stock_movements(product_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX idx_catalog_stock_movements_sale "
                "ON catalog_stock_movements(sale_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX idx_catalog_stock_movements_receipt "
                "ON catalog_stock_movements(receipt_id, created_at)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX idx_catalog_stock_movements_idempotency "
                "ON catalog_stock_movements(idempotency_key)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX idx_catalog_stock_movements_operation "
                "ON catalog_stock_movements("
                "tenant_id, source_type, source_id, source_line_id, operation_kind"
                ")"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                "Stock movement schema migration created foreign key violations"
            )

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def table_names(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name LIKE 'catalog_%' ORDER BY name"
            ).fetchall()
        return [row["name"] for row in rows]

    def exists(self):
        return str(self.path) == ":memory:" or self.path.exists()
