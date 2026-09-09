"""Remove retired collections without modifying products or other business data."""
REMOVE_COLLECTIONS_SQL = (
    "DROP TABLE IF EXISTS product_collections",
    "DROP TABLE IF EXISTS erp_collections",
)


def apply_remove_collections_migration(connection, ddl_observer=None):
    for statement in REMOVE_COLLECTIONS_SQL:
        if ddl_observer:
            ddl_observer(statement)
        connection.execute(statement)
