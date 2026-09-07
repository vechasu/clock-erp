"""SQLite API compatibility with production Python 3.6 / SQLite 3.7.17."""
import sqlite3


def register_deterministic_function(connection, name, num_params, function):
    """Retain the optimization when supported; register the same function otherwise."""
    try:
        connection.create_function(name, num_params, function, deterministic=True)
    except (TypeError, sqlite3.NotSupportedError):
        # Python < 3.8 rejects the keyword; SQLite < 3.8.3 rejects the flag.
        connection.create_function(name, num_params, function)
