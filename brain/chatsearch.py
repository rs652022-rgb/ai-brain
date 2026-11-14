"""Search every past conversation, not only the current one (like Hermes' session search).

Uses SQLite's built-in full-text index (FTS5), kept in step with the messages table by triggers, so
nothing extra runs while you chat. Falls back to a plain LIKE search on SQLite builds without FTS5.
Background runners (goals, jobs, workflows, the mail watcher) are left out by default.
"""
import re
import sqlite3

from . import db

BACKGROUND = ("goal:", "job:", "workflow:", "mailwatch")

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def ensure(conn: sqlite3.Connection) -> bool:
    """Create the index (and fill it from old messages) once. False when this SQLite has no FTS5."""
    try:
        have = conn.execute("SELECT 1 FROM sqlite_master WHERE name='messages_fts'").fetchone()
        conn.executescript(FTS_SCHEMA)
        if not have:
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
        return True
    except sqlite3.OperationalError:
        return False


def _fts_query(text: str) -> str:
    """Words from the owner's text as an FTS5 query: every word must appear (prefix match)."""
    words = re.findall(r"[\w']+", text.lower(), re.UNICODE)[:12]
    return " ".join(f'"{w.replace(chr(34), "")}"*' for w in words if len(w) > 1)


def _row(r) -> dict:
    return {"id": r["id"], "conversation": r["conversation"], "role": r["role"],
            "snippet": r["snippet"], "created_at": r["created_at"]}


def search(query: str, limit: int = 20, include_background: bool = False, conversation: str = "") -> list[dict]:
    """Matching messages, best first: [{id, conversation, role, snippet, created_at}]."""
    q = _fts_query(str(query or ""))
    if not q:
        return []
    limit = max(1, min(int(limit), 100))
    where, params = [], []
    if conversation:
        where.append("m.conversation = ?")
        params.append(conversation)
    elif not include_background:
        where.extend("m.conversation NOT LIKE ?" for _ in BACKGROUND)
        params.extend(f"{p}%" for p in BACKGROUND)
    extra = (" AND " + " AND ".join(where)) if where else ""
    with db.session() as conn:
        if ensure(conn):
            rows = conn.execute(
                "SELECT m.id, m.conversation, m.role, m.created_at, "
                "snippet(messages_fts, 0, '[', ']', ' … ', 14) AS snippet "
                f"FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
                f"WHERE messages_fts MATCH ?{extra} ORDER BY bm25(messages_fts) LIMIT ?",
                [q, *params, limit]).fetchall()
            return [_row(r) for r in rows]
        like = "%" + str(query).strip()[:100] + "%"
        rows = conn.execute(
            "SELECT m.id, m.conversation, m.role, m.created_at, substr(m.content, 1, 240) AS snippet "
            f"FROM messages m WHERE m.content LIKE ?{extra} ORDER BY m.id DESC LIMIT ?",
            [like, *params, limit]).fetchall()
        return [_row(r) for r in rows]


def around(message_id: int, before: int = 3, after: int = 3) -> list[dict]:
    """The messages around one hit, so a result can be read in context."""
    with db.session() as conn:
        hit = conn.execute("SELECT conversation FROM messages WHERE id=?", (int(message_id),)).fetchone()
        if not hit:
            return []
        rows = conn.execute(
            "SELECT * FROM (SELECT id, role, content, created_at FROM messages WHERE conversation=? AND id<=? "
            "ORDER BY id DESC LIMIT ?) UNION ALL SELECT id, role, content, created_at FROM "
            "(SELECT id, role, content, created_at FROM messages WHERE conversation=? AND id>? ORDER BY id LIMIT ?) "
            "ORDER BY id", (hit["conversation"], int(message_id), before + 1, hit["conversation"],
                            int(message_id), after)).fetchall()
        return [dict(r) for r in rows]


def as_text(hits: list[dict]) -> str:
    if not hits:
        return "No past messages matched."
    return "\n".join(f"- [{h['created_at'][:10]}] ({h['conversation']}, {h['role']}) {h['snippet']}" for h in hits)
