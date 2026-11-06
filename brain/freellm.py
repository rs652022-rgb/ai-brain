"""FreeLLMAPI: one free key that pools many free AI providers (github.com/tashfeenahmed/freellmapi).

The Brain uses it for chat (model "auto", or "auto:fast", "auto:smart", "fusion"), seeing
screenshots, search-by-meaning, web search, image generation, speech and transcription.

On a shared server the admin can run one FreeLLMAPI for everyone with BRAIN_FREELLMAPI_URL and
BRAIN_FREELLMAPI_KEY; users who add their own key use theirs instead.
"""
import base64
import os

import httpx

from . import settings

PROFILES = {
    "auto": "Best available (recommended)",
    "auto:fast": "Fastest answers",
    "auto:smart": "Smartest available",
    "fusion": "Several models answer, one combines them (slow, best quality)",
}


class FreeLLMError(RuntimeError):
    pass


def shared() -> tuple[str, str]:
    """The admin's FreeLLMAPI for all users (server version), if set."""
    return (os.environ.get("BRAIN_FREELLMAPI_URL", "").rstrip("/"), os.environ.get("BRAIN_FREELLMAPI_KEY", ""))


def conf() -> dict:
    p = settings.resolved("freellmapi")
    return {"base_url": p["base_url"], "api_key": p["api_key"], "model": p["model"] or "auto"}


def configured() -> bool:
    c = conf()
    return bool(c["api_key"]) and settings.url_allowed(c["base_url"])


def _headers(c: dict) -> dict:
    return {"Authorization": f"Bearer {c['api_key']}"}


def _check(r: httpx.Response, what: str) -> None:
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            msg = r.text
        raise FreeLLMError(f"FreeLLMAPI {what} failed (HTTP {r.status_code}): {str(msg)[:200]}")


def _ready() -> dict:
    if not configured():
        raise FreeLLMError("FreeLLMAPI isn't set up. Add it in Settings → AI models (Quick setup).")
    return conf()


def models(base_url: str | None = None, api_key: str | None = None) -> list[str]:
    c = conf() if base_url is None else {"base_url": base_url.rstrip("/"), "api_key": api_key or ""}
    try:
        r = httpx.get(f"{c['base_url']}/models", headers=_headers(c), params={"execution_status": "ready"}, timeout=15)
    except httpx.HTTPError as exc:
        raise FreeLLMError(f"Can't reach FreeLLMAPI at {c['base_url']}: {exc.__class__.__name__}. Is it running?") from exc
    _check(r, "model list")
    return [m.get("id") for m in r.json().get("data", []) if m.get("id")]


def search(query: str, n: int = 6) -> list[dict]:
    c = _ready()
    r = httpx.post(f"{c['base_url']}/search", headers=_headers(c), json={"query": query, "max_results": n}, timeout=40)
    _check(r, "search")
    return [{"title": x.get("title", ""), "url": x.get("url", ""),
             "snippet": (x.get("content") or x.get("snippet") or x.get("description") or "")[:500]}
            for x in r.json().get("results", [])[:n]]


def image(prompt: str, size: str = "1024x1024") -> tuple[bytes, str]:
    """Generate an image. Returns (bytes, mime)."""
    c = _ready()
    r = httpx.post(f"{c['base_url']}/images/generations", headers=_headers(c),
                   json={"model": "auto", "prompt": prompt[:2000], "n": 1, "size": size, "response_format": "b64_json"},
                   timeout=180)
    _check(r, "image generation")
    item = (r.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), "image/png"
    if item.get("url"):
        from . import web
        web.check_url(item["url"])
        img = httpx.get(item["url"], timeout=60, follow_redirects=True)
        img.raise_for_status()
        return img.content, img.headers.get("content-type", "image/png").split(";")[0]
    raise FreeLLMError("FreeLLMAPI returned no image.")


def speech(text: str, voice: str = "alloy") -> tuple[bytes, str]:
    c = _ready()
    r = httpx.post(f"{c['base_url']}/audio/speech", headers=_headers(c),
                   json={"model": "auto", "input": text[:4000], "voice": voice or "alloy", "response_format": "mp3"},
                   timeout=90)
    _check(r, "speech")
    return r.content, r.headers.get("content-type", "audio/mpeg").split(";")[0]


def transcribe(audio: bytes, filename: str = "speech.webm", language: str | None = None) -> str:
    c = _ready()
    data = {"model": "auto"} | ({"language": language} if language else {})
    r = httpx.post(f"{c['base_url']}/audio/transcriptions", headers=_headers(c), data=data,
                   files={"file": (filename, audio)}, timeout=90)
    _check(r, "transcription")
    return (r.json().get("text") or "").strip()


def quick_setup(base_url: str, api_key: str) -> dict:
    """Check the gateway works, then make it the first engine for chat, search, and search-by-meaning."""
    base_url = (base_url or "http://127.0.0.1:3001/v1").strip().rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    if not settings.url_allowed(base_url):
        raise FreeLLMError("On a shared server, FreeLLMAPI must be at a public address (or set up by the admin).")
    found = models(base_url, api_key)
    data = settings.load()
    prov = data.setdefault("providers", {})
    prov["freellmapi"] = prov.get("freellmapi", {}) | {"base_url": base_url, "api_key": api_key.strip(), "model": "auto",
                                                        "embed_model": "auto"}
    data["order"] = ["freellmapi"] + [p for p in data.get("order", []) if p != "freellmapi"]
    order = data.get("search_order") or ["duckduckgo"]
    data["search_order"] = ["freellmapi"] + [e for e in order if e != "freellmapi"]
    settings.save(data)
    return {"ok": True, "models": found[:80], "profiles": PROFILES}
