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

-- Cyber events are CLAIMS until verification checks support or contradict them.
CREATE TABLE IF NOT EXISTS cyber_events (
    id INTEGER PRIMARY KEY,
    ext_id TEXT UNIQUE,                 -- collector id, so re-polling never duplicates an event
    kind TEXT,                          -- ddos_claim | ransomware_claim | defacement | data_breach_claim | outage | vulnerability
    title TEXT, target TEXT, sector TEXT, claimed_actor TEXT,
    source_name TEXT, source_type TEXT, source_url TEXT,
    claimed_at REAL, first_seen REAL,
    status TEXT DEFAULT 'unverified',   -- unverified | partly_confirmed | confirmed | refuted
    source_reliability TEXT,            -- Admiralty A-F (from the source type)
    info_credibility INTEGER,           -- Admiralty 1-6 (from the checks)
    confidence REAL,                    -- 0-1, derived from both
    severity TEXT,                      -- low | medium | high | critical (+1 when linked to a narrative)
    checked_at REAL,                   -- data time of the last verification run
    verdict_at REAL,                    -- data time the status first became confirmed / refuted
    raw TEXT
);
CREATE INDEX IF NOT EXISTS ix_cyber_time ON cyber_events(claimed_at);

CREATE TABLE IF NOT EXISTS verification_checks (
    id INTEGER PRIMARY KEY,
    event_id INTEGER, check_name TEXT,
    result TEXT,                        -- supports | contradicts | inconclusive | error
    detail TEXT, evidence_url TEXT, checked_at REAL,
    origin TEXT                         -- live | recorded | own_data
);
CREATE INDEX IF NOT EXISTS ix_checks_event ON verification_checks(event_id, checked_at);

-- Events: what happened, where, how sure (hybridwatch/events.py). Recomputed from narratives.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE,                    -- category|place|anchor narrative|part: stable across recomputations
    category TEXT, title TEXT, place TEXT, lat REAL, lon REAL,
    narrative_ids TEXT, alert_ids TEXT, part TEXT, post_ids TEXT,
    first_ts REAL, last_ts REAL, first_credible_ts REAL, first_official_ts REAL,
    n_posts INTEGER, n_sources INTEGER, n_credible INTEGER, n_official INTEGER, langs TEXT,
    severity TEXT, reliability TEXT, credibility INTEGER, confidence REAL,
    status TEXT, indicators TEXT, updated_at REAL
);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(last_ts);

CREATE TABLE IF NOT EXISTS hybrid_links (
    id INTEGER PRIMARY KEY,
    cyber_event_id INTEGER, alert_id INTEGER, reason TEXT, score REAL,
    UNIQUE(cyber_event_id, alert_id)
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,
    wall_ts REAL, sim_ts REAL, n_posts INTEGER, seconds REAL, n_alerts INTEGER
);
"""

_local = threading.local()

# Columns added after the first release. Existing databases get them on connect.
MIGRATIONS = [
    ("alerts", "seen_snapshot", "TEXT"),       # what the analyst saw last time -> "what changed since"
    ("decisions", "prev", "TEXT"),             # alert state before the decision -> undo
    ("decisions", "undone", "INTEGER DEFAULT 0"),
    ("narratives", "tags", "TEXT"),            # top concept tags, shown as chips under the title
    ("narratives", "en_post_id", "INTEGER"),   # most central English post -> readable title
    ("cyber_events", "severity", "TEXT"),
    # threat model (threat.py): score 0-100 and its explanation (factors, why, why not higher, boundaries)
    ("alerts", "score", "REAL"), ("alerts", "threat", "TEXT"),
    ("events", "score", "REAL"), ("events", "threat", "TEXT"),
    ("cyber_events", "score", "REAL"), ("cyber_events", "threat", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:  # the other process added it first
                pass
    conn.commit()


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
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
