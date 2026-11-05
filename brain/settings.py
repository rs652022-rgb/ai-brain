"""Provider settings, stored in data/settings.json and edited from the Settings tab.

Values in .env are used only to seed the file the first time (for example ANTHROPIC_API_KEY
or GEMINI_API_KEY), so older setups keep working.
"""
import json
import os
import threading

from . import config
from .providers import PRESETS

_lock = threading.Lock()
PATH = config.DATA_DIR / "settings.json"

ENV_KEYS = {pid: f"{pid.upper()}_API_KEY" for pid in PRESETS}


def _default() -> dict:
    providers = {}
    for pid in PRESETS:
        key = os.environ.get(ENV_KEYS[pid], "")
        if key:
            providers[pid] = {"api_key": key}
    if os.environ.get("CLAUDE_MODEL") and "anthropic" in providers:
        providers["anthropic"]["model"] = os.environ["CLAUDE_MODEL"]
    order = [p for p in ("anthropic", "gemini", "groq", "openrouter") if p in providers] + ["ollama"]
    providers.setdefault("ollama", {"base_url": config.OLLAMA_URL, "model": config.OLLAMA_CHAT_MODEL})
    return {
        "order": order,                 # tried in this order; the next one is used when one fails or hits a limit
        "providers": providers,         # per provider: api_key, model, base_url (all optional)
        "embeddings": "auto",           # auto | ollama | gemini | openai | none
        "cloud_private_tasks": False,   # allow tagging/fact extraction on a cloud model when Ollama is off
        "web_mode": "ask",              # off | ask (only when you ask) | auto (Brain decides; not recommended)
        "suggest_facts": True,          # suggest facts from your messages for your approval
        "search_order": ["duckduckgo"], # web search engines, tried in order
        "search": {},                   # per engine: api_key, base_url
        "voice": {},                    # see voice.DEFAULTS
    }


def path():
    return config.DATA_DIR / "settings.json"


def load() -> dict:
    p = path()
    with _lock:
        if not p.exists():
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = _default()
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return data
        data = json.loads(p.read_text(encoding="utf-8"))
    base = _default()
    base.update(data)
    return base


def save(data: dict) -> dict:
    clean = {
        "order": [p for p in data.get("order", []) if p in PRESETS],
        "providers": {},
        "embeddings": data.get("embeddings", "auto"),
        "cloud_private_tasks": bool(data.get("cloud_private_tasks", False)),
        "web_mode": data.get("web_mode", "ask") if data.get("web_mode") in ("off", "ask", "auto") else "ask",
        "suggest_facts": bool(data.get("suggest_facts", True)),
        "search_order": [e for e in data.get("search_order", ["duckduckgo"]) if e in ("duckduckgo", "brave", "tavily", "searxng")],
        "search": {},
        "voice": {},
    }
    from .voice import DEFAULTS as VOICE_DEFAULTS
    for k, v in (data.get("voice") or {}).items():
        if k in VOICE_DEFAULTS and isinstance(v, type(VOICE_DEFAULTS[k])) or (k == "rate" and isinstance(v, (int, float))):
            clean["voice"][k] = v
    for eid, cfg in (data.get("search") or {}).items():
        if isinstance(cfg, dict):
            clean["search"][eid] = {k: str(v).strip() for k, v in cfg.items() if k in ("api_key", "base_url") and v}
    for pid, cfg in (data.get("providers") or {}).items():
        if pid not in PRESETS or not isinstance(cfg, dict):
            continue
        clean["providers"][pid] = {k: str(v).strip() for k, v in cfg.items()
                                   if k in ("api_key", "model", "base_url", "embed_model") and v}
        backups = cfg.get("fallbacks")
        if isinstance(backups, list):
            clean["providers"][pid]["fallbacks"] = [str(m).strip() for m in backups if str(m).strip()][:10]
    # Master Control and Device control are saved through their own modules; other saves (the Settings
    # tab) must keep them as they are.
    try:
        on_disk = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        on_disk = {}
    for key in ("master", "control"):
        clean[key] = data[key] if isinstance(data.get(key), dict) else on_disk.get(key, {})
    with _lock:
        path().write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return clean


def resolved(pid: str, data: dict | None = None) -> dict:
    """A provider's full settings: preset defaults overlaid with the user's values."""
    data = data or load()
    p = PRESETS.get(pid, PRESETS["custom"])
    mine = data.get("providers", {}).get(pid, {})
    return {
        "id": pid,
        "label": p["label"],
        "kind": p["kind"],
        # 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and a closed port
        # takes about 2 seconds to fail, which made every message slow when Ollama was off.
        "base_url": (mine.get("base_url") or p["base_url"]).rstrip("/").replace("://localhost", "://127.0.0.1"),
        "model": mine.get("model") or p["model"],
        "embed_model": mine.get("embed_model") or p.get("embed_model", ""),
        "api_key": mine.get("api_key", ""),
        "needs_key": p.get("needs_key", True),
        "fallbacks": mine.get("fallbacks", []),
    }


def masked(data: dict | None = None) -> dict:
    """Settings safe to send to the browser: keys are shown only as their last 4 characters."""
    data = json.loads(json.dumps(data or load()))
    data.pop("master", None)   # PIN hash and Master Control are served separately
    data.pop("control", None)
    for cfg in list(data["providers"].values()) + list(data.get("search", {}).values()):
        k = cfg.get("api_key", "")
        if k:
            cfg["api_key"] = "••••" + k[-4:]
            cfg["has_key"] = True
    return data
