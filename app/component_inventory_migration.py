"""Add local physical balances without converting any legacy quantities."""
COMPONENT_SQL = (
    "CREATE TABLE erp_component_inventory (product_id INTEGER PRIMARY KEY REFERENCES catalog_excel_products(id) ON DELETE RESTRICT, physical_stock REAL CHECK(physical_stock>=0), initialized_at TEXT, updated_at TEXT NOT NULL)",
    "CREATE TABLE erp_physical_documents (document_type TEXT NOT NULL, document_id TEXT NOT NULL, product_id INTEGER NOT NULL REFERENCES erp_component_inventory(product_id) ON DELETE RESTRICT, PRIMARY KEY(document_type,document_id,product_id))",
    "CREATE TABLE erp_component_inventory_events (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES erp_component_inventory(product_id) ON DELETE RESTRICT, stock_before REAL, stock_after REAL NOT NULL CHECK(stock_after>=0), actor TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE erp_bundle_transitions (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE RESTRICT, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), legacy_stock REAL NOT NULL, created_at TEXT NOT NULL)",
    "INSERT INTO erp_component_inventory(product_id,updated_at) SELECT DISTINCT component_id,datetime('now') FROM erp_bundle_components",
    "DROP TRIGGER trg_bundle_physical_stock",
)


def apply_component_inventory_migration(connection, ddl_observer=None):
    for statement in COMPONENT_SQL:
        if ddl_observer:
            ddl_observer(statement)
        connection.execute(statement)
