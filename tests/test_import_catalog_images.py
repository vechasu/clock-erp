import sqlite3

from scripts import import_catalog_images


def test_backup_runtime_supports_sqlite_without_connection_backup(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    connection = sqlite3.connect(str(database_path))
    connection.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO products (name) VALUES ('Clock')")
    connection.commit()
    connection.close()

    image_root = tmp_path / "product_images"
    image_root.mkdir()
    (image_root / "sample.jpg").write_bytes(b"image")

    real_connect = import_catalog_images.sqlite3.connect

    class LegacyConnection:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, name):
            if name == "backup":
                raise AttributeError(name)
            return getattr(self.wrapped, name)

    monkeypatch.setattr(
        import_catalog_images.sqlite3,
        "connect",
        lambda path: LegacyConnection(real_connect(path)),
    )

    backup = import_catalog_images.backup_runtime(
        database_path, [image_root], tmp_path / "backups"
    )

    copied = real_connect(str(backup / "catalog.db"))
    try:
        assert copied.execute("SELECT name FROM products").fetchone() == ("Clock",)
    finally:
        copied.close()
    assert (backup / "product_images" / "sample.jpg").read_bytes() == b"image"
