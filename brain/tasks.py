"""Plans and a task board.

- Todo list: the Brain keeps a short checklist for the task it is working on in a conversation, so you
  can see the plan and its progress (like Hermes' todo tool and MausBot's plan cards).
- Board: a simple kanban board (To do / Doing / Done / Blocked) that you and the Brain share for work
  that lasts longer than one chat.
"""
import json

from . import db

STATUSES = ("pending", "in_progress", "done")
COLUMNS = ("todo", "doing", "done", "blocked")
MAX_ITEMS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    conversation TEXT PRIMARY KEY,
    items TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS board_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    col TEXT NOT NULL DEFAULT 'todo',
    created_by TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class TaskError(ValueError):
    pass


def _conn():
    conn = db.connect()
    conn.executescript(SCHEMA)
    return conn


def _clean_items(items) -> list[dict]:
    if not isinstance(items, list):
        raise TaskError("items must be a list of {text, status}")
    out = []
    for it in items[:MAX_ITEMS]:
        if isinstance(it, str):
            it = {"text": it}
        if not isinstance(it, dict):
            continue
        text = str(it.get("text", "")).strip()[:200]
        if not text:
            continue
        status = str(it.get("status", "pending"))
        out.append({"text": text, "status": status if status in STATUSES else "pending"})
    return out


def _one_in_progress(items: list[dict]) -> list[dict]:
    """One step at a time: only the first in-progress item stays in progress."""
    seen, out = False, []
    for i in items:
        if i["status"] == "in_progress":
            if seen:
                i = i | {"status": "pending"}
            seen = True
        out.append(i)
    return out


def set_todos(conversation: str, items) -> list[dict]:
    clean = _one_in_progress(_clean_items(items))
    conn = _conn()
    try:
        conn.execute("INSERT INTO todos(conversation, items, updated_at) VALUES(?,?,?) ON CONFLICT(conversation) "
                     "DO UPDATE SET items=excluded.items, updated_at=excluded.updated_at",
                     (conversation or "main", json.dumps(clean), db.now()))
        conn.commit()
    finally:
        conn.close()
    return clean


def todos(conversation: str) -> list[dict]:
    conn = _conn()
    try:
        r = conn.execute("SELECT items FROM todos WHERE conversation=?", (conversation or "main",)).fetchone()
    finally:
        conn.close()
    return json.loads(r["items"]) if r else []


def todos_text(items: list[dict]) -> str:
    mark = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}
    return "\n".join(f"{mark[i['status']]} {i['text']}" for i in items) or "The list is empty."


# ---------- Board ----------

def _card(r) -> dict:
    return {"id": r["id"], "title": r["title"], "notes": r["notes"], "column": r["col"],
            "created_by": r["created_by"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def board() -> dict:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM board_cards ORDER BY updated_at DESC, id DESC").fetchall()
    finally:
        conn.close()
    out = {c: [] for c in COLUMNS}
    for r in rows:
        out.setdefault(r["col"], []).append(_card(r))
    return out


def add_card(title: str, notes: str = "", column: str = "todo", created_by: str = "owner") -> dict:
    title = str(title or "").strip()[:200]
    if not title:
        raise TaskError("A card needs a title.")
    if column not in COLUMNS:
        raise TaskError(f"column must be one of {', '.join(COLUMNS)}")
    conn = _conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM board_cards").fetchone()[0]
        if n >= 500:
            raise TaskError("The board is full (500 cards). Delete some done cards first.")
        cur = conn.execute("INSERT INTO board_cards(title, notes, col, created_by, created_at, updated_at) "
                           "VALUES(?,?,?,?,?,?)", (title, str(notes or "")[:2000], column, created_by, db.now(), db.now()))
        conn.commit()
        r = conn.execute("SELECT * FROM board_cards WHERE id=?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    db.audit(created_by, "board.add", card=r["id"])
    return _card(r)


def update_card(card_id: int, actor: str = "owner", **fields) -> dict:
    sets, params = [], []
    if "title" in fields and fields["title"] is not None:
        t = str(fields["title"]).strip()[:200]
        if not t:
            raise TaskError("A card needs a title.")
        sets.append("title=?")
        params.append(t)
    if "notes" in fields and fields["notes"] is not None:
        sets.append("notes=?")
        params.append(str(fields["notes"])[:2000])
    if "column" in fields and fields["column"] is not None:
        if fields["column"] not in COLUMNS:
            raise TaskError(f"column must be one of {', '.join(COLUMNS)}")
        sets.append("col=?")
        params.append(fields["column"])
    conn = _conn()
    try:
        if sets:
            conn.execute(f"UPDATE board_cards SET {', '.join(sets)}, updated_at=? WHERE id=?",
                         [*params, db.now(), int(card_id)])
            conn.commit()
        r = conn.execute("SELECT * FROM board_cards WHERE id=?", (int(card_id),)).fetchone()
    finally:
        conn.close()
    if not r:
        raise TaskError("No card with that number.")
    db.audit(actor, "board.update", card=int(card_id))
    return _card(r)


def delete_card(card_id: int, actor: str = "owner") -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM board_cards WHERE id=?", (int(card_id),))
        conn.commit()
    finally:
        conn.close()
    db.audit(actor, "board.delete", card=int(card_id))


def board_text() -> str:
    b = board()
    parts = []
    for col in COLUMNS:
        cards = b.get(col) or []
        if cards:
            parts.append(f"{col.upper()}:\n" + "\n".join(f"  #{c['id']} {c['title']}" for c in cards[:30]))
    return "\n".join(parts) or "The board is empty."
