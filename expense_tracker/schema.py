"""SQLite schema and connection setup."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    inserted_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    error_line INTEGER
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    source_hash TEXT NOT NULL UNIQUE,
    date TEXT NOT NULL,
    merchant TEXT NOT NULL,
    amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
    currency TEXT NOT NULL DEFAULT 'INR' CHECK(currency = 'INR'),
    source TEXT NOT NULL,
    source_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('needs_review','confirmed')),
    category TEXT NOT NULL,
    category_source TEXT NOT NULL,
    parser_confidence REAL NOT NULL CHECK(parser_confidence BETWEEN 0 AND 1),
    category_confidence REAL NOT NULL CHECK(category_confidence BETWEEN 0 AND 1),
    review_reason TEXT,
    ingestion_run_id INTEGER REFERENCES ingestion_runs(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS transactions_date ON transactions(date);
CREATE TABLE IF NOT EXISTS merchant_rules (
    id INTEGER PRIMARY KEY,
    merchant TEXT NOT NULL UNIQUE COLLATE NOCASE,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS review_actions (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    action TEXT NOT NULL,
    old_category TEXT,
    new_category TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_db(db_path):
    """Create parents/tables and return a connection; caller must close it."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
            if 'raw_category' not in columns:
                conn.execute("ALTER TABLE transactions ADD COLUMN raw_category TEXT")
            if 'parser_reason' not in columns:
                conn.execute("ALTER TABLE transactions ADD COLUMN parser_reason TEXT")
            if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
                conn.execute("PRAGMA user_version = 2")
        return conn
    except Exception:
        conn.close()
        raise
