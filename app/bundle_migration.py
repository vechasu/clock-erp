"""Additive deploy-time composition schema; never converts existing stock."""
BUNDLE_SQL = (
    "CREATE TABLE IF NOT EXISTS erp_local_components ("
    "product_id INTEGER PRIMARY KEY REFERENCES catalog_excel_products(id) ON DELETE RESTRICT)",
    "CREATE TRIGGER IF NOT EXISTS trg_bundle_physical_stock BEFORE UPDATE OF stock ON catalog_excel_products "
    "WHEN NEW.stock<>0 AND EXISTS(SELECT 1 FROM erp_product_bundles WHERE product_id=NEW.id) "
    "BEGIN SELECT RAISE(ABORT,'Bundle SKU has no physical stock; receive or count components'); END",

    "CREATE TRIGGER IF NOT EXISTS trg_local_component_bitrix BEFORE UPDATE OF bitrix_catalog_product_id,"
    "bitrix_external_product_id ON catalog_excel_products "
    "WHEN (NEW.bitrix_catalog_product_id IS NOT NULL OR NEW.bitrix_external_product_id IS NOT NULL) "
    "AND EXISTS(SELECT 1 FROM erp_local_components WHERE product_id=NEW.id) "
    "BEGIN SELECT RAISE(ABORT,'Local components cannot be linked to Bitrix'); END",
    "CREATE TABLE IF NOT EXISTS erp_product_bundles ("
    "product_id INTEGER PRIMARY KEY REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,"
    "updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS erp_bundle_components ("
    "product_id INTEGER NOT NULL REFERENCES erp_product_bundles(product_id) ON DELETE RESTRICT,"
    "component_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,"
    "quantity INTEGER NOT NULL CHECK(quantity>0 AND quantity=CAST(quantity AS INTEGER)),"
    "PRIMARY KEY(product_id,component_id),CHECK(product_id<>component_id))",
    "CREATE INDEX IF NOT EXISTS idx_erp_bundle_component ON erp_bundle_components(component_id)",
    "CREATE TABLE IF NOT EXISTS erp_sale_component_snapshots ("
    "sale_item_id INTEGER NOT NULL REFERENCES erp_sale_items(id) ON DELETE RESTRICT,"
    "component_id INTEGER NOT NULL REFERENCES catalog_excel_products(id) ON DELETE RESTRICT,"
    "quantity_per_unit INTEGER NOT NULL CHECK(quantity_per_unit>0),"
    "name TEXT NOT NULL,article TEXT,PRIMARY KEY(sale_item_id,component_id))",
)


def apply_bundle_migration(connection, ddl_observer=None):
    for statement in BUNDLE_SQL:
        if ddl_observer:
            ddl_observer(statement)
        connection.execute(statement)
