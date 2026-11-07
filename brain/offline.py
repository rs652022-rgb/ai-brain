"""Offline AI: a free model that runs on this computer, so the Brain keeps working without internet.

Uses Ollama (free, open source, ollama.com). When there's no internet, the Brain answers with it
automatically; with internet it uses your free online models first (they're smarter).

Models (all free, open licences):
  qwen2.5:3b   about 2 GB, good English and Hindi, runs on most laptops with 8 GB memory (recommended)
  qwen2.5:7b   about 5 GB, smarter, needs 16 GB memory
  llama3.2:3b  about 2 GB, English-first
  moondream    about 2 GB, can look at screenshots (for offline computer control)
"""
import json
import shutil
import threading

import httpx

from . import config, db, llm, settings

MODELS = {
    "qwen2.5:3b": "Recommended: about 2 GB, English and Hindi, 8 GB memory is enough",
    "qwen2.5:7b": "Smarter: about 5 GB, needs 16 GB memory",
    "llama3.2:3b": "About 2 GB, English",
    "moondream": "About 2 GB, can see screenshots (offline computer control)",
}
_pull = {"model": "", "status": "", "done": 0, "total": 0, "error": ""}


class OfflineError(RuntimeError):
    pass


def status() -> dict:
    if config.MULTIUSER:
        return {"available": False}
    running = llm.ollama_available(fresh=True)
    models = []
    if running:
        try:
            base = settings.resolved("ollama")["base_url"]
            models = [m["name"] for m in httpx.get(f"{base}/api/tags", timeout=3).json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            pass
    data = settings.load()
    return {"available": True, "installed": bool(shutil.which("ollama")) or running, "running": running,
            "models": models, "chosen": settings.resolved("ollama", data)["model"],
            "in_order": "ollama" in data.get("order", []), "online": llm.online(),
            "choices": MODELS, "pull": dict(_pull)}


def use(model: str) -> dict:
    """Pick the offline model and make sure the Brain falls back to it."""
    data = settings.load()
    data.setdefault("providers", {}).setdefault("ollama", {})["model"] = model
    if "ollama" not in data.get("order", []):
        data.setdefault("order", []).append("ollama")       # last: online models first, this one when offline
    settings.save(data)
    db.audit("owner", "offline.use", model=model)
    return status()


def pull(model: str) -> dict:
    if config.MULTIUSER:
        raise OfflineError("Offline AI is for the app on your own computer.")
    if model not in MODELS and not (model and len(model) < 60 and all(c.isalnum() or c in ":.-_/" for c in model)):
        raise OfflineError("Pick a model from the list.")
    if not llm.ollama_available(fresh=True):
        raise OfflineError("Install Ollama first (free, ollama.com/download) and open it, then try again.")
    if _pull["model"] and not _pull["error"] and _pull["status"] != "success":
        raise OfflineError(f"Already downloading {_pull['model']}.")
    _pull.update(model=model, status="starting", done=0, total=0, error="")
    base = settings.resolved("ollama")["base_url"]

    def go():
        try:
            with httpx.stream("POST", f"{base}/api/pull", json={"name": model, "stream": True}, timeout=None) as r:
                for line in r.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise OfflineError(ev["error"])
                    _pull.update(status=ev.get("status", ""), done=ev.get("completed", _pull["done"]),
                                 total=ev.get("total", _pull["total"]))
            _pull["status"] = "success"
            use(model)
        except Exception as exc:
            _pull["error"] = str(exc)[:200]
    threading.Thread(target=go, daemon=True, name="ollama-pull").start()
    db.audit("owner", "offline.pull", model=model)
    return status()
