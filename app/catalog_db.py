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
