"""Other memory engines you can switch on, next to the built-in one (SQLite plus embeddings).

- LanceDB (free, open source, runs inside the Brain): a real vector database for fast recall when you have
  many thousands of memories. Memories stay in SQLite as the source of truth; LanceDB keeps the vectors.
  `pip install lancedb`.
- Honcho (open source by Plastic Labs, self-host for free or use their cloud): it builds a running
  understanding of you from your conversations. The Brain sends your chat turns to it and can ask it
  questions with the deep_memory tool. `pip install honcho-ai`. Experimental.

Settings → Memory engines. Both are optional; turning them off changes nothing else.
"""
import importlib.util
import logging
import threading

from . import config, db, settings

log = logging.getLogger("brain.memory_engines")
_tables: dict[str, object] = {}
_lock = threading.Lock()


def cfg() -> dict:
    c = settings.load().get("memory_engines") or {}
    return {"vector": c.get("vector", "builtin"), "honcho": bool(c.get("honcho")),
            "honcho_url": c.get("honcho_url", ""), "honcho_key": c.get("honcho_key", ""),
            "honcho_workspace": c.get("honcho_workspace", "ai-brain")}


def public() -> dict:
    c = cfg()
    c["has_honcho_key"], c["honcho_key"] = bool(c["honcho_key"]), ""
    c["lancedb_installed"] = importlib.util.find_spec("lancedb") is not None
    c["honcho_installed"] = importlib.util.find_spec("honcho") is not None
    return c


def save(changes: dict) -> dict:
    data = settings.load()
    c = data.get("memory_engines") or {}
    for k in ("vector", "honcho", "honcho_url", "honcho_key", "honcho_workspace"):
        if k in changes and not (k == "honcho_key" and not changes[k]):
            c[k] = changes[k]
    if c.get("vector") not in (None, "builtin", "lancedb"):
        raise ValueError("Vector engine is builtin or lancedb.")
    if c.get("vector") == "lancedb" and importlib.util.find_spec("lancedb") is None:
        raise ValueError("LanceDB isn't installed: pip install lancedb")
    if c.get("honcho") and importlib.util.find_spec("honcho") is None:
        raise ValueError("Honcho isn't installed: pip install honcho-ai")
    if c.get("honcho_url"):
        from . import web
        if not str(c["honcho_url"]).startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("Honcho address must be https:// (or http://localhost for self-hosted).")
        if config.MULTIUSER and str(c["honcho_url"]).startswith("http://"):
            raise ValueError("On a shared server use an https:// Honcho address.")
        if str(c["honcho_url"]).startswith("https://"):
            web.check_url(c["honcho_url"])
    data["memory_engines"] = c
    settings.save(data)
    db.audit("owner", "memory_engines.update", vector=c.get("vector"), honcho=bool(c.get("honcho")))
    if c.get("vector") == "lancedb":
        config.spawn(rebuild)
    return public()


# ---------- LanceDB ----------

def _table(dim: int):
    import lancedb
    import pyarrow as pa
    key = f"{config.DATA_DIR}:{dim}"
    with _lock:
        if key in _tables:
            return _tables[key]
        conn = lancedb.connect(str(config.DATA_DIR / "lancedb"))
        name = f"memories_{dim}"
        try:
            names = list(getattr(conn.list_tables(), "tables", None) or conn.list_tables())
        except Exception:
            names = conn.table_names()
        if name in names:
            t = conn.open_table(name)
        else:
            schema = pa.schema([pa.field("id", pa.int64()), pa.field("vector", pa.list_(pa.float32(), dim))])
            t = conn.create_table(name, schema=schema)
        _tables[key] = t
        return t


def lance_on() -> bool:
    return cfg()["vector"] == "lancedb" and importlib.util.find_spec("lancedb") is not None


def lance_add(mem_id: int, vec) -> None:
    if not lance_on() or vec is None:
        return
    try:
        t = _table(len(vec))
        t.delete(f"id = {int(mem_id)}")
        t.add([{"id": int(mem_id), "vector": [float(x) for x in vec]}])
    except Exception as exc:
        log.warning("LanceDB add failed: %s", exc)


def lance_delete(mem_id: int) -> None:
    if not lance_on():
        return
    for t in list(_tables.values()):
        try:
            t.delete(f"id = {int(mem_id)}")
        except Exception:
            pass


def lance_search(vec, limit: int = 200) -> list[int] | None:
    """Nearest memory ids, or None to fall back to the built-in search."""
    if not lance_on() or vec is None:
        return None
    try:
        rows = _table(len(vec)).search([float(x) for x in vec]).metric("cosine").limit(limit).to_list()
        return [int(r["id"]) for r in rows]
    except Exception as exc:
        log.warning("LanceDB search failed: %s", exc)
        return None


def rebuild() -> int:
    import numpy as np
    with db.session() as conn:
        rows = conn.execute("SELECT id, embedding FROM memories WHERE embedding IS NOT NULL").fetchall()
    n = 0
    for r in rows:
        lance_add(r["id"], np.frombuffer(r["embedding"], dtype=np.float32).tolist())
        n += 1
    db.audit("system", "lancedb.rebuild", count=n)
    return n


# ---------- Honcho ----------

def _honcho():
    from honcho import Honcho
    c = cfg()
    kw = {"workspace_id": c["honcho_workspace"] or "ai-brain", "timeout": 20}
    if c["honcho_url"]:
        kw["base_url"] = c["honcho_url"]
    if c["honcho_key"]:
        kw["api_key"] = c["honcho_key"]
    return Honcho(**kw)


def honcho_on() -> bool:
    c = cfg()
    return c["honcho"] and importlib.util.find_spec("honcho") is not None and bool(c["honcho_url"] or c["honcho_key"])


def honcho_record(conversation: str, user_text: str, reply: str) -> None:
    """Mirror one chat turn to Honcho (in the background; sealed/private turns are never sent)."""
    if not honcho_on() or not conversation or conversation.startswith(("goal:", "mailwatch")):
        return

    def go():
        try:
            h = _honcho()
            owner, brain = h.peer("owner"), h.peer("brain")
            s = h.session(conversation.replace(":", "-")[:60] or "main")
            s.add_messages([owner.message(user_text[:8000]), brain.message(reply[:8000])])
        except Exception as exc:
            log.warning("Honcho record failed: %s", exc)
    config.spawn(go)


def honcho_ask(question: str) -> str:
    if not honcho_on():
        return "Honcho isn't switched on (Settings → Memory engines)."
    h = _honcho()
    out = h.peer("owner").chat(question[:2000], timeout=60)
    return str(getattr(out, "content", out) or "Honcho has nothing on that yet.")[:4000]
