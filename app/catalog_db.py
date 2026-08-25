import os
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_DATABASE_PATH = PROJECT_ROOT / "instance" / "catalog.db"




class CatalogDatabase:
    _schema_cache = {}
    _schema_cache_lock = threading.Lock()

    def __init__(
        self,
        path=None,
        cache_initialization=True,
        ddl_observer=None,
    ):
        configured_path = path or os.getenv("CATALOG_DATABASE_PATH")
        self.path = Path(configured_path) if configured_path else DEFAULT_CATALOG_DATABASE_PATH
        self.cache_initialization = bool(cache_initialization)
        self.ddl_observer = ddl_observer
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
        try:
            connection = sqlite3.connect(
                "file:{}?mode=ro".format(resolved), uri=True
            )
            try:
                schema_version = int(
                    connection.execute("PRAGMA schema_version").fetchone()[0]
                )
                ledger_state = tuple(connection.execute(
                    "SELECT migration_id, checksum, state "
                    "FROM erp_migration_ledger ORDER BY migration_id"
                ).fetchall())
            finally:
                connection.close()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None
        return (
            str(resolved), stat.st_dev, stat.st_ino,
            schema_version, ledger_state,
        )

    def connect(self):
        self.initialize()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if self.ddl_observer is not None:
            def trace(statement):
                normalized = str(statement or "").lstrip().upper()
                if normalized.startswith(("CREATE ", "ALTER ", "DROP ")):
                    self.ddl_observer(" ".join(str(statement).split()))

            connection.set_trace_callback(trace)
        return connection

    def initialize(self, allow_schema_changes=False):
        if allow_schema_changes:
            raise RuntimeError(
                "catalog schema changes are deploy-time only; run catalog migrations"
            )
        from app.schema_migrations import validate_catalog_runtime
        with self._initialize_lock:
            cache_path = str(self.path.resolve())
            with self._schema_cache_lock:
                identity = self._schema_cache_identity()
                if (
                    identity is None
                    or self._schema_cache.get(cache_path) != identity
                ):
                    validate_catalog_runtime(self.path)
                    identity = self._schema_cache_identity()
                    if identity is not None:
                        self._schema_cache[cache_path] = identity
                self._initialized = True
        return None


















    @contextmanager
    def transaction(self):
        connection = None
        try:
            for attempt in range(3):
                connection = self.connect()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as error:
                    connection.close()
                    connection = None
                    if "disk i/o error" not in str(error).lower() or attempt == 2:
                        raise
            yield connection
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
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
