"""Master Control: the owner's settings for who the Brain is, how it is woken and rested,
which features are switched on, and who may open it.

Stored in data/settings.json under "master". The PIN is never stored, only a salted hash.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time

from . import config, settings

DEFAULTS = {
    "brain_name": "Brain",                   # what the Brain calls itself; shown in the header
    "owner_name": config.OWNER_NAME,         # your name
    "greeting": "Hey {first}. What's on your mind?",
    "subtitle": "Talk it through out loud or type. I answer from your memories, in your own way of thinking.",
    "suggestions": ["How was my week?", "Help me plan tomorrow", "What did I decide about pricing?"],
    "default_mode": "mirror",
    "theme": "blueprint",                    # blueprint (light, cobalt) | midnight (dark)
    "accent": "#ffd84a",                     # signal colour
    "wake_words": ["hey brain"],             # wake the Brain from rest, and the voice wake word
    "sleep_words": ["go to sleep", "rest now", "so jao"],
    "stop_words": ["stop", "ruko", "bas", "be quiet"],
    "resting": False,                        # resting: no answers, no background work, until woken
    "features": {"web": True, "learning": True, "voice": True, "suggest_facts": True},
    "timezone": "",                          # IANA name, e.g. Asia/Kolkata (set from the browser)
    "lock_app": False,                       # require the PIN to open the app at all
    "pin_hash": "",
    "pin_salt": "",
}
PUBLIC_KEYS = ("brain_name", "owner_name", "greeting", "subtitle", "suggestions", "default_mode", "theme",
               "accent", "wake_words", "sleep_words", "stop_words", "resting", "features", "lock_app", "timezone")
THEMES = ("blueprint", "midnight")
MODES = ("mirror", "assistant", "coach")

_lock = threading.Lock()


def get() -> dict:
    saved = settings.load().get("master") or {}
    out = DEFAULTS | {k: v for k, v in saved.items() if k in DEFAULTS}
    out["features"] = DEFAULTS["features"] | (saved.get("features") or {})
    return out


def public() -> dict:
    """Everything the interface needs (no PIN data). Safe to show before unlocking."""
    m = get()
    out = {k: m[k] for k in PUBLIC_KEYS}
    out["owner_first"] = (m["owner_name"] or "there").split()[0]
    out["greeting"] = m["greeting"].replace("{first}", out["owner_first"]).replace("{name}", m["brain_name"])
    out["greeting_template"] = m["greeting"]
    out["pin_set"] = bool(m["pin_hash"])
    return out


def _clean_words(value, fallback: list[str]) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    words = [re.sub(r"\s+", " ", str(w)).strip().lower() for w in (value or [])]
    words = [w for w in dict.fromkeys(words) if 1 < len(w) <= 40][:8]
    return words or fallback


def update(changes: dict) -> dict:
    """Apply owner edits. Unknown keys and the PIN fields are ignored here."""
    with _lock:
        data = settings.load()
        m = get()
        if "brain_name" in changes:
            m["brain_name"] = str(changes["brain_name"]).strip()[:40] or DEFAULTS["brain_name"]
        if "owner_name" in changes:
            m["owner_name"] = str(changes["owner_name"]).strip()[:60] or DEFAULTS["owner_name"]
        if "greeting" in changes:
            m["greeting"] = str(changes["greeting"]).strip()[:120] or DEFAULTS["greeting"]
        if "subtitle" in changes:
            m["subtitle"] = str(changes["subtitle"]).strip()[:240]
        if "suggestions" in changes:
            items = changes["suggestions"]
            if isinstance(items, str):
                items = items.splitlines()
            m["suggestions"] = [str(s).strip()[:80] for s in items if str(s).strip()][:6]
        if changes.get("default_mode") in MODES:
            m["default_mode"] = changes["default_mode"]
        if changes.get("theme") in THEMES:
            m["theme"] = changes["theme"]
        if "accent" in changes and re.fullmatch(r"#[0-9a-fA-F]{6}", str(changes["accent"])):
            m["accent"] = str(changes["accent"]).lower()
        for key in ("wake_words", "sleep_words", "stop_words"):
            if key in changes:
                m[key] = _clean_words(changes[key], DEFAULTS[key])
        if isinstance(changes.get("features"), dict):
            m["features"] = m["features"] | {k: bool(v) for k, v in changes["features"].items() if k in DEFAULTS["features"]}
        if "lock_app" in changes:
            m["lock_app"] = bool(changes["lock_app"]) and bool(m["pin_hash"])   # can't lock without a PIN
        if "resting" in changes:
            m["resting"] = bool(changes["resting"])
        if "timezone" in changes:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(str(changes["timezone"]))
                m["timezone"] = str(changes["timezone"])[:64]
            except Exception:
                pass
        data["master"] = m
        settings.save(data)
    return public()


def set_resting(on: bool) -> None:
    update({"resting": on})


def owner_name() -> str:
    return get()["owner_name"] or DEFAULTS["owner_name"] or "the owner"


def brain_name() -> str:
    return get()["brain_name"] or DEFAULTS["brain_name"]


def feature(name: str) -> bool:
    return bool(get()["features"].get(name, True))


# ---------- Spoken / typed commands ----------

def _norm(text: str) -> str:
    return re.sub(r"[^\w\sऀ-ॿ]", "", text.lower()).strip()


def matches(text: str, words: list[str]) -> bool:
    """True when the message is (or starts with) one of the command phrases."""
    t = _norm(text)
    return any(t == _norm(w) or t.startswith(_norm(w) + " ") for w in words if _norm(w))


def strip_command(text: str, words: list[str]) -> str:
    """The rest of the message after a wake phrase: 'hey jarvis what's the time' -> "what's the time"."""
    t = text.strip()
    low = _norm(t)
    for w in sorted(words, key=len, reverse=True):
        nw = _norm(w)
        if nw and low.startswith(nw):
            cut = re.match(r"^\W*" + r"\W+".join(re.escape(p) for p in nw.split()) + r"\W*", t, re.I)
            return t[cut.end():].strip() if cut else ""
    return t


