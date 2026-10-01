"""Connexion SQLite et schéma."""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id TEXT PRIMARY KEY,
    sender_name TEXT NOT NULL DEFAULT '',
    sender_address TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    category TEXT,
    urgency TEXT,
    confidence REAL,
    explanation TEXT,
    summary TEXT,
    extraction TEXT,
    invoice_checks TEXT NOT NULL DEFAULT '[]',
    draft TEXT,
    draft_original TEXT,
    error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    processed_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at TEXT,
    reply_to TEXT NOT NULL DEFAULT '',
    links TEXT NOT NULL DEFAULT '[]',
    fraud_risk TEXT,
    fraud_reason TEXT,
    fraud_signals TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS trusted_senders (
    value TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

NEW_COLUMNS = {
    "reply_to": "TEXT NOT NULL DEFAULT ''",
    "links": "TEXT NOT NULL DEFAULT '[]'",
    "fraud_risk": "TEXT",
    "fraud_reason": "TEXT",
    "fraud_signals": "TEXT NOT NULL DEFAULT '[]'",
}


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(emails)")}
    for name, declaration in NEW_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE emails ADD COLUMN {name} {declaration}")
    conn.commit()
    return conn
