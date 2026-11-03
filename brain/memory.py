"""The memory system: add, tag, search, edit, forget, and the approval queue."""
import json
import math
import re
from datetime import datetime, timezone

import numpy as np

from . import config, db, llm

TAG_SYSTEM = (
    "You tag personal memories. Reply with JSON only: "
    '{"summary": one sentence, "tags": [up to 5 short topic words], '
    '"people": [names of people mentioned], "mood": one word or ""}. '
    "Never invent facts that are not in the text."
)


def _row(r) -> dict:
    d = dict(r)
    d.pop("embedding", None)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["people"] = json.loads(d.get("people") or "[]")
    d["locked"] = bool(d.get("locked"))
    return d


def _to_blob(vec: list[float] | None) -> bytes | None:
    return None if vec is None else np.asarray(vec, dtype=np.float32).tobytes()


def _embed(text: str) -> tuple[list[float] | None, str]:
    vec = llm.embed(text)
    return vec, (llm.embedding_label() if vec is not None else "")


def add(content: str, type: str = "episodic", source: str = "manual", sensitivity: str = "personal",
        happened_on: str = "", tags: list[str] | None = None, auto_tag: bool = True, actor: str = "owner") -> dict:
    content = content.strip()
    if not content:
        raise ValueError("Memory is empty")
    if type not in config.MEMORY_TYPES:
        raise ValueError(f"Unknown memory type: {type}")
    if sensitivity not in config.SENSITIVITY_LEVELS:
        raise ValueError(f"Unknown sensitivity: {sensitivity}")

    meta = {"summary": "", "tags": tags or [], "people": [], "mood": ""}
    if auto_tag:
        found = llm.private_json(TAG_SYSTEM, content)
        if isinstance(found, dict):
            meta["summary"] = str(found.get("summary", ""))[:300]
            meta["tags"] = list(dict.fromkeys((tags or []) + [str(t) for t in found.get("tags", [])][:5]))
            meta["people"] = [str(p) for p in found.get("people", [])][:10]
            meta["mood"] = str(found.get("mood", ""))[:30]

    vec, label = _embed(content)
    ts = db.now()
    with db.session() as conn:
        cur = conn.execute(
            """INSERT INTO memories (type, content, summary, tags, people, mood, sensitivity, source,
               happened_on, created_at, updated_at, embedding, embed_model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (type, content, meta["summary"], json.dumps(meta["tags"]), json.dumps(meta["people"]), meta["mood"],
             sensitivity, source, happened_on, ts, ts, _to_blob(vec), label),
        )
        mem_id = cur.lastrowid
    db.audit(actor, "memory.add", id=mem_id, type=type, source=source, sensitivity=sensitivity)
    return get(mem_id)


def get(mem_id: int) -> dict | None:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM memories WHERE id=?", (mem_id,)).fetchone()
    return _row(r) if r else None


def list_all(type: str | None = None, query: str = "", limit: int = 200) -> list[dict]:
    sql, args = "SELECT * FROM memories WHERE 1=1", []
    if type:
        sql += " AND type=?"
        args.append(type)
    if query:
        sql += " AND (content LIKE ? OR summary LIKE ? OR tags LIKE ?)"
        args += [f"%{query}%"] * 3
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db.session() as conn:
        return [_row(r) for r in conn.execute(sql, args).fetchall()]


def update(mem_id: int, actor: str = "owner", **fields) -> dict:
    current = get(mem_id)
    if not current:
        raise KeyError(mem_id)
    if current["locked"] and actor != "owner":
        raise PermissionError("This memory is locked; only the owner can change it.")
    allowed = {"content", "type", "sensitivity", "tags", "happened_on", "locked"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "tags":
            v = json.dumps(v)
        if k == "locked":
            v = int(bool(v))
        sets.append(f"{k}=?")
        args.append(v)
    if "content" in fields and fields["content"]:
        vec, label = _embed(fields["content"])
        sets += ["embedding=?", "embed_model=?"]
        args += [_to_blob(vec), label]
    if not sets:
        return current
    sets.append("updated_at=?")
    args += [db.now(), mem_id]
    with db.session() as conn:
        conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", args)
    db.audit(actor, "memory.update", id=mem_id, fields=sorted(k for k in fields if k in allowed))
    return get(mem_id)


def forget(mem_id: int) -> bool:
    """Delete a memory everywhere it is stored. The audit log records that it was forgotten, not what it said."""
    with db.session() as conn:
        n = conn.execute("DELETE FROM memories WHERE id=?", (mem_id,)).rowcount
    if n:
        db.audit("owner", "memory.forget", id=mem_id)
    return bool(n)


# ---------- Recall ----------

_WORD = re.compile(r"[a-zA-Zऀ-ॿ0-9']+")
_STOP = set("the a an and or but to of in on for with is are was were i me my you your it this that what how why "
            "when do did does be have has had about can could should would will just also from them they there "
            "here then than tell know want need like get got make please okay yes not now today "
            # common Hindi/Hinglish fillers, so they don't count as topic matches
            "hai hain kya kar karo karna mein main mera meri mere tum tumhara aap yeh woh aur toh bhi nahi nahin nhi "
            "kuch kuchh sab ho hun hoon tha thi the ka ki ke ko se par ek bas acha accha haan batao wala wali "
            "kaise kaisa theek thik badhiya hello hey hii".split())

# Small talk never needs memories: recalling for "hello" only pulls in unrelated things.
_SMALL_TALK = set("hi hii hello hey hlo namaste namaskar hola yo kaise kaisa kaisi ho hai hain kya haal chal raha rahi "
                  "sab badhiya theek thik tum batao aap how are you doing good morning afternoon evening night ok okay "
                  "thanks thank you thx bye yes no haan nahi nahin kuch kuchh acha accha hmm sure cool great nice "
                  "what's up whats sup bhai yaar".split())


def is_small_talk(text: str) -> bool:
    words = [w.lower() for w in _WORD.findall(text)]
    return 0 < len(words) <= 8 and all(w in _SMALL_TALK for w in words)


SEM_FLOOR = 0.62   # embedding models give unrelated texts ~0.4-0.6; below this, "similar meaning" is noise


def select_relevant(cands: list[tuple], limit: int) -> list[tuple]:
    """cands: (cosine or None, keyword_fraction, bonus, row). Returns [(score, row)] that are truly relevant.

    A candidate counts only if its meaning clearly stands out (cosine above the floor AND above the typical
    similarity of everything stored), or it shares real topic words with the query."""
    cosines = [c for c, *_ in cands if c is not None]
    standout = SEM_FLOOR
    if len(cosines) >= 6:
        arr = np.asarray(cosines)
        standout = max(SEM_FLOOR, float(arr.mean() + 0.8 * arr.std()))
    kept = []
    for cos, kw, bonus, row in cands:
        sem_ok = cos is not None and cos >= standout
        kw_ok = kw > 0          # shares at least one real topic word (fillers and greetings are excluded)
        if not (sem_ok or kw_ok):
            continue
        kept.append(((0.7 * cos if sem_ok else 0.0) + 0.5 * kw + bonus, row))
    kept.sort(key=lambda x: x[0], reverse=True)
    if kept:
        top = kept[0][0]
        kept = [k for k in kept if k[0] >= 0.6 * top]   # drop weak matches next to a strong one
    return kept[:limit]


def _stem(w: str) -> str:
    for end in ("ing", "ies", "es", "ed", "s"):
        if len(w) > len(end) + 2 and w.endswith(end):
            return w[: -len(end)] + ("y" if end == "ies" else "")
    return w


def _keywords(text: str) -> set[str]:
    """Topic words: 3+ letters, not filler words, lightly stemmed (lives -> live)."""
    return {_stem(w.lower()) for w in _WORD.findall(text) if len(w) > 2 and w.lower() not in _STOP}


def _recency(created_at: str) -> float:
    try:
        age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(created_at)).days
    except ValueError:
        return 0.0
    return math.exp(-age_days / 180)  # half-weight after about four months


def search(query: str, limit: int = config.RECALL_LIMIT, include_sealed: bool = False) -> list[dict]:
    """Hybrid recall: meaning (embeddings) and words, with a strict relevance bar."""
    if is_small_talk(query):
        return []
    qvec, qlabel = _embed(query)
    qwords = _keywords(query)
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM memories").fetchall()
    q = np.asarray(qvec, dtype=np.float32) if qvec is not None else None
    cands = []
    for r in rows:
        if r["sensitivity"] == "sealed" and not include_sealed:
            continue
        cos = None
        if q is not None and r["embedding"] and r["embed_model"] == qlabel:
            v = np.frombuffer(r["embedding"], dtype=np.float32)
            if v.shape == q.shape:
                cos = float(v @ q / (np.linalg.norm(v) * np.linalg.norm(q) + 1e-9))
        words = _keywords(" ".join([r["content"], r["summary"] or "", r["tags"] or "", r["people"] or ""]))
        kw = len(qwords & words) / len(qwords) if qwords else 0.0
        bonus = 0.08 * _recency(r["created_at"]) + (0.04 if r["type"] == "semantic" else 0.0)
        cands.append((cos, kw, bonus, r))
    return [_row(r) | {"score": round(s, 3)} for s, r in select_relevant(cands, limit)]


def reindex() -> dict:
    """Re-embed every memory with the current embedding model (after switching providers)."""
    with db.session() as conn:
        rows = conn.execute("SELECT id, content FROM memories").fetchall()
    done = 0
    for r in rows:
        vec, label = _embed(r["content"])
        if vec is None:
            break
        with db.session() as conn:
            conn.execute("UPDATE memories SET embedding=?, embed_model=? WHERE id=?", (_to_blob(vec), label, r["id"]))
        done += 1
    db.audit("owner", "memory.reindex", done=done, total=len(rows))
    return {"done": done, "total": len(rows), "model": llm.embedding_label()}


def index_coverage() -> dict:
    label = llm.embedding_label()
    with db.session() as conn:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        ok = conn.execute("SELECT COUNT(*) FROM memories WHERE embed_model=? AND embed_model!=''", (label,)).fetchone()[0]
    return {"model": label, "indexed": ok, "total": total}


# ---------- Approval queue (learned patterns are proposed, never silently added) ----------

def propose(content: str, type: str = "semantic", reason: str = "", source: str = "chat") -> int:
    with db.session() as conn:
        dup = conn.execute("SELECT id FROM pending_memories WHERE content=?", (content,)).fetchone()
        if dup:
            return dup["id"]
        cur = conn.execute(
            "INSERT INTO pending_memories (type, content, reason, source, created_at) VALUES (?,?,?,?,?)",
            (type if type in config.MEMORY_TYPES else "semantic", content, reason, source, db.now()),
        )
        pid = cur.lastrowid
    db.audit("brain", "memory.propose", pending_id=pid)
    return pid


def pending() -> list[dict]:
    with db.session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM pending_memories ORDER BY id DESC").fetchall()]


def approve(pid: int, sensitivity: str = "personal") -> dict:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM pending_memories WHERE id=?", (pid,)).fetchone()
        if not r:
            raise KeyError(pid)
        conn.execute("DELETE FROM pending_memories WHERE id=?", (pid,))
    db.audit("owner", "memory.approve", pending_id=pid)
    return add(r["content"], type=r["type"], source=r["source"], sensitivity=sensitivity)


def reject(pid: int) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM pending_memories WHERE id=?", (pid,))
    db.audit("owner", "memory.reject", pending_id=pid)


def stats() -> dict:
    with db.session() as conn:
        by_type = {r["type"]: r["n"] for r in conn.execute("SELECT type, COUNT(*) n FROM memories GROUP BY type")}
        n_pending = conn.execute("SELECT COUNT(*) FROM pending_memories").fetchone()[0]
    return {"by_type": by_type, "total": sum(by_type.values()), "pending": n_pending}


# ---------- Standing instructions (rules the owner teaches) ----------

def add_rule(text: str) -> dict:
    """A standing instruction from the owner, e.g. 'Always reply to clients in formal English.'
    Rules are always included in the Brain's instructions, are locked, and only the owner can change them."""
    text = text.strip()
    if not text:
        raise ValueError("Rule is empty")
    mem = add(text, type="procedural", source="instruction", sensitivity="personal", tags=["rule"], auto_tag=False)
    return update(mem["id"], actor="owner", locked=True)


def rules() -> list[dict]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM memories WHERE type='procedural' AND tags LIKE '%\"rule\"%' ORDER BY id").fetchall()
    return [_row(r) for r in rows]