# ---------- PIN and sessions ----------

_sessions: dict[str, tuple[str, float]] = {}   # token -> (whose Brain, expiry)
SESSION_SECONDS = 12 * 3600
_failures_by: dict[str, dict] = {}


def _failures() -> dict:
    return _failures_by.setdefault(config.user_key(), {"count": 0, "until": 0.0})


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


def valid_pin_format(pin: str) -> bool:
    return bool(re.fullmatch(r".{4,64}", pin or "")) and not pin.isspace()


def set_pin(new_pin: str, old_pin: str = "") -> None:
    m = get()
    if m["pin_hash"] and not check_pin(old_pin):
        raise PermissionError("The current PIN is wrong.")
    if not valid_pin_format(new_pin):
        raise ValueError("Use at least 4 characters.")
    salt = secrets.token_hex(16)
    with _lock:
        data = settings.load()
        mm = get() | {"pin_salt": salt, "pin_hash": _hash(new_pin, salt)}
        data["master"] = mm
        settings.save(data)


def remove_pin(old_pin: str) -> None:
    if not check_pin(old_pin):
        raise PermissionError("The current PIN is wrong.")
    with _lock:
        data = settings.load()
        data["master"] = get() | {"pin_salt": "", "pin_hash": "", "lock_app": False}
        settings.save(data)
    me = config.user_key()
    for tok in [k for k, (who, _) in _sessions.items() if who == me]:
        _sessions.pop(tok, None)


def check_pin(pin: str) -> bool:
    m = get()
    if not m["pin_hash"]:
        return True
    now = time.time()
    f = _failures()
    if f["until"] > now:
        raise PermissionError(f"Too many wrong tries. Wait {int(f['until'] - now) + 1} seconds.")
    ok = hmac.compare_digest(_hash(pin or "", m["pin_salt"]), m["pin_hash"])
    if ok:
        f.update(count=0, until=0.0)
    else:
        f["count"] += 1
        if f["count"] >= 5:
            f.update(count=0, until=now + 60)
        time.sleep(0.6)  # slow down guessing
    return ok


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = (config.user_key(), time.time() + SESSION_SECONDS)
    return token


def session_ok(token: str | None) -> bool:
    if not get()["pin_hash"]:
        return True
    who, exp = _sessions.get(token or "", ("", 0))
    if not exp or exp < time.time() or who != config.user_key():
        if exp and exp < time.time():
            _sessions.pop(token or "", None)
        return False
    return True


def end_session(token: str | None) -> None:
    _sessions.pop(token or "", None)
