"""Add write-off documents; preserve all existing balances and movements."""
WRITEOFF_SQL = (
    "CREATE TABLE erp_writeoffs (id TEXT PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE RESTRICT, quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0), reason TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', status TEXT NOT NULL CHECK(status IN ('posted','cancelled')), created_at TEXT NOT NULL, created_by TEXT NOT NULL, created_by_name TEXT NOT NULL, cancelled_at TEXT, cancelled_by TEXT, cancelled_by_name TEXT, idempotency_key TEXT UNIQUE, product_name TEXT NOT NULL, article TEXT, brand TEXT, category TEXT)",
    "CREATE INDEX idx_erp_writeoffs_created ON erp_writeoffs(created_at,id)",
    "CREATE INDEX idx_erp_writeoffs_product ON erp_writeoffs(product_id,created_at)",
    "CREATE TABLE erp_writeoff_items (writeoff_id TEXT NOT NULL REFERENCES erp_writeoffs(id) ON DELETE RESTRICT, product_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE RESTRICT, quantity INTEGER NOT NULL CHECK(quantity>0), PRIMARY KEY(writeoff_id,product_id))",
)


def apply_writeoff_migration(connection, ddl_observer=None):
    for statement in WRITEOFF_SQL:
        if ddl_observer:
            ddl_observer(statement)
        connection.execute(statement)
