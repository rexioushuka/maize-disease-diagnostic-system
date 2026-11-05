"""SQLite connection helpers (context-managed, WAL enabled per connection)."""

import sqlite3

from flask import current_app, g


def _connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def get_db():
    """Return the request-scoped connection (QUA-01/QUA-11)."""
    if "db" not in g:
        g.db = _connect(current_app.config["DB_PATH"])
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def register_db(app):
    app.teardown_appcontext(close_db)


def connect(path):
    """Standalone connection for scripts/tests outside a request context."""
    return _connect(path)
