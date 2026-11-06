"""SQLite storage: memories, pending memories, chat history and an append-only audit log."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                 -- episodic | semantic | procedural | working
    content TEXT NOT NULL,
    summary TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',             -- JSON list
    people TEXT DEFAULT '[]',           -- JSON list
    mood TEXT DEFAULT '',
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    source TEXT NOT NULL DEFAULT 'manual',  -- journal | interview | chat | import | manual
    locked INTEGER NOT NULL DEFAULT 0,  -- locked memories cannot be edited by the Brain
    happened_on TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS pending_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    reason TEXT DEFAULT '',
    source TEXT NOT NULL DEFAULT 'chat',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation TEXT NOT NULL DEFAULT 'main',
    role TEXT NOT NULL,                 -- user | assistant
    content TEXT NOT NULL,
    mode TEXT DEFAULT 'mirror',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interview_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    memory_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,                -- owner | brain | system
    action TEXT NOT NULL,
    detail TEXT DEFAULT '{}'
);

-- The audit log is append-only: no updates, no deletes.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation, id);

-- Earlier parts of long conversations, summarised (compaction)
CREATE TABLE IF NOT EXISTS summaries (
    conversation TEXT PRIMARY KEY,
    upto INTEGER NOT NULL,            -- messages up to this id are summarised
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Dreaming: a nightly look back over the day
CREATE TABLE IF NOT EXISTS dreams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL UNIQUE,
    diary TEXT NOT NULL,
    suggested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Automations: scheduled jobs, heartbeat and webhooks, and their runs
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'schedule',   -- schedule | heartbeat | webhook
    schedule TEXT NOT NULL DEFAULT '',
    tz TEXT NOT NULL DEFAULT '',
    deliver TEXT NOT NULL DEFAULT 'web',
    token TEXT,                               -- webhook secret (hash)
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run TEXT,
    last_run TEXT,
    last_result TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL,
    output TEXT NOT NULL DEFAULT ''
);

-- Usage: model requests per day (for the usage card and /metrics)
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, provider, model)
);

-- Channels: who may talk to the Brain on Telegram, Discord, Slack and email
CREATE TABLE IF NOT EXISTS channel_senders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    sender TEXT NOT NULL,                     -- the app's user id or email address
    name TEXT NOT NULL DEFAULT '',
    chat TEXT NOT NULL DEFAULT '',            -- where to send the owner messages (DM chat id)
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | allowed | blocked
    code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(channel, sender)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    if config.MULTIUSER and config.DATA_DIR != config.ROOT_DATA:
        if not config.DATA_DIR.exists():      # a deleted account: never bring its folder back
            raise sqlite3.OperationalError("This account no longer exists.")
    else:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
        if "embed_model" not in cols:  # added when multi-provider embeddings arrived
            conn.execute("ALTER TABLE memories ADD COLUMN embed_model TEXT DEFAULT ''")


def audit(actor: str, action: str, **detail) -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)",
            (now(), actor, action, json.dumps(detail, ensure_ascii=False)),
        )


def recent_audit(limit: int = 100) -> list[dict]:
    with session() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) | {"detail": json.loads(r["detail"] or "{}")} for r in rows]
