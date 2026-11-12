"""Device control settings: what the Brain may do on your computer and phone.

Nothing works until the owner switches Device control on AND allows a device (once).
Each kind of action then has its own level:
  off   never
  ask   the Brain stops and waits for you to press Allow (or say no)
  auto  allowed without asking
Calls, messages, shutting down and deleting always ask, whatever the levels say.
Stored in data/settings.json under "control".
"""
import threading
from pathlib import Path

from . import config, settings

CATEGORIES = {
    #  key           label                              what it covers                                        default
    "observe":     ("See the screen",                 "Screenshots, open windows, the clipboard",              "ask"),
    "apps":        ("Open apps and websites",         "Start apps, open links, switch and close windows",      "ask"),
    "input":       ("Mouse and keyboard / touch",     "Click, type, press keys, scroll; tap and swipe on the phone", "ask"),
    "files_read":  ("Read files",                     "List and read files in the folders below",              "auto"),
    "files_write": ("Change files",                   "Create, edit and delete files in the folders below",    "ask"),
    "commands":    ("Run commands",                   "PowerShell / terminal commands",                        "ask"),
    "power":       ("Lock, sleep, shut down",         "Lock the screen, sleep, restart, shut down",            "ask"),
    "messages":    ("Calls and messages",             "Phone calls, SMS and WhatsApp messages",                "ask"),
}
LEVELS = ("off", "ask", "auto")
ALWAYS_ASK = {"messages", "power"}          # these can never be set to "auto"


def _home_folders() -> list[str]:
    if config.MULTIUSER:          # paths on the user's own computer; the companion expands "~"
        return ["~/Desktop", "~/Documents", "~/Downloads"]
    home = Path.home()
    return [str(home / d) for d in ("Desktop", "Documents", "Downloads")]


DEFAULTS = {
    "enabled": False,                       # the main switch
    "devices": {"pc": False, "phone": False},   # "allow once": stays allowed until you switch it off
    "levels": {k: v[2] for k, v in CATEGORIES.items()},
    "folders": None,                        # None = Desktop, Documents and Downloads
    "max_steps": 30,
    "adb_path": "",
    "phone_serial": "",
}

_lock = threading.Lock()


def get() -> dict:
    saved = settings.load().get("control") or {}
    out = DEFAULTS | {k: v for k, v in saved.items() if k in DEFAULTS}
    out["devices"] = DEFAULTS["devices"] | (saved.get("devices") or {})
    out["levels"] = DEFAULTS["levels"] | {k: v for k, v in (saved.get("levels") or {}).items()
                                          if k in CATEGORIES and v in LEVELS}
    for k in ALWAYS_ASK:
        if out["levels"][k] == "auto":
            out["levels"][k] = "ask"
    if out["folders"] is None:
        out["folders"] = _home_folders()
    return out


def update(changes: dict) -> dict:
    with _lock:
        data = settings.load()
        c = get()
        if "enabled" in changes:
            c["enabled"] = bool(changes["enabled"])
        if isinstance(changes.get("devices"), dict):
            c["devices"] = c["devices"] | {k: bool(v) for k, v in changes["devices"].items() if k in DEFAULTS["devices"]}
        if isinstance(changes.get("levels"), dict):
            for k, v in changes["levels"].items():
                if k in CATEGORIES and v in LEVELS:
                    c["levels"][k] = "ask" if (k in ALWAYS_ASK and v == "auto") else v
        if "folders" in changes:
            items = changes["folders"]
            if isinstance(items, str):
                items = items.splitlines()
            c["folders"] = [(str(f).strip() if config.MULTIUSER else str(Path(str(f).strip()).expanduser()))
                            for f in items if str(f).strip()][:20]
        if "max_steps" in changes:
            try:
                c["max_steps"] = max(3, min(80, int(changes["max_steps"])))
            except (TypeError, ValueError):
                pass
        for key in ("adb_path", "phone_serial"):
            if key in changes:
                c[key] = str(changes[key] or "").strip()[:300]
        data["control"] = c
        settings.save(data)
    return get()


def ready(device: str | None = None) -> bool:
    c = get()
    if not c["enabled"]:
        return False
    return any(c["devices"].values()) if device is None else bool(c["devices"].get(device))


def level(category: str) -> str:
    c = get()
    lv = c["levels"].get(category, "ask")
    return "ask" if (category in ALWAYS_ASK and lv == "auto") else lv


# The companion app gets the allowed folders from the owner's Brain with each request.
FOLDERS_OVERRIDE: list[str] | None = None


def folders() -> list[str]:
    return FOLDERS_OVERRIDE if FOLDERS_OVERRIDE is not None else get()["folders"]


def folder_allowed(path: str | Path) -> Path:
    """The resolved path if it is inside one of the allowed folders; raises PermissionError otherwise."""
    p = Path(str(path)).expanduser()
    try:
        p = p.resolve()
    except OSError as exc:
        raise PermissionError(f"Can't use that path: {exc}") from exc
    for f in folders():
        try:
            root = Path(f).expanduser().resolve()
        except OSError:
            continue
        if p == root or p.is_relative_to(root):
            return p
    raise PermissionError(f"{p} is outside the folders you allowed (Control → Folders).")
