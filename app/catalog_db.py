import os
import sqlite3
from contextlib import contextmanager
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

CREATE TABLE IF NOT EXISTS catalog_excel_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    created_batch_id TEXT NOT NULL REFERENCES catalog_excel_batches(id),
    current_batch_id TEXT NOT NULL REFERENCES catalog_excel_batches(id),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    raw_excel_json TEXT NOT NULL,
    excel_row INTEGER NOT NULL,
    excel_name_raw TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    excel_article TEXT,
    article_quality TEXT NOT NULL,
    excel_brand TEXT NOT NULL,
    excel_category TEXT,
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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_active
    ON catalog_excel_products(active, current_batch_id);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_match_status
    ON catalog_excel_products(match_status);
CREATE INDEX IF NOT EXISTS idx_catalog_excel_products_bitrix
    ON catalog_excel_products(bitrix_catalog_product_id);

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
"""


class CatalogDatabase:
    def __init__(self, path=None):
        configured_path = path or os.getenv("CATALOG_DATABASE_PATH")
        self.path = Path(configured_path) if configured_path else DEFAULT_CATALOG_DATABASE_PATH

    def connect(self):
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_excel_receipt_constraints(connection)
            self._ensure_excel_cardinality_columns(connection)

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
