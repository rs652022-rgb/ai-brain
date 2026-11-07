"""Skill marketplace: find skills others made and add them to your Brain.

1. ClawHub (clawhub.ai), OpenClaw's public skill registry. Skills there are the same SKILL.md format.
   We only show skills ClawHub hasn't flagged as suspicious, and every skill you add arrives as
   "suggested": you read it and switch it on yourself. Skills are instructions, not code, so a skill can't
   run anything by itself, but read it first anyway: it tells the Brain what to do.
2. This server's own shelf: on a shared server, people can publish their skills for everyone on it.
   The admin approves each one before others can see it.
"""
import io
import json
import re
import threading
import time
import zipfile

import httpx

from . import config, db, plugins, skills

CLAWHUB = "https://clawhub.ai"
SLUG_RE = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9_.@-]{0,80}(/[A-Za-z0-9][A-Za-z0-9_.-]{0,80})?$")
_lock = threading.Lock()


class MarketError(RuntimeError):
    pass


def _get(path: str, params: dict) -> httpx.Response:
    try:
        r = httpx.get(CLAWHUB + path, params=params, timeout=25, follow_redirects=True,
                      headers={"User-Agent": "AI-Brain/1.0 (skill browser)", "Accept": "application/json, application/zip"})
    except httpx.HTTPError as exc:
        raise MarketError(f"Couldn't reach ClawHub: {exc}") from exc
    if r.status_code == 429:
        raise MarketError("ClawHub is busy (rate limit). Try again in a minute.")
    if r.status_code >= 400:
        raise MarketError(f"ClawHub said {r.status_code}.")
    return r


def clawhub_search(query: str, limit: int = 20) -> list[dict]:
    q = (query or "").strip()[:100]
    if not q:
        raise MarketError("Type what kind of skill you want.")
    data = _get("/api/v1/search", {"q": q, "limit": max(1, min(50, limit)), "nonSuspiciousOnly": "true"}).json()
    out = []
    for x in data.get("results", [])[:limit]:
        slug = str(x.get("slug") or "")
        if not SLUG_RE.match(slug) or ".." in slug:
            continue
        out.append({"slug": slug, "name": str(x.get("displayName") or slug)[:80], "summary": str(x.get("summary") or "")[:300],
                    "version": str(x.get("version") or ""), "owner": str(x.get("ownerHandle") or ""),
                    "url": f"{CLAWHUB}/skills/{slug}"})
    return out


def clawhub_install(slug: str) -> list[str]:
    if not SLUG_RE.match(slug or "") or ".." in slug:
        raise MarketError("That isn't a ClawHub skill name.")
    r = _get("/api/v1/download", {"slug": slug})
    body = r.content
    if len(body) > 5_000_000:
        raise MarketError("That skill is too big.")
    if body[:2] != b"PK":
        try:
            info = json.loads(body)
        except ValueError:
            raise MarketError("ClawHub didn't send a skill file.")
        raise MarketError("This skill lives on GitHub: " + str(info.get("url") or info.get("repo") or "see its ClawHub page")
                          + ". Download its SKILL.md and add it in Skills → Import.")
    added = plugins.import_skills(f"{slug.split('/')[-1]}.zip", body)
    if not added:
        raise MarketError("No SKILL.md found in that download.")
    db.audit("owner", "market.clawhub_install", slug=slug, skills=added)
    return added


# ---------- This server's shelf ----------

def _shelf_file():
    return config.ROOT_DATA / "skill_shelf.json"


def _shelf() -> list[dict]:
    try:
        return json.loads(_shelf_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_shelf(items: list[dict]) -> None:
    f = _shelf_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(items, indent=1), encoding="utf-8")


def shelf(admin: bool = False) -> list[dict]:
    me = config.user_key()
    out = []
    for x in _shelf():
        if x["status"] == "approved" or admin or x["publisher_key"] == me:
            out.append({k: v for k, v in x.items() if k not in ("body", "publisher_key")} | {"mine": x["publisher_key"] == me})
    return out


def publish(skill_name: str, publisher: str) -> dict:
    s = next((x for x in skills.list_all() if x["name"] == skill_name), None)
    if not s:
        raise MarketError("No such skill.")
    body = (skills.root() / skill_name / "SKILL.md").read_text(encoding="utf-8")
    with _lock:
        items = [x for x in _shelf() if not (x["name"] == skill_name and x["publisher_key"] == config.user_key())]
        item = {"id": f"{int(time.time()*1000):x}", "name": skill_name, "description": s["description"][:300],
                "publisher": publisher[:60], "publisher_key": config.user_key(), "body": body[:20000],
                "status": "approved" if not config.MULTIUSER else "waiting", "published": time.strftime("%Y-%m-%d"),
                "installs": 0}
        items.append(item)
        _save_shelf(items)
    db.audit("owner", "market.publish", skill=skill_name)
    return {k: v for k, v in item.items() if k not in ("body", "publisher_key")}


def moderate(item_id: str, approve: bool) -> None:
    with _lock:
        items = _shelf()
        for x in items:
            if x["id"] == item_id:
                x["status"] = "approved" if approve else "rejected"
        _save_shelf(items)


def unpublish(item_id: str, admin: bool = False) -> None:
    with _lock:
        _save_shelf([x for x in _shelf() if not (x["id"] == item_id and (admin or x["publisher_key"] == config.user_key()))])


def shelf_install(item_id: str) -> list[str]:
    with _lock:
        items = _shelf()
        x = next((i for i in items if i["id"] == item_id and (i["status"] == "approved" or i["publisher_key"] == config.user_key())), None)
        if not x:
            raise MarketError("That skill isn't on the shelf.")
        x["installs"] = x.get("installs", 0) + 1
        _save_shelf(items)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{x['name']}/SKILL.md", x["body"])
    return plugins.import_skills("shelf.zip", buf.getvalue())
