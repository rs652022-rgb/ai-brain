"""The Brain writes and improves its own skills, safely (Hermes-style self-improvement, with you in charge).

- scan(): a safety check for any skill text, from the Brain, the marketplace or an import. It looks for
  hidden instructions, attempts to read or send secrets, destructive commands and disguised code.
- create()/improve(): the skill_manage tool. New or changed skills from the Brain wait as "suggested"
  until you switch them on, unless Settings "auto_skills" is on AND the scan is clean.
  Skills you wrote yourself are never changed by the Brain: it suggests a new version instead.
- record_use()/rate(): a ledger of how often each skill is used and how well it worked, so weak skills
  can be spotted and improved.
"""
import re
import unicodedata

from . import db, settings, skills

# (label, pattern) — case-insensitive. Kept specific to avoid flagging normal how-to text.
THREATS = [
    ("hidden instructions", r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|messages)"),
    ("hidden instructions", r"disregard (your|the|all) (rules|instructions|system prompt|guidelines)"),
    ("hidden instructions", r"(reveal|print|show|repeat) (your|the) (system prompt|hidden instructions)"),
    ("hidden instructions", r"you are no longer|new instructions:|do not tell the (owner|user)"),
    ("secret access", r"(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.env\b|keychain|credential manager|/etc/shadow)"),
    ("secret access", r"(api[_ ]?key|password|token|secret)s?\b.{0,40}\b(send|post|upload|email|curl|webhook|paste)"),
    ("sending data out", r"(curl|wget|invoke-webrequest|iwr)\b[^\n]{0,80}(-d|--data|-F|--upload-file|-Body)\b"),
    ("sending data out", r"(webhook\.site|requestbin|pastebin\.com|ngrok\.io|transfer\.sh)"),
    ("destructive command", r"rm\s+-rf\s+(/|~|\$home|\*)|del\s+/[sq]\b.*[a-z]:\\|format\s+[a-z]:|mkfs\.|diskpart"),
    ("destructive command", r"(shutdown|reboot)\s+(-h|/s|/r|now)|:\(\)\s*\{\s*:\|:&\s*\};:|drop\s+database"),
    ("disguised code", r"base64\s+(-d|--decode)[^\n]{0,40}\|\s*(sh|bash|python|powershell)"),
    ("disguised code", r"(curl|wget|iwr)[^\n]{0,120}\|\s*(sh|bash|iex|python|powershell)"),
    ("disguised code", r"\b(eval|exec)\s*\(\s*(base64|atob|bytes\.fromhex|codecs\.decode)"),
    ("disguised code", r"-encodedcommand|frombase64string"),
]
_COMPILED = [(label, re.compile(p, re.I)) for label, p in THREATS]
INVISIBLE = {"Cf"}                     # zero-width and bidi control characters hide text from people


def scan(text: str) -> list[dict]:
    """[{issue, excerpt}] — empty when nothing looks unsafe."""
    text = str(text or "")
    found = []
    for label, rx in _COMPILED:
        m = rx.search(text)
        if m:
            s = max(0, m.start() - 30)
            found.append({"issue": label, "excerpt": text[s:m.end() + 30].replace("\n", " ")[:160]})
    hidden = [c for c in text if unicodedata.category(c) in INVISIBLE and c not in "­"]
    if hidden:
        found.append({"issue": "invisible characters", "excerpt": f"{len(hidden)} hidden character(s), e.g. U+{ord(hidden[0]):04X}"})
    seen, out = set(), []
    for f in found:
        key = (f["issue"], f["excerpt"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def scan_skill(name: str) -> list[dict]:
    """Safety check of a saved skill (empty when clean or missing)."""
    s = skills._read(skills.root() / skills._slug(name))
    return scan(s["description"] + "\n" + s["body"]) if s else []


def _status_for(findings: list[dict]) -> str:
    return "active" if (not findings and settings.load().get("auto_skills", False)) else "suggested"


def create(name: str, description: str, body: str) -> dict:
    """A skill written by the Brain. Returns {skill, status, findings}."""
    full = f"{description}\n{body}"
    findings = scan(full)
    try:
        existing = skills._read(skills.root() / skills._slug(name))
    except ValueError as exc:
        raise ValueError(str(exc))
    if existing and existing["origin"] == "owner":
        return improve(name, body, description)
    s = skills.save(name, description, body, status=_status_for(findings), origin="brain")
    db.audit("brain", "skill.forge", skill=s["name"], status=s["status"], findings=len(findings))
    return {"skill": s["name"], "status": s["status"], "findings": findings}


def improve(name: str, body: str, description: str = "") -> dict:
    """A better version of an existing skill. Owner skills get a separate '-v2' suggestion."""
    old = skills._read(skills.root() / skills._slug(name))
    if not old:
        raise ValueError("No skill with that name to improve. Use op create.")
    desc = (description or old["description"]).strip()
    findings = scan(f"{desc}\n{body}")
    if old["origin"] == "owner":
        target = skills._slug(f"{old['name'][:44]}-v2")
        s = skills.save(target, desc, body, status="suggested", origin="brain")
        db.audit("brain", "skill.forge_suggest", skill=old["name"], suggestion=target, findings=len(findings))
        return {"skill": s["name"], "status": "suggested", "findings": findings,
                "note": f"{old['name']} is the owner's own skill, so the new version waits as {target}."}
    status = "suggested" if findings else (old["status"] if old["status"] == "active" else _status_for(findings))
    s = skills.save(old["name"], desc, body, status=status, origin="brain")
    db.audit("brain", "skill.forge_improve", skill=s["name"], status=s["status"], findings=len(findings))
    return {"skill": s["name"], "status": s["status"], "findings": findings}


# ---------- Usage ledger ----------

LEDGER = """CREATE TABLE IF NOT EXISTS skill_usage (
    name TEXT PRIMARY KEY, uses INTEGER NOT NULL DEFAULT 0, good INTEGER NOT NULL DEFAULT 0,
    bad INTEGER NOT NULL DEFAULT 0, last_used TEXT)"""


def record_use(name: str) -> None:
    with db.session() as conn:
        conn.execute(LEDGER)
        conn.execute("INSERT INTO skill_usage(name, uses, last_used) VALUES(?, 1, ?) ON CONFLICT(name) "
                     "DO UPDATE SET uses = uses + 1, last_used = excluded.last_used", (name, db.now()))


def rate(name: str, good: bool) -> dict:
    col = "good" if good else "bad"
    with db.session() as conn:
        conn.execute(LEDGER)
        conn.execute(f"INSERT INTO skill_usage(name, {col}) VALUES(?, 1) ON CONFLICT(name) DO UPDATE SET {col} = {col} + 1",
                     (name,))
    return ledger().get(name, {})


def ledger() -> dict:
    with db.session() as conn:
        conn.execute(LEDGER)
        rows = conn.execute("SELECT * FROM skill_usage").fetchall()
    return {r["name"]: {"uses": r["uses"], "good": r["good"], "bad": r["bad"], "last_used": r["last_used"],
                        "score": round((r["good"] + 1) / (r["good"] + r["bad"] + 2), 2)} for r in rows}


def needs_work(min_ratings: int = 3) -> list[str]:
    """Skills people rated poorly: candidates for the Brain to improve."""
    return sorted(n for n, x in ledger().items() if x["good"] + x["bad"] >= min_ratings and x["score"] < 0.5)
