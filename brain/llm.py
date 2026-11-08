"""Model access for any provider, with automatic fallback.

- chat(): tries each provider in your order. If one has no key, is down, or hits a free-tier
  limit (HTTP 429), the next one answers. Sealed conversations only ever use Ollama.
- private_json(): tagging and fact extraction. Uses Ollama; uses a cloud model only if you
  allow it in Settings.
- embed(): search by meaning. Uses Ollama, or Gemini/OpenAI embeddings if chosen.
"""
import contextvars
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import config, settings, usage
from .providers import PRESETS

log = logging.getLogger("brain.llm")
TIMEOUT = httpx.Timeout(60, connect=10)
TEST_TIMEOUT = httpx.Timeout(25, connect=8)


class ModelUnavailable(RuntimeError):
    pass


def friendly_failure(errors: list[str]) -> str:
    """One clear sentence about what went wrong and what to do, then the raw details."""
    cloud = [e for e in errors if not e.startswith("ollama:")]
    text = " | ".join(errors)
    if cloud and all("quota" in e or "rate limit" in e for e in cloud):
        main = ("Your AI models have used up their free limit for now. Free limits reset over time (Gemini's daily). "
                "To keep talking, add a second free provider: Settings → AI models → add Groq or OpenRouter → "
                "Auto-find free models.")
    elif cloud and all("key rejected" in e or "no API key" in e for e in cloud):
        main = "The API key was rejected. Open Settings → AI models, paste the key again and press Test."
    elif cloud and all("busy" in e or "HTTP 50" in e for e in cloud):
        main = "The AI service is busy right now. Try again in a minute, or add a second provider in Settings."
    elif not cloud:
        main = "No AI model is set up. Open Settings → AI models and add a free key (Gemini or Groq)."
    else:
        main = "No AI model could answer right now."
    return f"{main}\n\nDetails: {text}"


class ProviderError(RuntimeError):
    def __init__(self, pid: str, message: str, retryable: bool = True, status: int = 0):
        super().__init__(f"{pid}: {message}")
        self.pid, self.retryable, self.status = pid, retryable, status

    @property
    def key_problem(self) -> bool:
        """The key itself is bad: trying other models of this provider won't help."""
        return self.status == 401 or "no API key" in str(self)


# ---------- Busy models are skipped for a while ----------

_cooldown: dict[str, float] = {}
_cool_lock = threading.Lock()
COOLDOWN = {429: 300, 503: 120, 500: 60, 502: 60, 504: 60, 0: 60, 404: 3600, 403: 3600}


def _cool_key(pid: str, model: str) -> str:
    return config.user_key() + '|' + f"{pid}:{model}"   # limits belong to each user's own key


def cooling(pid: str, model: str) -> bool:
    with _cool_lock:
        return _cooldown.get(_cool_key(pid, model), 0) > time.time()


def cool_down(pid: str, model: str, status: int) -> None:
    with _cool_lock:
        _cooldown[_cool_key(pid, model)] = time.time() + COOLDOWN.get(status, 60)


# ---------- One call to one provider ----------

def _error_message(r: httpx.Response) -> str:
    """Pull a readable message out of any provider's error body (dict, list, or plain text)."""
    try:
        data = r.json()
    except ValueError:
        return r.text[:200]
    for _ in range(4):  # Gemini wraps errors in a list: [{"error": {"message": ...}}]
        if isinstance(data, list) and data:
            data = data[0]
        elif isinstance(data, dict) and isinstance(data.get("error"), (dict, list)):
            data = data["error"]
        else:
            break
    if isinstance(data, dict):
        return str(data.get("message") or data.get("error") or data.get("detail") or data)[:200]
    return str(data)[:200]


