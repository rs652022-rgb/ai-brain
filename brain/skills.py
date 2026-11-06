"""Skills: written know-how the Brain follows for a kind of task (like OpenClaw's SKILL.md).

Each skill is a folder data/skills/<name>/SKILL.md:

    ---
    name: client-proposal
    description: Write a web-development proposal for a new client
    ---
    1. Ask for budget and deadline...

The Brain sees every active skill's name and description and loads the full text when a task matches.
Self-learning: after a task that took several tool steps, the Brain may suggest a new skill; it stays
"suggested" until the owner switches it on. Nothing it suggests is used before that.
"""
import re
import shutil

import yaml

from . import config, db, llm, master

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")


def root():
    d = config.DATA_DIR / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:48]
    if not NAME_RE.match(s):
        raise ValueError("Use a short name with letters, numbers and dashes, e.g. client-proposal.")
    return s


def _parse(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), m.group(2).strip()


def _read(folder) -> dict | None:
    f = folder / "SKILL.md"
    if not f.is_file():
        return None
    meta, body = _parse(f.read_text(encoding="utf-8", errors="replace"))
    return {"name": folder.name, "description": str(meta.get("description", ""))[:300],
            "status": meta.get("status", "active") if meta.get("status") in ("active", "suggested", "off") else "active",
            "origin": str(meta.get("origin", "owner")), "body": body}


def list_all() -> list[dict]:
    return sorted((s for s in (_read(d) for d in root().iterdir() if d.is_dir()) if s), key=lambda s: s["name"])


def list_active() -> list[dict]:
    return [s for s in list_all() if s["status"] == "active"]


def get(name: str) -> dict | None:
    try:
        s = _read(root() / _slug(name))
    except ValueError:
        return None
    return s if s and s["status"] == "active" else None


def save(name: str, description: str, body: str, status: str = "active", origin: str = "owner") -> dict:
    slug = _slug(name)
    if not body.strip():
        raise ValueError("Write the skill's instructions.")
    meta = {"name": slug, "description": description.strip()[:300], "status": status, "origin": origin}
    d = root() / slug
    d.mkdir(exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)}---\n\n{body.strip()[:20000]}\n",
                                encoding="utf-8")
    db.audit("owner" if origin == "owner" else "brain", "skill.save", skill=slug, status=status)
    return _read(d)


def set_status(name: str, status: str) -> dict:
    s = _read(root() / _slug(name))
    if not s:
        raise ValueError("No such skill.")
    return save(s["name"], s["description"], s["body"], status if status in ("active", "suggested", "off") else "active",
                s["origin"])


def delete(name: str) -> None:
    shutil.rmtree(root() / _slug(name), ignore_errors=True)
    db.audit("owner", "skill.delete", skill=name)


REFLECT_SYSTEM = """You review a task you just finished for your owner and decide if it taught a reusable way of working.
Reply with JSON only: {"skill": null} when there is nothing reusable, or
{"skill": {"name": "short-dashed-name", "description": "when to use it, one line", "steps": "numbered steps"}}.
Only suggest a skill for a task likely to come again. Never include personal data, passwords or keys."""


def reflect(message: str, tools_used: list[str], reply: str) -> dict | None:
    """Self-learning: maybe suggest a skill from a multi-step task. Suggestions wait for the owner."""
    if not master.feature("learning"):
        return None
    try:
        out, _ = llm.chat(REFLECT_SYSTEM, [{"role": "user", "content":
                          f"Owner asked: {message[:1500]}\nTools used: {', '.join(tools_used)}\nYour answer: {reply[:2000]}"}],
                          max_tokens=500)
        data = llm._parse_json(out)
    except Exception:
        return None
    sk = (data or {}).get("skill") if isinstance(data, dict) else None
    if not isinstance(sk, dict) or not sk.get("name") or not sk.get("steps"):
        return None
    try:
        name = _slug(str(sk["name"]))
    except ValueError:
        return None
    if (root() / name).exists():
        return None
    return save(name, str(sk.get("description", "")), str(sk["steps"]), status="suggested", origin="self-learning")
