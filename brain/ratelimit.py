"""Brute-force and rate-limit counters kept on disk, so a restart (or a second server process)
doesn't reset them. One small SQLite file next to the other server data: data/security.db.

Keys are short strings such as "pin:<user>", "login:email:a@b.c", "signup:<ip>" or "hook:<prefix>".
"""
import sqlite3
import threading
import time
from contextlib import closing

from . import config

_lock = threading.Lock()
_ready: set[str] = set()
KEEP_SECONDS = 24 * 3600          # events older than this are pruned


def _path():
    return config.ROOT_DATA / "security.db"


def _conn() -> sqlite3.Connection:
    p = _path()
    c = sqlite3.connect(p, timeout=10, isolation_level=None)
    if str(p) not in _ready:
        p.parent.mkdir(parents=True, exist_ok=True)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS hits (key TEXT NOT NULL, at REAL NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS hits_key_at ON hits(key, at)")
        c.execute("CREATE TABLE IF NOT EXISTS locks (key TEXT PRIMARY KEY, until REAL NOT NULL)")
        _ready.add(str(p))
    return c


def count(key: str, window: float) -> int:
    """How many events for key happened in the last `window` seconds."""
    with _lock, closing(_conn()) as c:
        return c.execute("SELECT COUNT(*) FROM hits WHERE key=? AND at>?", (key, time.time() - window)).fetchone()[0]


def hit(*keys: str) -> None:
    now = time.time()
    with _lock, closing(_conn()) as c:
        c.executemany("INSERT INTO hits(key, at) VALUES(?, ?)", [(k, now) for k in keys])
        c.execute("DELETE FROM hits WHERE at<?", (now - KEEP_SECONDS,))


def allow(key: str, limit: int, window: float) -> bool:
    """Count one event and say whether it is within the limit (atomic check-and-add)."""
    now = time.time()
    with _lock, closing(_conn()) as c:
        c.execute("BEGIN IMMEDIATE")
        n = c.execute("SELECT COUNT(*) FROM hits WHERE key=? AND at>?", (key, now - window)).fetchone()[0]
        if n >= limit:
            c.execute("COMMIT")
            return False
        c.execute("INSERT INTO hits(key, at) VALUES(?, ?)", (key, now))
        c.execute("COMMIT")
        return True


def clear(key: str) -> None:
    with _lock, closing(_conn()) as c:
        c.execute("DELETE FROM hits WHERE key=?", (key,))
        c.execute("DELETE FROM locks WHERE key=?", (key,))


def locked_for(key: str) -> float:
    """Seconds left on a lockout for key (0 when not locked)."""
    with _lock, closing(_conn()) as c:
        row = c.execute("SELECT until FROM locks WHERE key=?", (key,)).fetchone()
    return max(0.0, row[0] - time.time()) if row else 0.0


def lock(key: str, seconds: float) -> None:
    with _lock, closing(_conn()) as c:
        c.execute("INSERT INTO locks(key, until) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET until=excluded.until",
                  (key, time.time() + seconds))
        c.execute("DELETE FROM hits WHERE key=?", (key,))