def _raise_for(pid: str, r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    detail = _error_message(r)
    hint = {400: "request rejected", 401: "key rejected", 403: "key not allowed for this model",
            404: "model or URL not found (try 'Load models')",
            429: "rate limit or free quota reached", 503: "model busy"}.get(r.status_code, f"HTTP {r.status_code}")
    raise ProviderError(pid, f"{hint} ({str(detail)[:160]})", status=r.status_code)


def _post(pid: str, url: str, headers: dict, payload: dict, timeout=None) -> dict:
    try:
        r = httpx.post(url, headers=headers, json=payload, timeout=timeout or TIMEOUT)
    except httpx.HTTPError as exc:
        raise ProviderError(pid, f"cannot connect ({exc.__class__.__name__})") from exc
    _raise_for(pid, r)
    try:
        return r.json()
    except ValueError:
        raise ProviderError(pid, f"reply was not JSON ({r.text[:120]})")


# ---------- Thinking control ----------
# Newer "thinking" models (Gemini 3.x, gpt-oss, Qwen 3) reason silently before answering. That added
# several seconds to every chat reply, and Gemini sometimes leaked the reasoning as <thought> text.
# For chat we ask for the least thinking each model accepts. Values a model rejects are remembered.

_rejected: set[tuple[str, str]] = set()   # (provider:model, option) the provider refused


def reasoning_options(pid: str, model: str) -> list[dict]:
    """Extra request fields to try, best first. The last option is always 'send nothing extra'."""
    m = model.lower()
    think = settings.load().get("thinking", "fast")          # /think off|low|high (fast = the old default)
    if think in ("low", "high") and pid != "anthropic":
        opts = [{"reasoning_effort": think}]
        key = f"{pid}:{model}"
        return [o for o in opts if (key, json.dumps(o, sort_keys=True)) not in _rejected] + [{}]
    if pid == "gemini" or m.startswith("gemini"):
        opts = [{"reasoning_effort": "none"}, {"reasoning_effort": "low"}]
    elif "gpt-oss" in m:
        opts = [{"reasoning_effort": "low", "include_reasoning": False}, {"reasoning_effort": "low"}]
    elif "qwen3" in m or "qwen-3" in m:
        opts = [{"reasoning_effort": "none"}, {"reasoning_format": "hidden"}]
    elif m.startswith(("o1", "o3", "o4", "gpt-5")):
        opts = [{"reasoning_effort": "low"}]
    else:
        opts = []
    key = f"{pid}:{model}"
    return [o for o in opts if (key, json.dumps(o, sort_keys=True)) not in _rejected] + [{}]


def _reject(pid: str, model: str, opt: dict) -> None:
    _rejected.add((f"{pid}:{model}", json.dumps(opt, sort_keys=True)))


_THOUGHT = re.compile(r"<(thought|think|thinking|reasoning)>.*?</\1>\s*", re.S | re.I)
_OPEN_THOUGHT = re.compile(r"^\s*<(thought|think|thinking|reasoning)>.*", re.S | re.I)


def strip_thoughts(text: str) -> str:
    """Remove leaked model reasoning such as <thought>...</thought> or <think>...</think>."""
    text = _THOUGHT.sub("", text)
    text = _OPEN_THOUGHT.sub("", text)        # reasoning that never closed: nothing usable after it
    return text.strip()


def call(p: dict, system: str, messages: list[dict], max_tokens: int = 1200, json_mode: bool = False,
         timeout=None) -> str:
    """Send one chat request to provider p (a settings.resolved() dict)."""
    pid, kind = p["id"], p["kind"]
    has_images = any(isinstance(m.get("content"), list) for m in messages)
    messages = _with_images(kind, messages) if has_images else messages
    if p["needs_key"] and not p["api_key"]:
        raise ProviderError(pid, "no API key set", retryable=True)
    if not p["model"]:
        raise ProviderError(pid, "no model set")

    if kind == "anthropic":
        data = _post(pid, f"{p['base_url']}/messages",
                     {"x-api-key": p["api_key"], "anthropic-version": "2023-06-01"},
                     {"model": p["model"], "max_tokens": max_tokens, "system": system, "messages": messages}, timeout)
        return strip_thoughts("".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"))

    if kind == "ollama":
        payload = {"model": p["model"], "stream": False,
                   "messages": [{"role": "system", "content": system}, *messages]}
        if json_mode:
            payload["format"] = "json"
        payload["think"] = False if not json_mode else payload.get("think", False)
        try:
            out = _post(pid, f"{p['base_url']}/api/chat", {}, payload, timeout)
        except ProviderError as exc:
            if exc.status != 400:
                raise
            payload.pop("think", None)        # older Ollama versions don't know "think"
            out = _post(pid, f"{p['base_url']}/api/chat", {}, payload, timeout)
        return strip_thoughts(out["message"]["content"])

    # OpenAI-compatible: Gemini, Groq, OpenRouter, Mistral, Cerebras, GitHub, OpenAI, custom...
    headers = {"Authorization": f"Bearer {p['api_key']}"} if p["api_key"] else {}
    if pid == "openrouter":
        headers |= {"HTTP-Referer": "http://127.0.0.1", "X-Title": "AI Brain"}
    payload = {"model": p["model"], "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, *messages]}
    for opt in reasoning_options(pid, p["model"]):
        try:
            data = _post(pid, f"{p['base_url']}/chat/completions", headers, payload | opt, timeout)
            break
        except ProviderError as exc:
            if opt and exc.status == 400 and not has_images:   # model doesn't accept that option: try the next
                _reject(pid, p["model"], opt)
                continue
            raise
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(pid, f"unexpected response: {str(data)[:160]}")
    content = msg.get("content") or ""
    if isinstance(content, list):  # some providers return content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    content = strip_thoughts(content)
    if not content:
        raise ProviderError(pid, "empty reply")
    return content


def _with_images(kind: str, messages: list[dict]) -> list[dict]:
    """Messages whose content is a list of parts ({"type": "text", "text"} or
    {"type": "image", "data": base64, "mime"}) turned into each provider's own format."""
    out = []
    for m in messages:
        parts = m.get("content")
        if not isinstance(parts, list):
            out.append(m)
            continue
        texts = [x["text"] for x in parts if x.get("type") == "text"]
        imgs = [x for x in parts if x.get("type") == "image"]
        if kind == "ollama":
            out.append({"role": m["role"], "content": "\n\n".join(texts), "images": [x["data"] for x in imgs]})
        elif kind == "anthropic":
            out.append({"role": m["role"], "content": [
                *({"type": "image", "source": {"type": "base64", "media_type": x.get("mime", "image/jpeg"),
                                               "data": x["data"]}} for x in imgs),
                *({"type": "text", "text": t} for t in texts)]})
        else:
            out.append({"role": m["role"], "content": [
                *({"type": "text", "text": t} for t in texts),
                *({"type": "image_url", "image_url": {"url": f"data:{x.get('mime', 'image/jpeg')};base64,{x['data']}"}}
                  for x in imgs)]})
    return out


# ---------- Routing with fallback ----------

_net = {"at": 0.0, "ok": True}


def online() -> bool:
    """Is there internet? Checked at most every 20 seconds, in under a second. Offline, the Brain goes
    straight to the model on this computer (Ollama) instead of waiting for cloud models to time out."""
    import os
    import socket
    if os.environ.get("BRAIN_ASSUME_ONLINE") == "1":
        return True
    now = time.time()
    if now - _net["at"] < 20:
        return _net["ok"]
    ok = False
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 443), timeout=0.8).close()
            ok = True
            break
        except OSError:
            continue
    if not ok and os.environ.get("HTTPS_PROXY"):
        ok = True                                  # behind a proxy the direct check can't tell: assume online
    _net.update(at=now, ok=ok)
    return ok


def _local_provider(p: dict) -> bool:
    """Ollama, or a model server on this computer (LM Studio, llama.cpp). FreeLLMAPI runs locally but needs internet."""
    m = re.match(r"^\w+://([^/:]+)", p.get("base_url", ""))
    host = m.group(1) if m else ""
    return p["id"] == "ollama" or (p["id"] != "freellmapi" and host in ("127.0.0.1", "localhost"))


def _chain(only: list[str] | None = None, prefer: tuple[str, str] | None = None) -> list[dict]:
    chain = _chain_all(only, prefer)
    if chain and not online():
        local = [p for p in chain if _local_provider(p)]
        if not local and config.allow_private() and "ollama" not in [p["id"] for p in chain]:
            local = [settings.resolved("ollama")]
        return local
    return chain


def _chain_all(only: list[str] | None = None, prefer: tuple[str, str] | None = None) -> list[dict]:
    data = settings.load()
    order = only or data.get("order") or ["ollama"]
    if prefer and prefer[0] in PRESETS and not only:     # an agent's own model goes first, the usual order backs it up
        order = [prefer[0]] + [p for p in order if p != prefer[0]]
    chain = [settings.resolved(pid, data) for pid in order if pid in PRESETS]
    if prefer and chain and chain[0]["id"] == prefer[0] and prefer[1]:
        chain[0] = chain[0] | {"model": prefer[1], "fallbacks": []}
    return [p for p in chain if settings.url_allowed(p["base_url"])]


def models_for(p: dict) -> list[str]:
    """The main model first, then backup models found by auto-discovery."""
    return list(dict.fromkeys([m for m in [p["model"], *p.get("fallbacks", [])] if m]))


def chat(system: str, messages: list[dict], private: bool = False, json_mode: bool = False,
         max_tokens: int = 1200, prefer: tuple[str, str] | None = None) -> tuple[str, str]:
    """Answer with the first provider and model that works. Returns (reply, 'provider:model').

    Within each provider it tries the main model, then its backup models. A model that is busy (503)
    or out of quota (429) is skipped for a few minutes so later messages don't wait on it.
    """
    try:
        usage.check_limits()
    except usage.OverBudget as exc:
        raise ModelUnavailable(str(exc))
    chain = _chain(["ollama"]) if private else _chain(prefer=prefer)
    errors = []
    for p in chain:
        models = models_for(p)
        fresh = [m for m in models if not cooling(p["id"], m)]
        for model in fresh or models[:1]:  # if every model is cooling down, still try the main one
            t0 = time.time()
            try:
                reply = call(p | {"model": model}, system, messages, json_mode=json_mode, max_tokens=max_tokens)
                if errors:
                    log.info("Answered by %s:%s after: %s", p["id"], model, "; ".join(errors))
                usage.record(p["id"], model, True, time.time() - t0, usage.estimate(system, messages, reply))
                return reply, f"{p['id']}:{model}"
            except ProviderError as exc:
                usage.record(p["id"], model, False)
                errors.append(f"{exc} [{model}]" if len(models) > 1 else str(exc))
                if exc.key_problem:
                    break
                cool_down(p["id"], model, exc.status)
            except Exception as exc:
                log.exception("Provider %s failed", p["id"])
                errors.append(f"{p['id']}: unexpected error {exc.__class__.__name__}: {exc}")
                break
    if private:
        raise ModelUnavailable("Sealed memories only go to the local model, and Ollama is not running. " + "; ".join(errors))
    if not errors and not online():
        raise ModelUnavailable("You're offline, and there's no AI model on this computer. Settings → Offline AI sets one up (free).")
    if not errors:
        raise ModelUnavailable("No AI model is set up. Open Settings → AI models and add a free key (Gemini or Groq).")
    raise ModelUnavailable(friendly_failure(errors))


# ---------- Streaming (words appear and are spoken as they are written) ----------

class ThoughtFilter:
    """Removes <thought>/<think> blocks from a stream of text pieces, even when tags are split."""
    TAGS = ("thought", "think", "thinking", "reasoning")

    def __init__(self):
        self.buf, self.inside, self.started = "", None, False

    def feed(self, piece: str) -> str:
        self.buf += piece
        out = ""
        while self.buf:
            if self.inside:
                end = re.search(rf"</{self.inside}>", self.buf, re.I)
                if not end:
                    self.buf = self.buf[-(len(self.inside) + 3):]   # keep a possible partial closing tag
                    return out
                self.buf, self.inside = self.buf[end.end():].lstrip(), None
                continue
            start = re.search(r"<(thought|think|thinking|reasoning)>", self.buf, re.I)
            if start:
                out += self.buf[:start.start()]
                self.buf, self.inside = self.buf[start.end():], start.group(1).lower()
                continue
            cut = self.buf.rfind("<")                               # might be the start of a tag
            if cut != -1 and len(self.buf) - cut < 12:
                out, self.buf = out + self.buf[:cut], self.buf[cut:]
            else:
                out, self.buf = out + self.buf, ""
            break
        return out

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


def _stream_openai(p: dict, system: str, messages: list[dict], max_tokens: int):
    pid = p["id"]
    headers = {"Authorization": f"Bearer {p['api_key']}"} if p["api_key"] else {}
    if pid == "openrouter":
        headers |= {"HTTP-Referer": "http://127.0.0.1", "X-Title": "AI Brain"}
    payload = {"model": p["model"], "max_tokens": max_tokens, "stream": True,
               "messages": [{"role": "system", "content": system}, *messages]}
    for opt in reasoning_options(pid, p["model"]):
        try:
            with httpx.stream("POST", f"{p['base_url']}/chat/completions", headers=headers, json=payload | opt,
                              timeout=TIMEOUT) as r:
                if r.status_code >= 400:
                    r.read()
                    _raise_for(pid, r)
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        delta = json.loads(data)["choices"][0].get("delta") or {}
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    text = delta.get("content") or ""
                    if isinstance(text, list):
                        text = "".join(x.get("text", "") for x in text if isinstance(x, dict))
                    if text:
                        yield text
            return
        except ProviderError as exc:
            if opt and exc.status == 400:
                _reject(pid, p["model"], opt)
                continue
            raise
        except httpx.HTTPError as exc:
            raise ProviderError(pid, f"cannot connect ({exc.__class__.__name__})") from exc


def chat_stream(system: str, messages: list[dict], private: bool = False, max_tokens: int = 1200,
                prefer: tuple[str, str] | None = None):
    """Like chat(), but yields ('text', piece) as the reply is written, then ('done', 'provider:model').

    Failover to the next model/provider happens only before the first words arrive."""
    try:
        usage.check_limits()
    except usage.OverBudget as exc:
        raise ModelUnavailable(str(exc))
    chain = _chain(["ollama"]) if private else _chain(prefer=prefer)
    errors = []
    for p in chain:
        models = models_for(p)
        fresh = [m for m in models if not cooling(p["id"], m)]
        for model in fresh or models[:1]:
            q = p | {"model": model}
            sent = False
            streamed_chars = 0
            filt = ThoughtFilter()
            try:
                if p["kind"] == "openai":
                    for piece in _stream_openai(q, system, messages, max_tokens):
                        clean = filt.feed(piece)
                        streamed_chars += len(piece)
                        if clean:
                            sent = True
                            yield "text", clean
                    tail = filt.flush()
                    if tail:
                        sent = True
                        yield "text", tail
                    if not sent:
                        raise ProviderError(p["id"], "empty reply")
                else:   # Claude and Ollama: one piece (still fast, just not word by word)
                    yield "text", call(q, system, messages, max_tokens=max_tokens)
                usage.record(p["id"], model, True, 0.0, usage.estimate(system, messages) + streamed_chars // 4)
                yield "done", f"{p['id']}:{model}"
                return
            except ProviderError as exc:
                usage.record(p["id"], model, False)
                if sent:   # words already shown: stop here rather than mixing two answers
                    yield "done", f"{p['id']}:{model}"
                    return
                errors.append(f"{exc} [{model}]" if len(models) > 1 else str(exc))
                if exc.key_problem:
                    break
                cool_down(p["id"], model, exc.status)
            except Exception as exc:
                if sent:
                    yield "done", f"{p['id']}:{model}"
                    return
                log.exception("Provider %s failed", p["id"])
                errors.append(f"{p['id']}: unexpected error {exc.__class__.__name__}: {exc}")
                break
    if private:
        raise ModelUnavailable("Sealed memories only go to the local model, and Ollama is not running. " + "; ".join(errors))
    if not errors and not online():
        raise ModelUnavailable("You're offline, and there's no AI model on this computer. Settings → Offline AI sets one up (free).")
    if not errors:
        raise ModelUnavailable("No AI model is set up. Open Settings → AI models and add a free key (Gemini or Groq).")
    raise ModelUnavailable(friendly_failure(errors))


def test_provider(pid: str) -> dict:
    p = settings.resolved(pid)
    try:
        reply = call(p, "You are a connection test.", [{"role": "user", "content": "Reply with just: OK"}], max_tokens=200,
                     timeout=TEST_TIMEOUT)
        return {"ok": True, "model": p["model"], "reply": reply[:80]}
    except ProviderError as exc:
        return {"ok": False, "model": p["model"], "error": str(exc)}
    except Exception as exc:  # never crash the Test button
        log.exception("Provider test failed")
        return {"ok": False, "model": p["model"], "error": f"{pid}: unexpected error {exc.__class__.__name__}: {exc}"}


def list_models(pid: str) -> list[str]:
    p = settings.resolved(pid)
    try:
        if p["kind"] == "ollama":
            r = httpx.get(f"{p['base_url']}/api/tags", timeout=10)
            _raise_for(pid, r)
            return sorted(m["name"] for m in r.json().get("models", []))
        if p["kind"] == "anthropic":
            r = httpx.get(f"{p['base_url']}/models", timeout=15,
                          headers={"x-api-key": p["api_key"], "anthropic-version": "2023-06-01"})
        else:
            r = httpx.get(f"{p['base_url']}/models", timeout=15,
                          headers={"Authorization": f"Bearer {p['api_key']}"} if p["api_key"] else {})
        _raise_for(pid, r)
        data = r.json()
        items = data.get("data", data) if isinstance(data, dict) else data
        ids = []
        for m in items if isinstance(items, list) else []:
            mid = m.get("id") or m.get("name") if isinstance(m, dict) else str(m)
            if mid:
                ids.append(mid.removeprefix("models/"))
        if pid == "openrouter":  # show the free ones first
            ids.sort(key=lambda x: (not (x.endswith(":free") or x == "openrouter/free"), x))
        else:
            ids.sort()
        return ids
    except httpx.HTTPError as exc:
        raise ProviderError(pid, f"cannot connect ({exc.__class__.__name__})")


_ollama_state = {"at": 0.0, "ok": False, "url": ""}


def ollama_available(fresh: bool = False) -> bool:
    """Is Ollama running? Cached for 30 seconds: this used to be checked 5 times per message."""
    p = settings.resolved("ollama")
    if not config.allow_private():
        return False
    now = time.time()
    if not fresh and _ollama_state["url"] == p["base_url"] and now - _ollama_state["at"] < 30:
        return _ollama_state["ok"]
    try:
        httpx.get(f"{p['base_url']}/api/tags", timeout=httpx.Timeout(1.5, connect=0.7)).raise_for_status()
        ok = True
    except httpx.HTTPError:
        ok = False
    _ollama_state.update(at=now, ok=ok, url=p["base_url"])
    return ok


def status() -> dict:
    data = settings.load()
    chain = []
    for pid in data.get("order", []):
        p = settings.resolved(pid, data)
        ready = ollama_available() if p["kind"] == "ollama" else bool(p["api_key"] or not p["needs_key"])
        chain.append({"id": pid, "label": p["label"], "model": p["model"], "ready": ready})
    return {"chain": chain, "ollama": ollama_available(), "embeddings": _embed_provider_id(data)}


# ---------- Private JSON tasks ----------

def _parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)  # cloud models sometimes wrap JSON in prose or code fences
        return json.loads(m.group(0)) if m else None


def light() -> tuple[str, str] | None:
    """Housekeeping (chat summaries, web routing, dreaming, skill reflection, search planning) goes to
    FreeLLMAPI's fast free pool, so the best models and your limits are kept for real answers.
    Settings key "light_model": "auto:fast" (default), another FreeLLMAPI profile, or "" to switch it off."""
    data = settings.load()
    model = data.get("light_model", "auto:fast")
    if model and "freellmapi" in data.get("order", []) and settings.resolved("freellmapi", data).get("api_key"):
        return ("freellmapi", model)
    return None


def voice_model() -> tuple[str, str] | None:
    """Spoken commands (activation word, phone, Siri): FreeLLMAPI's fast free models answer quickly and save
    tokens. Settings key "voice_model": "auto:fast" (default), "auto", "auto:smart", or "" to use the usual order."""
    data = settings.load()
    model = data.get("voice_model", "auto:fast")
    if model and "freellmapi" in data.get("order", []) and settings.resolved("freellmapi", data).get("api_key"):
        return ("freellmapi", model)
    return None


def private_json(system: str, prompt: str) -> dict | None:
    """Tagging and fact extraction. Local first; cloud only if allowed in Settings. None if unavailable."""
    msgs = [{"role": "user", "content": prompt}]
    try:
        if ollama_available():
            return _parse_json(call(settings.resolved("ollama"), system, msgs, json_mode=True))
        if settings.load().get("cloud_private_tasks"):
            reply, _ = chat(system + " Output only the JSON object, no other text.", msgs, prefer=light())
            return _parse_json(reply)
    except (ProviderError, ModelUnavailable, json.JSONDecodeError) as exc:
        log.info("Background task skipped: %s", exc)
    return None


# ---------- Embeddings ----------

def _embed_provider_id(data: dict | None = None) -> str:
    data = data or settings.load()
    choice = data.get("embeddings", "auto")
    if choice != "auto":
        return choice
    if ollama_available():
        return "ollama"
    for pid in ("gemini", "openai"):
        if data.get("providers", {}).get(pid, {}).get("api_key"):
            return pid
    if settings.resolved("freellmapi", data)["api_key"]:      # FreeLLMAPI pools free embedding providers too
        return "freellmapi"
    return "none"


_embed_cache: dict[tuple, list[float]] = {}


def embed(text: str) -> list[float] | None:
    """Return an embedding, or None (search then falls back to keywords).

    Recent results are cached, so memory search and knowledge search share one call per message."""
    key = (embedding_label(), text)
    if key in _embed_cache:
        return _embed_cache[key]
    vec = _embed_uncached(text)
    if vec is not None:
        if len(_embed_cache) > 256:
            _embed_cache.clear()
        _embed_cache[key] = vec
    return vec


def _embed_uncached(text: str) -> list[float] | None:
    pid = _embed_provider_id()
    if pid == "none":
        return None
    p = settings.resolved(pid)
    if not settings.url_allowed(p["base_url"]):
        return None
    try:
        if p["kind"] == "ollama":
            data = _post(pid, f"{p['base_url']}/api/embed", {}, {"model": p["embed_model"] or "nomic-embed-text",
                                                                  "input": text})
            vecs = data.get("embeddings") or []
            return vecs[0] if vecs else None
        if not p["embed_model"]:
            return None
        data = _post(pid, f"{p['base_url']}/embeddings", {"Authorization": f"Bearer {p['api_key']}"},
                     {"model": p["embed_model"], "input": text})
        return data["data"][0]["embedding"]
    except (ProviderError, KeyError, IndexError) as exc:
        log.info("Embedding skipped, using keyword search: %s", exc)
        return None


def embedding_label() -> str:
    pid = _embed_provider_id()
    return "none" if pid == "none" else f"{pid}:{settings.resolved(pid)['embed_model']}"


# ---------- Auto-discovery of free models ----------

# Models that are not general chat models (speech, images, embeddings, safety filters...).
NOT_CHAT = re.compile(r"embed|tts|whisper|transcri|audio|speech|image|imagen|veo|video|vision-only|guard|safeguard|"
                      r"moderat|rerank|robotics|aqa|live|realtime|native-audio|computer-use|search-preview|"
                      r"distil|dall-e|sora|ocr|codestral-embed|bge|e5-|clip", re.I)
PAID_PROVIDERS = {"anthropic", "openai", "deepseek"}


def _is_free(pid: str, model: str) -> bool:
    if pid == "openrouter":
        return model.endswith(":free") or model == "openrouter/free"
    if pid in PAID_PROVIDERS:
        return False
    return True  # free-tier providers, Ollama, FreeLLMAPI and custom gateways: every model is usable


def _rank(model: str) -> float:
    """Rough quality-and-reliability score so stronger, steadier models are tried first."""
    m = model.lower()
    score = 0.0
    if m in ("auto", "openrouter/free"):
        score += 50                      # gateway routers pick a working free model themselves
    size = re.search(r"(\d+(?:\.\d+)?)b\b", m)
    if size:
        score += min(float(size.group(1)), 400) ** 0.5   # bigger models answer better, with diminishing returns
    for word, pts in (("gpt-oss-120b", 8), ("flash", 6), ("70b", 3), ("qwen", 3), ("llama-3.3", 3),
                      ("mistral-small", 4), ("mistral-medium", 5), ("deepseek", 4), ("instruct", 1), ("chat", 1)):
        if word in m:
            score += pts
    for word, pts in (("lite", -2), ("mini", -1), ("nano", -3), ("tiny", -4), ("1b", -4), ("3b", -2),
                      ("preview", -2), ("exp", -2), ("thinking", -1), ("pro", -1)):
        if word in m:
            score += pts
    version = re.search(r"(\d+)\.(\d+)", m)   # newer versions first (e.g. gemini 3.8 over 2.5)
    if version:
        score += float(f"{version.group(1)}.{version.group(2)}") * 0.5
    return score


def _try_model(p: dict, model: str) -> tuple[str, bool, str, float]:
    start = time.time()
    try:
        call(p | {"model": model}, "You are a connection test.", [{"role": "user", "content": "Reply with just: OK"}],
             max_tokens=200, timeout=TEST_TIMEOUT)
        return model, True, "", time.time() - start
    except ProviderError as exc:
        return model, False, str(exc), time.time() - start
    except Exception as exc:
        return model, False, f"{exc.__class__.__name__}: {exc}", time.time() - start


def discover(pid: str, max_tests: int = 8, keep: int = 4, keep_user_model: bool = False) -> dict:
    """Find the free chat models this key can use, test them, and save the working ones in order.

    The fastest good model becomes the main model and the rest become automatic backups.
    keep_user_model (the automatic background check): a model the user chose stays the main model,
    and only the backups are filled in. The provider is marked so the check runs once, not every 10 minutes.
    """
    data = settings.load()
    p = settings.resolved(pid, data)
    if p["needs_key"] and not p["api_key"]:
        return {"provider": pid, "ok": False, "error": "no API key set"}
    try:
        all_models = list_models(pid)
    except ProviderError as exc:
        return {"provider": pid, "ok": False, "error": str(exc)}

    candidates = [m for m in all_models if _is_free(pid, m) and not NOT_CHAT.search(m)]
    if pid in ("freellmapi",) and "auto" not in candidates:
        candidates.insert(0, "auto")
    candidates.sort(key=_rank, reverse=True)
    candidates = candidates[:max_tests]
    if not candidates:
        return {"provider": pid, "ok": False, "error": "no free chat models found for this key",
                "listed": len(all_models)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        ctx = contextvars.copy_context()          # pool threads work for the same user
        results = list(pool.map(lambda m: ctx.copy().run(_try_model, p, m), candidates))
    working = [(m, secs) for m, ok, _err, secs in results if ok]
    failed = {m: err for m, ok, err, _secs in results if not ok}
    if not working:
        return {"provider": pid, "ok": False, "error": "none of the free models answered right now",
                "tested": candidates, "failed": failed}

    # Keep the ranking order, but prefer models that answered within 10 seconds.
    order = {m: i for i, m in enumerate(candidates)}
    working.sort(key=lambda w: (w[1] > 10, order[w[0]]))
    chosen = [m for m, _ in working[:keep]]

    with settings.edit_lock:
        data = settings.load()  # reload in case settings changed while testing
        old = data.get("providers", {}).get(pid, {})
        if keep_user_model and old.get("model"):
            chosen = [old["model"]] + [m for m in chosen if m != old["model"]][:keep - 1]
        cfg = old | {"model": chosen[0], "fallbacks": chosen[1:], "auto_discovered": True}
        settings.save(data | {"providers": data.get("providers", {}) | {pid: cfg}})
    return {"provider": pid, "ok": True, "model": chosen[0], "fallbacks": chosen[1:],
            "tested": candidates, "failed": failed, "listed": len(all_models)}


def discover_all() -> list[dict]:
    """Run discovery for every provider in your list that can be used (has a key, or is local)."""
    data = settings.load()
    out = []
    for pid in data.get("order", []):
        p = settings.resolved(pid, data)
        if pid in PAID_PROVIDERS:
            out.append({"provider": pid, "ok": None, "skipped": "paid provider: kept as you set it"})
        elif p["kind"] == "ollama" and not ollama_available():
            out.append({"provider": pid, "ok": None, "skipped": "Ollama is not running"})
        elif p["needs_key"] and not p["api_key"]:
            out.append({"provider": pid, "ok": None, "skipped": "no API key"})
        else:
            out.append(discover(pid))
    return out


from . import tracing as _tracing  # noqa: E402
chat = _tracing.wrap("llm.chat", chat, lambda a, kw: {"private": bool(kw.get("private")), "messages": len(a[1]) if len(a) > 1 else 0})
