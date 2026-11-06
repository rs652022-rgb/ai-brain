"""Several agents in one Brain (like OpenClaw's multi-agent routing).

The main agent is your Brain itself. Extra agents share your memories and Identity File but each
has its own name, persona, model, tools, chat apps and conversation, e.g. a "Sales" agent bound to
WhatsApp that answers in a formal tone with a cheap fast model, or a "Coder" agent on Discord.
"""
import re

from . import db, settings

MAIN = "main"


def _clean(a: dict) -> dict:
    aid = re.sub(r"[^a-z0-9-]+", "-", str(a.get("id") or a.get("name") or "").lower()).strip("-")[:30]
    if not aid or aid == MAIN:
        raise ValueError("Give the agent a name (not 'main').")
    return {
        "id": aid,
        "name": str(a.get("name") or aid).strip()[:40],
        "persona": str(a.get("persona") or "").strip()[:4000],
        "model": str(a.get("model") or "").strip()[:120],          # "" = your normal order, or "provider:model"
        "mode": a.get("mode") if a.get("mode") in ("mirror", "assistant", "coach") else "assistant",
        "tools_off": [str(t) for t in (a.get("tools_off") or [])][:20],
        "channels": [str(c) for c in (a.get("channels") or [])][:20],
    }


def list_agents() -> list[dict]:
    return list(settings.load().get("agents") or [])


def get(aid: str) -> dict | None:
    return next((a for a in list_agents() if a["id"] == aid), None)


def save(agent: dict) -> dict:
    a = _clean(agent)
    data = settings.load()
    items = [x for x in (data.get("agents") or []) if x["id"] != a["id"]]
    for other in items:                      # one agent per chat app
        other["channels"] = [c for c in other.get("channels", []) if c not in a["channels"]]
    data["agents"] = (items + [a])[:12]
    settings.save(data)
    db.audit("owner", "agent.save", agent=a["id"], channels=a["channels"])
    return a


def delete(aid: str) -> None:
    data = settings.load()
    data["agents"] = [x for x in (data.get("agents") or []) if x["id"] != aid]
    settings.save(data)
    db.audit("owner", "agent.delete", agent=aid)


def for_channel(channel: str) -> dict | None:
    return next((a for a in list_agents() if channel in a.get("channels", [])), None)


def prefer(agent: dict | None) -> tuple[str, str] | None:
    """The model an agent asked for, as (provider, model)."""
    from .providers import PRESETS
    spec = (agent or {}).get("model") or ""
    if ":" in spec and spec.split(":", 1)[0].strip() in PRESETS:
        pid, model = spec.split(":", 1)
        return pid.strip(), model.strip()
    if spec:
        return "freellmapi", spec           # "auto:fast", "fusion"…: FreeLLMAPI's routing choices
    return None


def prompt_block(agent: dict | None) -> str:
    if not agent:
        return ""
    return (f"\n\n<agent>\nRight now you are acting as the agent \"{agent['name']}\" that the owner set up.\n"
            f"{agent['persona']}\nKeep the safety core and the owner's rules.\n</agent>")
