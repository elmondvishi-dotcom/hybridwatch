"""SQLite storage. The worker writes, the API reads (and writes analyst decisions).

WAL mode lets the always-on worker and the web API use the database at the same
time, even as separate processes. Swap for PostgreSQL in production.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    ext_id TEXT UNIQUE,
    source TEXT, source_type TEXT, credibility TEXT,
    author TEXT, author_created REAL, followers INTEGER,
    ts REAL, text TEXT, translation TEXT, lang TEXT, url TEXT,
    narrative_id INTEGER, dup_group INTEGER,
    debunk_id INTEGER, debunk_score REAL,
    collected_at REAL,
    gt_narrative TEXT, gt_coordinated INTEGER, gt_debunk TEXT
);
CREATE INDEX IF NOT EXISTS ix_posts_narr ON posts(narrative_id, ts);
CREATE INDEX IF NOT EXISTS ix_posts_ts ON posts(ts);
CREATE INDEX IF NOT EXISTS ix_posts_dup ON posts(dup_group);

CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY,
    label TEXT, label_source TEXT, labeled_at_size INTEGER DEFAULT 0,
    centroid BLOB, size INTEGER DEFAULT 0,
    first_ts REAL, last_ts REAL, rep_post_id INTEGER
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    narrative_id INTEGER UNIQUE,
    status TEXT DEFAULT 'new',
    type TEXT, severity TEXT, confidence REAL,
    signals TEXT, reasons TEXT, stats TEXT,
    created_at REAL, created_sim REAL,
    bumped_at REAL, bumped_sim REAL, posts_at_bump INTEGER,
    unread INTEGER DEFAULT 1,
    previously_dismissed INTEGER DEFAULT 0,
    dismiss_reason TEXT, dismissed_signals TEXT, dismissed_posts INTEGER,
    suggestion TEXT, assessment TEXT, history TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts(status, bumped_at);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER, action TEXT, reason TEXT,
    fields TEXT, changed_fields TEXT, note TEXT, ts REAL
);

CREATE TABLE IF NOT EXISTS factchecks (
    id INTEGER PRIMARY KEY,
    ext_id TEXT UNIQUE,
    source TEXT, claim TEXT, verdict TEXT, url TEXT, date TEXT,
    kind TEXT DEFAULT 'external', embedding BLOB
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,
    wall_ts REAL, sim_ts REAL, n_posts INTEGER, seconds REAL, n_alerts INTEGER
);
"""

_local = threading.local()


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))
    conn.commit()


def jloads(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default
