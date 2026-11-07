"""Local web server. Binds to 127.0.0.1 only, so nothing outside your laptop can reach it."""
import asyncio
import contextvars
import uuid
import hmac
import os
import logging
import re
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import base64
import json
import threading
import time

from fastapi import Response

from . import (accounts, agent, config, control, core, db, devices, hub, identity, importer, interview, knowledge,
               llm, master, memory, settings, voice, web)
from .providers import PRESETS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

def _start_scheduler():
    """Runs research topics the owner scheduled. Checks every 10 minutes while the app is open."""
    def discover_new_keys():
        """The first time a free provider has a key: find its working free models and backups automatically."""
        data = settings.load()
        for pid in data.get("order", []):
            p = settings.resolved(pid, data)
            if pid not in llm.PAID_PROVIDERS and p["kind"] != "ollama" and p["api_key"] and not p["fallbacks"]:
                r = llm.discover(pid)
                db.audit("system", "models.discover", provider=pid, ok=r.get("ok"), model=r.get("model"))

    def for_each_brain(fn, what):
        users = accounts.list_users() if config.MULTIUSER else [None]
        for u in users:
            if u and u["disabled"]:
                continue
            tok = config.use_dir(accounts.prepare(u)) if u else None
            role = config.set_role(u["role"]) if u else None
            try:
                fn()
            except Exception as exc:  # never let the timer die
                logging.getLogger("brain.scheduler").warning("%s failed: %s", what, exc)
            finally:
                if u:
                    config.reset_dir(tok)
                    config._user_role.reset(role)

    def fast_loop():
        from . import automations, channels, dreaming, plugins
        plugins.load_all()
        time.sleep(3)
        for_each_brain(channels.restart, "Starting channels")
        ticks = 0
        while True:
            for_each_brain(automations.run_due, "Scheduled jobs")
            if ticks % 4 == 0:                         # every two minutes
                from . import workflows
                for_each_brain(workflows.check_mail, "Email watcher")
            if ticks % 120 == 0:                       # about once an hour
                for_each_brain(dreaming.nightly, "Dreaming")
                from . import gmail
                for_each_brain(gmail.renew_if_needed, "Gmail watch")
            ticks += 1
            time.sleep(30)

    threading.Thread(target=fast_loop, daemon=True).start()

    def loop():
        time.sleep(5)
        if not config.MULTIUSER:
            for_each_brain(discover_new_keys, "Model discovery")
        time.sleep(55)
        while True:
            for_each_brain(knowledge.run_due, "Scheduled learning")
            time.sleep(600)
    if config.os.environ.get("BRAIN_NO_SCHEDULER") != "1":
        threading.Thread(target=loop, daemon=True).start()


@asynccontextmanager
async def lifespan(_app):
    accounts.init()                   # also holds API tokens and push subscriptions on a laptop
    db.init()
    knowledge.init()
    identity.ensure()
    settings.load()
    _start_scheduler()
    if not config.MULTIUSER and config.os.environ.get("BRAIN_NO_SCHEDULER") != "1":
        from . import remote
        remote.watch()                # "Use from my phone": listens on the Tailscale address when switched on
        from . import listener
        listener.autostart()          # always listening for your activation word, if you switched it on
    db.audit("system", "startup", providers=settings.load().get("order", []))
    yield


app = FastAPI(title="AI Brain", version="0.1.0", lifespan=lifespan)


SESSION_COOKIE = "brain_session"
# Reachable without unlocking: the interface itself, the look and name for the lock screen, and unlocking.
OPEN_PATHS = ("/api/master/public", "/api/master/unlock", "/api/master/lock")


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
AUTH_COOKIE = "brain_auth"
# Reachable without logging in (on a server with accounts)
PUBLIC_API = ("/api/auth/", "/api/health", "/api/companion/pair", "/api/companion/ws", "/api/hooks/",
              "/api/channels/in/", "/api/gmail/push/", "/v1/")
MAX_BODY = 30 * 1024 * 1024


def _host_of(value: str) -> str:
    value = (value or "").split("://", 1)[-1].split("/", 1)[0]
    return value.split("]")[0] + "]" if value.startswith("[") else value.rsplit(":", 1)[0]


@app.middleware("http")
async def _guard(request, call_next):
    """Who may use the API.

    On your laptop: only pages from this computer (stops other websites and DNS-rebinding tricks),
    and the Master PIN when 'Lock the whole app' is on.
    On a server with accounts: a logged-in user, from this site only, working on their own Brain.
    """
    from fastapi.responses import JSONResponse
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    host = request.headers.get("host") or ""
    origin = request.headers.get("origin")
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse(status_code=413, content={"detail": "That upload is too big (30 MB at most)."})

    if not config.MULTIUSER:
        from . import remote
        client = request.client.host if request.client else ""
        loopback = client in ("127.0.0.1", "::1", "testclient")
        proxied = bool(request.headers.get("x-forwarded-for") or request.headers.get("tailscale-user-login"))
        is_local = (_host_of(host) in LOCAL_HOSTS and loopback and not proxied
                    and not (origin and origin != "null" and _host_of(origin) not in LOCAL_HOSTS))
        if not is_local:
            # "Use from my phone": only your own Tailscale devices, and only with the remote password
            via_tailnet = remote.is_tailnet(client) or (loopback and _host_of(host).endswith(".ts.net"))
            if not (via_tailnet and remote.cfg()["enabled"]):
                return JSONResponse(status_code=403, content={"detail": "Only this computer can use the Brain."})
            if origin and origin != "null" and _host_of(origin) != _host_of(host):
                return JSONResponse(status_code=403, content={"detail": "Requests from other websites are not allowed."})
            bearer_ok = False
            bearer = (request.headers.get("authorization") or "")[7:].strip()
            if bearer:                            # the phone app and Siri Shortcut use an API token instead of a cookie
                found = accounts.token_user(bearer)
                bearer_ok = bool(found) and found[0] is None
            if (path not in ("/api/remote/login", "/api/remote/state") and not bearer_ok
                    and not remote.session_ok(request.cookies.get(remote.COOKIE))):
                return JSONResponse(status_code=401, content={"detail": "Sign in with your phone password.", "remote": True})
        if path not in OPEN_PATHS and master.get()["lock_app"]:
            if not master.session_ok(request.cookies.get(SESSION_COOKIE)):
                return JSONResponse(status_code=401, content={"detail": "Locked. Enter your Master PIN.", "locked": True})
        return await call_next(request)

    # Server with accounts: requests that change things must come from this same site
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != "null" and _host_of(origin) != _host_of(host):
        return JSONResponse(status_code=403, content={"detail": "Requests from other websites are not allowed."})
    if path.startswith(PUBLIC_API):
        return await call_next(request)
    user = accounts.user_for_token(request.cookies.get(AUTH_COOKIE))
    bearer = (request.headers.get("authorization") or "")[7:].strip()
    if not user and bearer:                       # API token (CLI and other apps)
        found = accounts.token_user(bearer)
        user = found[0] if found else None
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Please log in.", "login": True})
    request.state.user = user
    tok = config.use_dir(accounts.prepare(user))
    role = config.set_role(user["role"])
    try:
        return await call_next(request)
    finally:
        config.reset_dir(tok)
        config._user_role.reset(role)


def _need_master(request) -> None:
    """Master Control changes need the PIN session once a PIN is set."""
    if not master.session_ok(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Enter your Master PIN to change Master Control.")


@app.exception_handler(Exception)
async def _unexpected(request, exc):
    """Show the real reason in the app instead of a bare 'Error 500'."""
    from fastapi.responses import JSONResponse
    logging.getLogger("brain.server").exception("Unexpected error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Unexpected error: {exc.__class__.__name__}: {exc}"})
WEB = config.ROOT / "web"


def _err(exc: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(exc))


# ---------- Status ----------

@app.get("/api/status")
def status():
    return {
        "owner": master.owner_name(),
        "brain_name": master.brain_name(),
        "resting": master.get()["resting"],
        **llm.status(),
        "index": memory.index_coverage(),
        "knowledge": knowledge.stats(),
        "web_mode": settings.load().get("web_mode", "ask"),
        "memory": memory.stats(),
        "interview": interview.progress(),
        "control": {"enabled": control.get()["enabled"], "devices": control.get()["devices"],
                    "running": bool(agent.running())},
        "multiuser": config.MULTIUSER,
    }


# ---------- Settings (providers and keys) ----------

@app.get("/api/settings")
def settings_get():
    return {"settings": settings.masked(), "presets": PRESETS, "search_presets": web.SEARCH_PRESETS,
            "voice_defaults": voice.DEFAULTS}


class SettingsIn(BaseModel):
    order: list[str]
    providers: dict[str, dict]
    embeddings: str = "auto"
    cloud_private_tasks: bool = False
    web_mode: str = "ask"
    suggest_facts: bool = True
    search_order: list[str] = Field(default_factory=lambda: ["duckduckgo"])
    search: dict[str, dict] = Field(default_factory=dict)
    voice: dict = Field(default_factory=dict)


@app.put("/api/settings")
def settings_put(body: SettingsIn, request: Request):
    _need_master(request)   # once a Master PIN is set, API keys and settings need it too
    current = settings.load()
    data = body.model_dump()
    for group in ("providers", "search"):
        for pid, cfg in (data.get(group) or {}).items():
            key = cfg.get("api_key", "")
            if key.startswith("••••"):  # unchanged masked key: keep the stored one
                cfg["api_key"] = current.get(group, {}).get(pid, {}).get("api_key", "")
    for k in ("thinking", "tools_off"):             # set elsewhere (/think, the Agent card): keep them
        data.setdefault(k, current.get(k))
    saved = settings.save(data)
    db.audit("owner", "settings.save", order=saved["order"], embeddings=saved["embeddings"],
             cloud_private_tasks=saved["cloud_private_tasks"])
    return {"settings": settings.masked(saved), "presets": PRESETS, "search_presets": web.SEARCH_PRESETS,
            "voice_defaults": voice.DEFAULTS}


@app.post("/api/settings/test/{pid}")
def settings_test(pid: str):
    if pid not in PRESETS:
        _err(Exception("Unknown provider"), 404)
    result = llm.test_provider(pid)
    db.audit("owner", "provider.test", provider=pid, ok=result["ok"])
    return result


@app.get("/api/settings/models/{pid}")
def settings_models(pid: str):
    if pid not in PRESETS:
        _err(Exception("Unknown provider"), 404)
    try:
        return {"models": llm.list_models(pid)}
    except llm.ProviderError as exc:
        _err(exc, 502)


class FreeLLMIn(BaseModel):
    base_url: str = ""
    api_key: str
    model: str = "auto"


@app.post("/api/settings/freellmapi")
def settings_freellmapi(body: FreeLLMIn, request: Request):
    _need_master(request)
    from . import freellm
    try:
        out = freellm.quick_setup(body.base_url, body.api_key)
    except freellm.FreeLLMError as exc:
        _err(exc)
    if body.model in freellm.PROFILES:
        data = settings.load()
        data["providers"]["freellmapi"]["model"] = body.model
        settings.save(data)
    db.audit("owner", "settings.freellmapi", models=len(out["models"]), profile=body.model)
    return out


@app.post("/api/settings/discover")
def settings_discover():
    results = llm.discover_all()
    db.audit("owner", "models.discover", results=[{k: r.get(k) for k in ("provider", "ok", "model")} for r in results])
    return {"results": results, "settings": settings.masked()}


@app.post("/api/settings/discover/{pid}")
def settings_discover_one(pid: str):
    if pid not in PRESETS:
        _err(Exception("Unknown provider"), 404)
    r = llm.discover(pid)
    db.audit("owner", "models.discover", provider=pid, ok=r.get("ok"), model=r.get("model"))
    return {"result": r, "settings": settings.masked()}


@app.post("/api/settings/test-search")
def settings_test_search():
    try:
        results = web.search("weather in Delhi today", n=3)
        return {"ok": True, "engine": results[0]["engine"], "results": results}
    except web.WebError as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/memories/reindex")
def reindex():
    r = memory.reindex()
    r["knowledge_chunks"] = knowledge.reindex()
    return r


# ---------- Master Control ----------

@app.get("/api/master/public")
def master_public(request: Request):
    out = master.public()
    out["unlocked"] = master.session_ok(request.cookies.get(SESSION_COOKIE))
    return out


@app.get("/api/master")
def master_get(request: Request):
    out = master.public()
    out["unlocked"] = master.session_ok(request.cookies.get(SESSION_COOKIE))
    return out


class MasterIn(BaseModel):
    changes: dict


@app.put("/api/master")
def master_put(body: MasterIn, request: Request):
    _need_master(request)
    out = master.update(body.changes)
    db.audit("owner", "master.update", fields=sorted(k for k in body.changes if k in master.DEFAULTS))
    if "wake_words" in body.changes:
        from . import listener
        try:
            listener.restart_if_running()
        except Exception:
            pass
    return out | {"unlocked": True}


class PinIn(BaseModel):
    pin: str = ""
    new_pin: str = ""


@app.post("/api/master/unlock")
def master_unlock(body: PinIn):
    try:
        ok = master.check_pin(body.pin)
    except PermissionError as exc:
        _err(exc, 429)
    if not ok:
        db.audit("system", "master.unlock_failed")
        _err(Exception("That PIN is not right."), 401)
    res = JSONResponse({"ok": True})
    res.set_cookie(SESSION_COOKIE, master.new_session(), httponly=True, samesite="strict", max_age=master.SESSION_SECONDS)
    db.audit("owner", "master.unlock")
    return res


@app.post("/api/master/lock")
def master_lock(request: Request):
    master.end_session(request.cookies.get(SESSION_COOKIE))
    res = JSONResponse({"ok": True})
    res.delete_cookie(SESSION_COOKIE)
    return res


@app.post("/api/master/pin")
def master_pin(body: PinIn, request: Request):
    try:
        master.set_pin(body.new_pin, body.pin)
    except (PermissionError, ValueError) as exc:
        _err(exc, 400)
    db.audit("owner", "master.pin_set")
    res = JSONResponse({"ok": True, "pin_set": True})
    res.set_cookie(SESSION_COOKIE, master.new_session(), httponly=True, samesite="strict", max_age=master.SESSION_SECONDS)
    return res


@app.post("/api/master/pin/remove")
def master_pin_remove(body: PinIn):
    try:
        master.remove_pin(body.pin)
    except PermissionError as exc:
        _err(exc, 400)
    db.audit("owner", "master.pin_removed")
    return {"ok": True, "pin_set": False}


class RestIn(BaseModel):
    resting: bool


@app.post("/api/master/rest")
def master_rest(body: RestIn):
    master.set_resting(body.resting)
    db.audit("owner", "brain.rest" if body.resting else "brain.wake")
    return master.public()


@app.get("/api/master/export")
def master_export(request: Request):
    """A full backup of what the Brain knows. API keys and the PIN are left out."""
    _need_master(request)
    with db.session() as conn:
        tables = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")]
                  for t in ("memories", "pending_memories", "messages", "interview_answers", "knowledge_sources",
                            "knowledge_chunks", "learn_topics")}
    for t in ("memories", "knowledge_chunks"):
        for r in tables[t]:
            r.pop("embedding", None)
    backup = {"exported_at": db.now(), "brain": master.public(), "identity_yaml": identity.raw(), **tables}
    db.audit("owner", "master.export")
    name = f"{master.brain_name().lower().replace(' ', '-')}-backup-{db.now()[:10]}.json"
    return Response(content=json.dumps(backup, ensure_ascii=False, indent=1, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------- Voice ----------

@app.get("/api/voice")
def voice_info():
    return {"config": voice.cfg(), **voice.capabilities()}


class AudioIn(BaseModel):
    data_base64: str
    filename: str = "speech.webm"


@app.post("/api/voice/transcribe")
def voice_transcribe(body: AudioIn):
    try:
        audio = base64.b64decode(body.data_base64)
        if len(audio) > 25_000_000:
            raise voice.VoiceError("Recording is too long.")
        return {"text": voice.transcribe(audio, body.filename)}
    except (voice.VoiceError, ValueError) as exc:
        _err(exc)


class SpeakIn(BaseModel):
    text: str


@app.post("/api/voice/speak")
def voice_speak(body: SpeakIn):
    try:
        audio, mime = voice.speak(body.text)
        return Response(content=audio, media_type=mime)
    except voice.VoiceError as exc:
        _err(exc)


class InstallIn(BaseModel):
    component: str


@app.post("/api/voice/install")
def voice_install(body: InstallIn):
    try:
        r = voice.install(body.component)
        db.audit("owner", "voice.install", component=body.component)
        return r
    except voice.VoiceError as exc:
        _err(exc)


class VoiceNameIn(BaseModel):
    name: str


@app.post("/api/voice/download")
def voice_download(body: VoiceNameIn):
    try:
        r = voice.download_piper_voice(body.name.strip())
        db.audit("owner", "voice.download", voice=body.name)
        return r
    except voice.VoiceError as exc:
        _err(exc)


# ---------- Learning (only ever started by the owner) ----------

class LearnTopicIn(BaseModel):
    topic: str
    instruction: str = ""
    frequency: str = "once"    # once | daily | weekly | monthly


@app.post("/api/learn/topic")
def learn_topic(body: LearnTopicIn):
    try:
        if body.frequency != "once":
            t = knowledge.add_topic(body.topic, body.instruction, body.frequency, run_now=False)
            r = knowledge.research(body.topic, body.instruction)
            return {"result": r, "scheduled": t}
        return {"result": knowledge.research(body.topic, body.instruction)}
    except (web.WebError, ValueError) as exc:
        _err(exc)
    except llm.ModelUnavailable as exc:
        _err(exc, 503)


class LearnUrlIn(BaseModel):
    url: str
    instruction: str = ""


@app.post("/api/learn/url")
def learn_url(body: LearnUrlIn):
    try:
        return knowledge.learn_url(body.url, body.instruction)
    except web.WebError as exc:
        _err(exc)
    except llm.ModelUnavailable as exc:
        _err(exc, 503)


class LearnFileIn(BaseModel):
    name: str
    data_base64: str
    instruction: str = ""
    sensitivity: str = "personal"


@app.post("/api/learn/file")
def learn_file(body: LearnFileIn):
    try:
        data = base64.b64decode(body.data_base64)
        if len(data) > 25_000_000:
            raise web.WebError("File is larger than 25 MB.")
        return knowledge.learn_file(body.name, data, body.instruction, body.sensitivity)
    except (web.WebError, ValueError) as exc:
        _err(exc)
    except llm.ModelUnavailable as exc:
        _err(exc, 503)


class NoteIn(BaseModel):
    text: str
    title: str = ""


@app.post("/api/learn/note")
def learn_note(body: NoteIn):
    if not body.text.strip():
        _err(Exception("Note is empty"))
    return knowledge.learn_note(body.text, body.title)


class SaveSourcesIn(BaseModel):
    urls: list[str]
    instruction: str = ""


@app.post("/api/learn/save-sources")
def save_sources(body: SaveSourcesIn):
    """The owner chose to keep the web pages used in a chat answer."""
    saved, failed = [], []
    for u in body.urls[:6]:
        try:
            saved.append(knowledge.learn_url(u, "")["source"])
        except web.WebError as exc:
            failed.append({"url": u, "error": str(exc)})
    return {"saved": saved, "failed": failed}


@app.get("/api/knowledge")
def knowledge_list():
    return knowledge.sources()


@app.get("/api/knowledge/{sid}")
def knowledge_get(sid: int):
    return {"text": knowledge.source_text(sid)}


@app.delete("/api/knowledge/{sid}")
def knowledge_delete(sid: int):
    if not knowledge.delete_source(sid):
        _err(Exception("Not found"), 404)
    return {"ok": True}


@app.get("/api/topics")
def topics_list():
    return knowledge.topics()


class TopicPatch(BaseModel):
    active: bool


@app.patch("/api/topics/{tid}")
def topics_patch(tid: int, body: TopicPatch):
    knowledge.set_topic_active(tid, body.active)
    return {"ok": True}


@app.delete("/api/topics/{tid}")
def topics_delete(tid: int):
    knowledge.delete_topic(tid)
    return {"ok": True}


class ForgetIn(BaseModel):
    memory_ids: list[int] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)
    clear_chat: bool = False


@app.post("/api/forget")
def forget_selected(body: ForgetIn):
    """Delete the memories and learned sources the owner ticked after a 'forget ...' request."""
    gone_m = sum(1 for i in body.memory_ids[:200] if memory.forget(i))
    gone_k = sum(1 for i in body.source_ids[:200] if knowledge.delete_source(i))
    if body.clear_chat:
        core.clear_history()
    return {"memories": gone_m, "knowledge": gone_k, "chat_cleared": body.clear_chat}


class RuleIn(BaseModel):
    text: str


@app.get("/api/rules")
def rules_list():
    return memory.rules()


@app.post("/api/rules")
def rules_add(body: RuleIn):
    try:
        return memory.add_rule(body.text)
    except ValueError as exc:
        _err(exc)


# ---------- Chat ----------

class ChatIn(BaseModel):
    message: str
    mode: str = "mirror"
    use_sealed: bool = False
    web: bool | None = None   # True: search the web for this message (the owner pressed the Web button)
    voice: bool = False       # spoken turn: short, natural, speakable replies
    agent: str = ""           # which agent answers (empty = the main Brain)
    attachments: list[str] = []   # ids from /api/chat/upload


@app.post("/api/chat")
def chat(body: ChatIn):
    try:
        return core.think(body.message, mode=body.mode, use_sealed=body.use_sealed, web_override=body.web or None,
                          voice=body.voice, agent_id=body.agent, attachments=body.attachments)
    except llm.ModelUnavailable as exc:
        _err(exc, 503)
    except ValueError as exc:
        _err(exc)


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn):
    """Streams the reply as it is written (one JSON object per line), so text and speech start at once."""
    from fastapi.responses import StreamingResponse

    def events():
        try:
            for kind, value in core.think_stream(body.message, mode=body.mode, use_sealed=body.use_sealed,
                                                 web_override=body.web or None, voice=body.voice,
                                                 agent_id=body.agent, attachments=body.attachments):
                if kind == "final":
                    yield json.dumps({"type": "final", "result": value}, ensure_ascii=False, default=str) + "\n"
                else:
                    yield json.dumps({"type": kind, "text": value}, ensure_ascii=False) + "\n"
        except (llm.ModelUnavailable, ValueError) as exc:
            yield json.dumps({"type": "error", "error": str(exc)}) + "\n"
        except Exception as exc:
            logging.getLogger("brain.server").exception("Streaming chat failed")
            yield json.dumps({"type": "error", "error": f"Unexpected error: {exc.__class__.__name__}: {exc}"}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/chat/history")
def chat_history(limit: int = 100, agent: str = ""):
    conv = f"agent:{agent}" if re.fullmatch(r"[a-z0-9-]{1,30}", agent or "") else "main"
    return core.history(conv, limit=max(1, min(500, limit)))


@app.delete("/api/chat/history")
def chat_clear():
    core.clear_history()
    return {"ok": True}


# ---------- Accounts (server with sign-up and login) ----------

def _ip(request) -> str:
    return request.client.host if request.client else ""


def _secure(request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _login_response(request, token: str, user: dict):
    res = JSONResponse({"ok": True, "user": user})
    res.set_cookie(AUTH_COOKIE, token, httponly=True, samesite="lax", secure=_secure(request),
                   max_age=accounts.SESSION_DAYS * 86400, path="/")
    return res


def _auth_call(fn, *args):
    try:
        return fn(*args)
    except accounts.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    """What the app should show: login screen or the Brain."""
    if not config.MULTIUSER:
        return {"multiuser": False, "user": None}
    user = accounts.user_for_token(request.cookies.get(AUTH_COOKIE))
    return {"multiuser": True, "user": user, "signup_open": accounts.signup_open(),
            "first_user": accounts.user_count() == 0, "email_reset": accounts.smtp_ready()}


class SignupIn(BaseModel):
    email: str
    password: str
    name: str


class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/signup")
def auth_signup(body: SignupIn, request: Request):
    if not config.MULTIUSER:
        _err(ValueError("Accounts are only used on the web version."))
    user = _auth_call(accounts.signup, body.email, body.password, body.name, _ip(request))
    accounts.prepare(user)
    token, user = _auth_call(accounts.login, body.email, body.password, _ip(request), request.headers.get("user-agent", ""))
    return _login_response(request, token, user)


@app.post("/api/auth/login")
def auth_login(body: LoginIn, request: Request):
    if not config.MULTIUSER:
        _err(ValueError("Accounts are only used on the web version."))
    token, user = _auth_call(accounts.login, body.email, body.password, _ip(request), request.headers.get("user-agent", ""))
    return _login_response(request, token, user)


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    accounts.logout(request.cookies.get(AUTH_COOKIE)) if config.MULTIUSER else None
    res = JSONResponse({"ok": True})
    res.delete_cookie(AUTH_COOKIE, path="/")
    return res


class ForgotIn(BaseModel):
    email: str


@app.post("/api/auth/forgot")
def auth_forgot(body: ForgotIn, request: Request):
    base = str(request.base_url)
    if request.headers.get("x-forwarded-proto") == "https":
        base = base.replace("http://", "https://", 1)
    try:
        _auth_call(accounts.forgot, body.email, base)
    except HTTPException:
        raise
    except Exception:
        logging.getLogger("brain.server").exception("Reset email failed")
        _err(RuntimeError("Couldn't send the email right now. Try again later."), 502)
    return {"ok": True, "message": "If that email has an account, a reset link is on its way."}


class ResetIn(BaseModel):
    token: str
    password: str


@app.post("/api/auth/reset")
def auth_reset(body: ResetIn):
    _auth_call(accounts.reset_password, body.token, body.password)
    return {"ok": True}


def _user(request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=400, detail="Accounts are only used on the web version.")
    return user


class PasswordIn(BaseModel):
    old: str
    new: str


@app.post("/api/account/password")
def account_password(body: PasswordIn, request: Request):
    _auth_call(accounts.change_password, _user(request)["id"], body.old, body.new, request.cookies.get(AUTH_COOKIE))
    return {"ok": True}


@app.post("/api/account/logout-all")
def account_logout_all(request: Request):
    accounts.logout_everywhere(_user(request)["id"])
    res = JSONResponse({"ok": True})
    res.delete_cookie(AUTH_COOKIE, path="/")
    return res


class DeleteAccountIn(BaseModel):
    password: str


@app.post("/api/account/delete")
def account_delete(body: DeleteAccountIn, request: Request):
    user = _user(request)
    _auth_call(accounts.login, user["email"], body.password, _ip(request))     # confirm it's really them
    agent.stop_all("Account deleted.")
    _auth_call(accounts.delete_account, user["id"])
    res = JSONResponse({"ok": True})
    res.delete_cookie(AUTH_COOKIE, path="/")
    return res


def _admin(request) -> dict:
    user = _user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only the admin can do this.")
    return user


@app.get("/api/admin/users")
def admin_users(request: Request):
    _admin(request)
    return {"users": accounts.list_users(), "signup_open": accounts.signup_open(), "email_reset": accounts.smtp_ready()}


class AdminUserIn(BaseModel):
    changes: dict


@app.patch("/api/admin/users/{uid}")
def admin_user_update(uid: int, body: AdminUserIn, request: Request):
    me = _admin(request)
    out = _auth_call(accounts.admin_update, uid, body.changes, me)
    db.audit("owner", "admin.user_update", user=uid, changes=body.changes)
    return out


@app.post("/api/admin/users/{uid}/reset")
def admin_user_reset(uid: int, request: Request):
    _admin(request)
    temp = accounts.admin_reset(uid)
    db.audit("owner", "admin.password_reset", user=uid)
    return {"temporary_password": temp}


@app.delete("/api/admin/users/{uid}")
def admin_user_delete(uid: int, request: Request):
    me = _admin(request)
    if uid == me["id"]:
        _err(ValueError("Delete your own account from the Account card instead."))
    _auth_call(accounts.delete_account, uid)
    db.audit("owner", "admin.user_delete", user=uid)
    return {"ok": True}


class SignupSettingIn(BaseModel):
    open: bool


@app.post("/api/admin/signup")
def admin_signup(body: SignupSettingIn, request: Request):
    _admin(request)
    accounts.set_setting("signup", "open" if body.open else "closed")
    return {"signup_open": accounts.signup_open()}


# ---------- Companion app (device control on the web version) ----------

@app.post("/api/companion/code")
def companion_code(request: Request):
    user = _user(request)
    return {"code": accounts.pairing_code(user["id"]), "expires_in": 600, "server": _site(request)}


def _site(request) -> str:
    base = str(request.base_url).rstrip("/")
    return base.replace("http://", "https://", 1) if request.headers.get("x-forwarded-proto") == "https" else base


class PairIn(BaseModel):
    code: str
    name: str = "My computer"


@app.post("/api/companion/pair")
def companion_pair(body: PairIn, request: Request):
    if not config.MULTIUSER:
        _err(ValueError("The companion is only used with the web version."))
    token, user = _auth_call(accounts.pair, body.code, body.name, _ip(request))
    return {"token": token, "user": {"name": user["name"], "email": user["email"]}}


@app.get("/api/companion/devices")
def companion_devices(request: Request):
    user = _user(request)
    conn = hub.connected(config.user_key())
    return [d | {"online": bool(conn and conn.device["id"] == d["id"])} for d in accounts.companions(user["id"])]


@app.delete("/api/companion/devices/{cid}")
def companion_remove(cid: int, request: Request):
    user = _user(request)
    accounts.remove_companion(user["id"], cid)
    hub.drop_device(config.user_key(), cid)
    db.audit("owner", "companion.remove", device=cid)
    return {"ok": True}


@app.websocket("/api/companion/ws")
async def companion_ws(ws: WebSocket):
    """The companion app's live link. First message: {"type": "hello", "token": ..., status...}."""
    import asyncio
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 20)
    except Exception:
        await ws.close(code=4000)
        return
    found = accounts.companion_user(str(hello.get("token") or "")) if hello.get("type") == "hello" else None
    if not found:
        await ws.send_json({"type": "error", "error": "This companion isn't linked any more. Pair it again."})
        await ws.close(code=4001)
        return
    user, device = found
    key = str(accounts.user_dir(user))
    info = {k: hello.get(k) for k in ("system", "pc_installed", "adb_installed", "phones", "phone_error") if k in hello}
    conn = hub.Conn(ws, asyncio.get_running_loop(), user, device, info)
    hub.register(key, conn)
    tok = config.use_dir(accounts.prepare(user))
    try:
        db.audit("system", "companion.connected", device=device["name"])
    finally:
        config.reset_dir(tok)
    await ws.send_json({"type": "welcome", "user": user["name"], "device": device["name"]})
    from . import listener
    tok = config.use_dir(accounts.prepare(user))
    try:
        await ws.send_json(listener.companion_settings())
    finally:
        config.reset_dir(tok)
    loop = asyncio.get_running_loop()

    def voice(msg: dict) -> None:
        """A command the companion heard after the activation word: one Brain turn, reply to speak."""
        t = config.use_dir(accounts.prepare(user))
        r = config.set_role(user["role"])
        try:
            reply = core.think(str(msg.get("text", ""))[:2000], mode="assistant", conversation="main", voice=True)["reply"]
        except Exception as exc:
            reply = f"Sorry, {str(exc).splitlines()[0]}"
        finally:
            config.reset_dir(t)
            config._user_role.reset(r)
        asyncio.run_coroutine_threadsafe(ws.send_json({"type": "voice_reply", "id": msg.get("id"), "text": reply}), loop)
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "voice" and str(msg.get("text", "")).strip():
                threading.Thread(target=voice, args=(msg,), daemon=True).start()
                continue
            if msg.get("type") == "learned":
                t2 = config.use_dir(accounts.prepare(user))
                try:
                    await asyncio.to_thread(listener.save_learned, str(msg.get("heard", "")))
                finally:
                    config.reset_dir(t2)
                continue
            if msg.get("type") == "listener":
                conn.info.update(listener_state=str(msg.get("state", ""))[:20], listener_heard=str(msg.get("heard", ""))[:200])
                continue
            hub.deliver(conn, msg)
    except Exception:
        pass
    finally:
        hub.unregister(key, conn)


# ---------- Agent tools, media, conversation search ----------

@app.get("/api/tools")
def tools_get():
    from . import tools
    off = set(settings.load().get("tools_off", []))
    ctx = {"web_asked": True}
    return {"tools": [{"name": n, "description": spec[1], "on": n not in off, "ready": spec[3](ctx)}
                      for n, spec in tools.TOOLS.items()], "thinking": settings.load().get("thinking", "fast")}


class ToolsIn(BaseModel):
    off: list[str] = []
    thinking: str | None = None


@app.put("/api/tools")
def tools_put(body: ToolsIn, request: Request):
    _need_master(request)
    data = settings.load()
    data["tools_off"] = body.off
    if body.thinking:
        data["thinking"] = body.thinking
    settings.save(data)
    db.audit("owner", "tools.update", off=body.off, thinking=body.thinking)
    return tools_get()


@app.get("/api/media/{name}")
def media(name: str):
    from . import tools
    if not re.fullmatch(r"(att-)?[0-9a-f]{16}\.(png|jpg|jpeg|webp|gif|mp4)", name):
        raise HTTPException(status_code=404, detail="Not found")
    path = tools.media_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/chat/upload")
async def chat_upload(file: UploadFile):
    """Attach a photo, document or voice recording to your next message."""
    from . import attachments
    data = await file.read()
    try:
        return attachments.store(file.filename or "file", data)
    except ValueError as exc:
        _err(exc)


class SteerIn(BaseModel):
    text: str = ""
    agent: str = ""


@app.post("/api/chat/steer")
def chat_steer(body: SteerIn):
    """A note for an answer that is still working (it's added before the next step)."""
    from . import goals
    goals.steer_chat(f"agent:{body.agent}" if body.agent else "main", body.text)
    return {"ok": True}


@app.post("/api/chat/abort")
def chat_abort(body: SteerIn):
    from . import goals
    goals.abort_chat(f"agent:{body.agent}" if body.agent else "main")
    return {"ok": True}


@app.post("/api/control/task/{task_id}/steer")
def control_task_steer(task_id: str, body: SteerIn):
    t = agent.get(task_id)
    if not t:
        _err(KeyError("No such task"), 404)
    t.notes.append(body.text.strip()[:500])
    db.audit("owner", "device.steer", task=task_id)
    return {"ok": True}


# ---------- Goals and limits ----------

@app.get("/api/goals")
def goals_get():
    from . import goals, usage
    return {"goals": goals.mine(), "limits": settings.load().get("limits") or {}, "today": usage.today()}


class GoalIn(BaseModel):
    text: str
    max_requests: int = 30
    max_tokens: int = 200000
    deliver: str = "web"


@app.post("/api/goals")
def goals_start(body: GoalIn):
    from . import goals
    try:
        return goals.start(body.text, body.max_requests, body.max_tokens, body.deliver)
    except ValueError as exc:
        _err(exc)


class GoalCtl(BaseModel):
    action: str
    note: str = ""


@app.post("/api/goals/{gid}")
def goals_control(gid: str, body: GoalCtl):
    from . import goals
    try:
        return goals.control(gid, body.action, body.note)
    except ValueError as exc:
        _err(exc)


class LimitsIn(BaseModel):
    daily_requests: int = 0
    daily_tokens: int = 0


@app.put("/api/limits")
def limits_put(body: LimitsIn, request: Request):
    _need_master(request)
    data = settings.load()
    data["limits"] = {"daily_requests": max(0, body.daily_requests), "daily_tokens": max(0, body.daily_tokens)}
    settings.save(data)
    db.audit("owner", "limits.update", **data["limits"])
    return data["limits"]


# ---------- Agents ----------

@app.get("/api/agents")
def agents_get():
    from . import agents
    return {"agents": agents.list_agents()}


class AgentIn(BaseModel):
    agent: dict


@app.post("/api/agents")
def agents_save(body: AgentIn, request: Request):
    _need_master(request)
    from . import agents, channels
    try:
        out = agents.save(body.agent)
    except ValueError as exc:
        _err(exc)
    return {"agent": out, "agents": agents.list_agents()}


@app.delete("/api/agents/{aid}")
def agents_delete(aid: str, request: Request):
    _need_master(request)
    from . import agents
    agents.delete(aid)
    return {"agents": agents.list_agents()}


@app.get("/api/chat/search")
def chat_search(q: str):
    return core.search_conversations(q, limit=40)


# ---------- Channels (Telegram, Discord, Slack, email) ----------

@app.get("/api/channels")
def channels_get(request: Request):
    from . import channels
    base = f"{_site(request)}/api/channels/in"
    sec = channels.inbox_secret()
    return {"config": channels.public_config(), "status": channels.status(), "senders": channels.senders(),
            "channels": channels.CHANNELS,
            "webhooks": {c: f"{base}/{c}/{sec}" for c in sorted(channels.WEBHOOK_CHANNELS)}}


def _as_owner_of_inbox(secret: str):
    """Yield once inside the context of the Brain whose webhook inbox this is."""
    from . import channels
    users = accounts.list_users() if config.MULTIUSER else [None]
    for u in users:
        if u and u["disabled"]:
            continue
        tok = config.use_dir(accounts.prepare(u)) if u else None
        role = config.set_role(u["role"]) if u else None
        try:
            if hmac.compare_digest((settings.load().get("channels") or {}).get("_inbox", "") or "-", secret):
                yield True
                return
        finally:
            if u:
                config.reset_dir(tok)
                config._user_role.reset(role)


@app.get("/api/channels/in/{channel}/{secret}")
def channel_verify(channel: str, secret: str, request: Request):
    """One-time checks some services do when you register the webhook address."""
    from . import channels_extra as cx, channels_more
    q = dict(request.query_params)
    for _ in _as_owner_of_inbox(secret):
        try:
            answer = {"whatsapp": lambda: channels_more.whatsapp_verify(q), "wechat": lambda: cx.wechat_verify(q),
                      "wecom": lambda: cx.wecom_verify(q), "messenger": lambda: cx.meta_verify("messenger", q),
                      "instagram": lambda: cx.meta_verify("instagram", q)}.get(channel, lambda: None)()
        except Exception:
            answer = None
        if answer is not None:
            return Response(content=answer, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/api/channels/in/{channel}/{secret}")
async def channel_inbound(channel: str, secret: str, request: Request):
    """Webhook channels deliver messages here. Each is checked with the service's own signature or secret."""
    from . import channels_extra as cx, channels_more
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    for _ in _as_owner_of_inbox(secret):
        try:
            if channel == "whatsapp":
                channels_more.whatsapp_inbound(raw, headers)
            elif channel == "line":
                channels_more.line_inbound(raw, headers)
            elif channel == "sms":
                form = dict((await request.form()).items())
                channels_more.sms_inbound(f"{_site(request)}{request.url.path}", form, headers)
                return Response(content="<Response/>", media_type="text/xml")
            elif channel in ("wechat", "wecom"):
                q = dict(request.query_params)
                out = cx.wechat_inbound(raw, q) if channel == "wechat" else cx.wecom_inbound(raw, q)
                return Response(content=out, media_type="text/plain")
            elif channel == "feishu":
                return cx.feishu_inbound(json.loads(raw or b"{}"))
            elif channel == "dingtalk":
                cx.dingtalk_inbound(json.loads(raw or b"{}"), headers)
            elif channel == "zalo":
                cx.zalo_inbound(raw, headers)
            elif channel == "nextcloud":
                cx.nextcloud_inbound(raw, headers)
            elif channel == "synology":
                form = dict((await request.form()).items())
                cx.synology_inbound(form)
            elif channel == "rocketchat":
                cx.rocketchat_inbound(json.loads(raw or b"{}"))
            elif channel == "viber":
                cx.viber_inbound(raw, headers)
            elif channel in ("messenger", "instagram"):
                cx.meta_inbound(channel, raw, headers)
            elif channel == "webex":
                cx.webex_inbound(raw, headers)
            else:
                break
        except PermissionError:
            raise HTTPException(status_code=403, detail="Bad signature")
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Unknown inbox")


class ChannelsIn(BaseModel):
    changes: dict


@app.put("/api/channels")
def channels_put(body: ChannelsIn, request: Request):
    _need_master(request)
    from . import channels
    channels.save_config(body.changes)
    time.sleep(0.3)
    return channels_get(request)


class ApproveIn(BaseModel):
    code: str
    allow: bool = True


@app.post("/api/channels/approve")
def channels_approve(body: ApproveIn, request: Request):
    _need_master(request)
    from . import channels
    try:
        channels.approve(body.code, body.allow)
    except ValueError as exc:
        _err(exc)
    return channels_get(request)


class AllowIn(BaseModel):
    channel: str
    sender: str
    name: str = ""


@app.post("/api/channels/allow")
def channels_allow(body: AllowIn, request: Request):
    _need_master(request)
    from . import channels
    try:
        channels.add_allowed(body.channel, body.sender, body.name)
    except ValueError as exc:
        _err(exc)
    return channels_get(request)


@app.delete("/api/channels/senders/{sid}")
def channels_remove(sid: int, request: Request):
    _need_master(request)
    from . import channels
    channels.remove_sender(sid)
    return channels_get(request)


@app.post("/api/channels/test/{channel}")
def channels_test(channel: str):
    from . import channels
    try:
        channels.send_owner(channel, f"👋 Test from {master.brain_name()}. Channels are working.")
    except Exception as exc:
        _err(exc)
    return {"ok": True}


# ---------- Automations: scheduled jobs, heartbeat, webhooks ----------

def _auto():
    from . import automations
    return automations


@app.get("/api/automations")
def automations_get():
    a = _auto()
    return {"jobs": a.list_jobs(), "heartbeat": a.heartbeat(), "default_checklist": a.DEFAULT_CHECKLIST,
            "deliver": list(a.DELIVER), "timezone": master.get().get("timezone") or ""}


class JobIn(BaseModel):
    name: str = ""
    prompt: str
    when: str
    deliver: str = "web"
    tz: str = ""


@app.post("/api/automations")
def automations_add(body: JobIn):
    a = _auto()
    try:
        return a.add(body.name, body.prompt, body.when, body.deliver, body.tz)
    except a.ScheduleError as exc:
        _err(exc)


class JobPatch(BaseModel):
    changes: dict


@app.patch("/api/automations/{jid}")
def automations_update(jid: int, body: JobPatch):
    a = _auto()
    try:
        return a.update(jid, body.changes)
    except a.ScheduleError as exc:
        _err(exc)


@app.delete("/api/automations/{jid}")
def automations_delete(jid: int):
    _auto().delete(jid)
    return {"ok": True}


@app.post("/api/automations/{jid}/run")
def automations_run(jid: int):
    a = _auto()
    job = a.get(jid)
    if not job:
        _err(KeyError("No such job"), 404)
    return a.run(job, {"test": True} if job["kind"] == "webhook" else None)


@app.get("/api/automations/{jid}/runs")
def automations_runs(jid: int):
    return _auto().runs(jid)


class HeartbeatIn(BaseModel):
    enabled: bool
    minutes: int = 60
    checklist: str = ""
    deliver: str = "web"


@app.post("/api/automations/heartbeat")
def automations_heartbeat(body: HeartbeatIn):
    a = _auto()
    try:
        return a.set_heartbeat(body.enabled, body.minutes, body.checklist, body.deliver)
    except a.ScheduleError as exc:
        _err(exc)


class WebhookIn(BaseModel):
    name: str
    prompt: str
    deliver: str = "web"


@app.post("/api/automations/webhook")
def automations_webhook(body: WebhookIn, request: Request):
    a = _auto()
    try:
        job, secret = a.add_webhook(body.name, body.prompt, body.deliver)
    except a.ScheduleError as exc:
        _err(exc)
    return job | {"url": f"{_site(request)}/api/hooks/{secret}"}


_hook_hits: dict[str, list[float]] = {}


@app.post("/api/hooks/{secret}")
async def webhook_in(secret: str, request: Request):
    """Another app triggers a webhook automation. The secret in the address is the password."""
    now = time.time()
    hits = [t for t in _hook_hits.get(secret[:12], []) if now - t < 60]
    if len(hits) >= 20:
        raise HTTPException(status_code=429, detail="Too many calls")
    _hook_hits[secret[:12]] = hits + [now]
    try:
        payload = await request.json()
    except Exception:
        payload = {"text": (await request.body()).decode("utf-8", "replace")[:8000]}
    a = _auto()
    users = accounts.list_users() if config.MULTIUSER else [None]
    for u in users:                                  # find whose webhook this is
        if u and u["disabled"]:
            continue
        tok = config.use_dir(accounts.prepare(u)) if u else None
        role = config.set_role(u["role"]) if u else None
        try:
            job = a.webhook_for(secret)
            if job:
                config.spawn(a.run, job, payload if isinstance(payload, dict) else {"data": payload})
                return {"ok": True, "accepted": job["name"]}
        finally:
            if u:
                config.reset_dir(tok)
                config._user_role.reset(role)
    raise HTTPException(status_code=404, detail="Unknown webhook")


# ---------- API tokens and the OpenAI-compatible API ----------

def _uid(request) -> int:
    u = getattr(request.state, "user", None)
    return u["id"] if u else 0


@app.get("/api/tokens")
def tokens_get(request: Request):
    return accounts.tokens(_uid(request))


class TokenIn(BaseModel):
    name: str = "API token"


@app.post("/api/tokens")
def tokens_make(body: TokenIn, request: Request):
    _need_master(request)
    token, row = accounts.make_token(_uid(request), body.name)
    db.audit("owner", "token.create", name=row["name"])
    return row | {"token": token}


@app.delete("/api/tokens/{tid}")
def tokens_drop(tid: int, request: Request):
    accounts.drop_token(_uid(request), tid)
    db.audit("owner", "token.delete", id=tid)
    return {"ok": True}


def _api_owner(request) -> tuple[dict | None, int]:
    """Check the Bearer token of a /v1 call: (user, or None for the laptop owner; token id)."""
    found = accounts.token_user((request.headers.get("authorization") or "")[7:].strip())
    if not found or (config.MULTIUSER and not found[0]):
        raise HTTPException(status_code=401, detail={"error": {"message": "Invalid API token. Make one in Master → API tokens.",
                                                               "type": "invalid_request_error"}})
    return found


def _as_user(user: dict | None, fn, *args, **kw):
    """Run fn inside that user's Brain."""
    if not user:
        return fn(*args, **kw)
    tok, role = config.use_dir(accounts.prepare(user)), config.set_role(user["role"])
    try:
        return fn(*args, **kw)
    finally:
        config.reset_dir(tok)
        config._user_role.reset(role)


@app.get("/v1/models")
def v1_models(request: Request):
    from . import agents
    user, _ = _api_owner(request)
    ids = ["brain"] + [f"brain:{a['id']}" for a in _as_user(user, agents.list_agents)]
    return {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "ai-brain"} for i in ids]}


@app.post("/v1/chat/completions")
async def v1_chat(request: Request):
    """Use your Brain from any app that speaks the OpenAI API (n8n, scripts, other assistants)."""
    from fastapi.responses import StreamingResponse
    user, tid = _api_owner(request)
    body = await request.json()
    msgs = [m for m in body.get("messages", []) if m.get("role") == "user"]
    if not msgs:
        raise HTTPException(status_code=400, detail="messages must include a user message")
    content = msgs[-1].get("content")
    text = content if isinstance(content, str) else " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict))
    model = str(body.get("model") or "brain")
    agent_id = model.split(":", 1)[1] if model.startswith("brain:") else ""
    cid, created = f"chatcmpl-{uuid.uuid4().hex[:20]}", int(time.time())

    def run_turn():
        return _as_user(user, core.think, text, mode="assistant", conversation=f"api:{tid}", agent_id=agent_id)

    if body.get("stream"):
        def sse():
            try:
                reply = run_turn()["reply"]
            except Exception as exc:
                reply = f"Error: {exc}"
            for k in range(0, len(reply), 40):
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                         "choices": [{"index": 0, "delta": {"content": reply[k:k + 40]}, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            end = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream")
    try:
        out = await asyncio.to_thread(contextvars.copy_context().run, run_turn)
    except llm.ModelUnavailable as exc:
        raise HTTPException(status_code=503, detail={"error": {"message": str(exc).split(chr(10))[0]}})
    return {"id": cid, "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": out["reply"]}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


# ---------- Push notifications, device node, usage, metrics ----------

@app.get("/api/push")
def push_info():
    from . import push
    return {"key": push.public_key(), "devices": push.count(), "nodes": push.nodes()}


class PushIn(BaseModel):
    subscription: dict


@app.post("/api/push/subscribe")
def push_subscribe(body: PushIn):
    from . import push
    try:
        push.subscribe(body.subscription)
    except ValueError as exc:
        _err(exc)
    return {"ok": True, "devices": push.count()}


class PushOffIn(BaseModel):
    endpoint: str


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: PushOffIn):
    from . import push
    push.unsubscribe(body.endpoint)
    return {"ok": True}


@app.post("/api/push/test")
def push_test():
    from . import push
    n = push.notify(f"{master.brain_name()} says hi", "Notifications are working.", "/")
    if not n:
        _err(ValueError("No device has notifications switched on yet."))
    return {"sent": n}


@app.websocket("/api/node/ws")
async def node_ws(ws: WebSocket):
    """The open Brain app on a phone or laptop offers location, camera and notifications to the Brain."""
    from . import push
    origin = ws.headers.get("origin") or ""
    host = ws.headers.get("host") or ""
    if origin and _host_of(origin) != _host_of(host):
        await ws.close(code=4003)
        return
    user = None
    if config.MULTIUSER:
        user = accounts.user_for_token(ws.cookies.get(AUTH_COOKIE))
        if not user:
            await ws.close(code=4001)
            return
    elif _host_of(host) not in LOCAL_HOSTS:
        await ws.close(code=4003)
        return
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 15)
    except Exception:
        await ws.close()
        return
    caps = [c for c in hello.get("caps", []) if c in ("location", "photo", "notify")]
    key = str(accounts.user_dir(user)) if user else str(config.ROOT_DATA)
    node = push.Node(ws, asyncio.get_running_loop(), str(hello.get("name") or "My device")[:40], caps)
    push.add_node(key, node)
    try:
        while True:
            push.deliver(node, await ws.receive_json())
    except Exception:
        pass
    finally:
        push.remove_node(key, node)


@app.get("/api/usage")
def usage_get():
    from . import usage
    return usage.summary()


@app.get("/metrics")
def metrics(request: Request):
    """Prometheus metrics. Laptop: this computer only. Server: needs BRAIN_METRICS_TOKEN as a Bearer token."""
    from . import usage
    if config.MULTIUSER:
        want = os.environ.get("BRAIN_METRICS_TOKEN", "")
        if not want or not hmac.compare_digest((request.headers.get("authorization") or "")[7:], want):
            raise HTTPException(status_code=401, detail="Set BRAIN_METRICS_TOKEN and send it as a Bearer token.")
        rows = {}
        for u in accounts.list_users():
            rows[str(u["id"])] = _as_user(u, usage.summary, 1)
        extra = {"users": len(rows)}
    else:
        if _host_of(request.headers.get("host") or "") not in LOCAL_HOSTS:
            raise HTTPException(status_code=403, detail="Only this computer")
        rows = {"owner": usage.summary(1)}
        extra = {"memories": memory.stats().get("total", 0)}
    return Response(content=usage.prometheus(rows, extra), media_type="text/plain; version=0.0.4")


# ---------- Plugins and skill packs ----------

@app.get("/api/plugins")
def plugins_get():
    from . import plugins
    return {"plugins": plugins.listing(), "can_install": not config.MULTIUSER}


class PluginToggleIn(BaseModel):
    enabled: bool


@app.post("/api/plugins/{name}")
def plugins_toggle(name: str, body: PluginToggleIn, request: Request):
    _need_master(request)
    if config.MULTIUSER and getattr(request.state, "user", {}).get("role") != "admin":
        _err(PermissionError("Only the admin manages plugins."), 403)
    from . import plugins
    plugins.set_enabled(name, body.enabled)
    db.audit("owner", "plugin.toggle", plugin=name, enabled=body.enabled)
    return {"plugins": plugins.listing()}


@app.post("/api/plugins")
async def plugins_install(file: UploadFile, request: Request):
    _need_master(request)
    from . import plugins
    try:
        name = plugins.install_zip(await file.read())
    except (ValueError, PermissionError) as exc:
        _err(exc, 403 if isinstance(exc, PermissionError) else 400)
    return {"installed": name, "plugins": plugins.listing()}


@app.get("/api/skills/export")
def skills_export():
    from . import plugins
    return Response(content=plugins.export_skills(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="brain-skills.zip"'})


@app.post("/api/skills/import")
async def skills_import(file: UploadFile):
    from . import plugins
    try:
        return {"added": plugins.import_skills(file.filename or "skills.zip", await file.read())}
    except Exception as exc:
        _err(ValueError(f"Couldn't read that skill pack: {exc}"))


# ---------- MCP, browser tool, sandbox ----------

@app.get("/api/mcp")
def mcp_get():
    from . import browser, mcp, sandbox
    return {"servers": mcp.public(), "can_stdio": config.allow_private(), "browser": browser.installed(),
            "sandbox": sandbox.mode()}


class McpIn(BaseModel):
    server: dict


@app.post("/api/mcp")
def mcp_save(body: McpIn, request: Request):
    _need_master(request)
    from . import mcp
    try:
        s = mcp.upsert(body.server)
        tools = mcp.refresh(s["name"])
    except mcp.MCPError as exc:
        _err(exc)
    except Exception as exc:
        _err(RuntimeError(f"Saved, but couldn't connect: {exc}"))
    return {"tools": tools, "servers": mcp.public()}


@app.post("/api/mcp/{name}/refresh")
def mcp_refresh(name: str):
    from . import mcp
    try:
        return {"tools": mcp.refresh(name), "servers": mcp.public()}
    except Exception as exc:
        _err(exc)


@app.delete("/api/mcp/{name}")
def mcp_delete(name: str, request: Request):
    _need_master(request)
    from . import mcp
    mcp.remove(name)
    return {"servers": mcp.public()}


@app.post("/api/browser/install")
def browser_install(request: Request):
    _need_master(request)
    from . import browser
    try:
        return browser.install()
    except browser.BrowserError as exc:
        _err(exc)


# ---------- Workflows, standing orders, email watcher ----------

@app.get("/api/workflows")
def workflows_get():
    from . import workflows
    return {"workflows": workflows.list_workflows(), "runs": workflows.runs(), "orders": workflows.orders(),
            "mail": workflows.public_mail()}


class WorkflowIn(BaseModel):
    workflow: dict


@app.post("/api/workflows")
def workflows_save(body: WorkflowIn):
    from . import workflows
    try:
        return workflows.save(body.workflow)
    except ValueError as exc:
        _err(exc)


@app.delete("/api/workflows/{wid}")
def workflows_delete(wid: str):
    from . import workflows
    workflows.delete(wid)
    return {"ok": True}


@app.post("/api/workflows/{wid}/run")
def workflows_run(wid: str):
    from . import workflows
    try:
        return workflows.start(wid)
    except ValueError as exc:
        _err(exc)


class DecideIn(BaseModel):
    approve: bool


@app.post("/api/workflows/runs/{rid}/decide")
def workflows_decide(rid: str, body: DecideIn):
    from . import workflows
    try:
        return workflows.decide(rid, body.approve)
    except ValueError as exc:
        _err(exc)


class OrdersIn(BaseModel):
    orders: list[dict]


@app.put("/api/orders")
def orders_put(body: OrdersIn):
    from . import workflows
    return {"orders": workflows.save_orders(body.orders)}


class MailIn(BaseModel):
    changes: dict


@app.put("/api/mailwatch")
def mailwatch_put(body: MailIn, request: Request):
    _need_master(request)
    from . import workflows
    return workflows.save_mail(body.changes)


@app.post("/api/mailwatch/check")
def mailwatch_check():
    from . import workflows
    try:
        return {"new": workflows.check_mail()}
    except Exception as exc:
        _err(RuntimeError(f"Couldn't read the mailbox: {exc}"))


# ---------- Skills and dreaming ----------

@app.get("/api/skills")
def skills_get():
    from . import skills
    return skills.list_all()


class SkillIn(BaseModel):
    name: str
    description: str = ""
    body: str
    status: str = "active"


@app.post("/api/skills")
def skills_save(body: SkillIn):
    from . import skills
    try:
        return skills.save(body.name, body.description, body.body, body.status)
    except ValueError as exc:
        _err(exc)


class SkillStatusIn(BaseModel):
    status: str


@app.patch("/api/skills/{name}")
def skills_status(name: str, body: SkillStatusIn):
    from . import skills
    try:
        return skills.set_status(name, body.status)
    except ValueError as exc:
        _err(exc)


@app.delete("/api/skills/{name}")
def skills_delete(name: str):
    from . import skills
    skills.delete(name)
    return {"ok": True}


@app.get("/api/dreams")
def dreams_get():
    from . import dreaming
    return dreaming.dreams()


@app.post("/api/dreams/run")
def dreams_run():
    from . import dreaming
    from datetime import datetime as _dt
    out = dreaming.dream(_dt.now().strftime("%Y-%m-%d"), force=True)
    if not out:
        _err(ValueError("Not enough conversation today to dream about yet."))
    return out


# ---------- Device control ----------

@app.get("/api/control")
def control_get(request: Request):
    c = control.get()
    return {"settings": c, "status": devices.status(), "categories": control.CATEGORIES,
            "always_ask": sorted(control.ALWAYS_ASK), "unlocked": master.session_ok(request.cookies.get(SESSION_COOKIE)),
            "pin_set": bool(master.get()["pin_hash"])}


class ControlIn(BaseModel):
    changes: dict


@app.put("/api/control")
def control_put(body: ControlIn, request: Request):
    _need_master(request)
    before = control.get()
    after = control.update(body.changes)
    changed = {k: after[k] for k in after if after[k] != before.get(k)}
    db.audit("owner", "control.update", changed=changed)
    if not after["enabled"] or not any(after["devices"].values()):
        agent.stop_all("Stopped: device control was switched off.")
    return after


class ControlInstallIn(BaseModel):
    component: str          # pc | adb


@app.post("/api/control/install")
def control_install(body: ControlInstallIn, request: Request):
    _need_master(request)
    if config.MULTIUSER and body.component == "adb":          # install ADB on the user's own computer
        try:
            return hub.call(config.user_key(), {"op": "install_adb"}, 240)
        except (devices.DeviceError, PermissionError) as exc:
            _err(exc)
    try:
        out = devices.install_pc() if body.component == "pc" else devices.driver("phone").install_adb()
    except devices.DeviceError as exc:
        _err(exc)
    db.audit("owner", "control.install", component=body.component)
    return out


class PhoneConnectIn(BaseModel):
    address: str
    code: str = ""


@app.post("/api/control/phone/connect")
def phone_connect(body: PhoneConnectIn, request: Request):
    _need_master(request)
    if config.MULTIUSER:
        try:
            out = hub.call(config.user_key(), {"op": "phone_connect", "address": body.address.strip(), "code": body.code.strip()}, 60)
        except (devices.DeviceError, PermissionError) as exc:
            _err(exc)
        return {"ok": True, "message": out.get("message", ""), "phones": out.get("phones", [])}
    ph = devices.driver("phone")
    try:
        paired = ph.pair(body.address.strip(), body.code.strip()) if body.code.strip() else ""
        connected = "" if body.code.strip() else ph.connect(body.address.strip())
    except devices.DeviceError as exc:
        _err(exc)
    db.audit("owner", "control.phone_connect", address=body.address, paired=bool(body.code))
    return {"ok": True, "message": paired or connected, "phones": devices.status()["phones"]}


@app.get("/api/control/look/{device}")
def control_look(device: str):
    """A test screenshot, so you can check control works before giving it a task."""
    if device not in ("pc", "phone"):
        _err(ValueError("device must be pc or phone"))
    if not control.ready(device):
        _err(PermissionError("Allow this device in the Control tab first."), 403)
    try:
        view = devices.look(device)
    except (devices.DeviceError, PermissionError) as exc:
        _err(exc)
    db.audit("owner", "control.test_look", device=device)
    shot = view["shot"]
    return {"image": f"data:{shot['mime']};base64,{shot['data']}", "text": view.get("text", ""),
            "width": shot["width"], "height": shot["height"]}


class TaskIn(BaseModel):
    goal: str
    device: str = "auto"


@app.post("/api/control/task")
def control_task(body: TaskIn):
    try:
        return agent.start(body.goal, body.device).public(with_screen=False)
    except (PermissionError, ValueError) as exc:
        _err(exc, 403 if isinstance(exc, PermissionError) else 400)


@app.get("/api/control/tasks")
def control_tasks():
    return agent.recent()


@app.get("/api/control/task/{task_id}")
def control_task_get(task_id: str, screen: bool = True):
    t = agent.get(task_id)
    if not t:
        _err(KeyError("No such task"), 404)
    return t.public(with_screen=screen)


class TaskReplyIn(BaseModel):
    approve: bool | None = None
    answer: str = ""


@app.post("/api/control/task/{task_id}/reply")
def control_task_reply(task_id: str, body: TaskReplyIn):
    t = agent.get(task_id)
    if not t or t.status != "waiting":
        _err(ValueError("That task isn't waiting for you."))
    db.audit("owner", "device.reply", task=task_id, approve=body.approve, answered=bool(body.answer))
    t.respond(body.approve, body.answer)
    return {"ok": True}


@app.post("/api/control/stop")
def control_stop():
    return {"stopped": agent.stop_all()}


# ---------- Memories ----------

class MemoryIn(BaseModel):
    content: str
    type: str = "episodic"
    sensitivity: str = "personal"
    happened_on: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = "manual"


class MemoryPatch(BaseModel):
    content: str | None = None
    type: str | None = None
    sensitivity: str | None = None
    happened_on: str | None = None
    tags: list[str] | None = None
    locked: bool | None = None


@app.get("/api/memories")
def memories(type: str | None = None, q: str = ""):
    return memory.list_all(type=type or None, query=q)


@app.post("/api/memories")
def add_memory(body: MemoryIn):
    try:
        return memory.add(body.content, type=body.type, source=body.source, sensitivity=body.sensitivity,
                          happened_on=body.happened_on, tags=body.tags)
    except ValueError as exc:
        _err(exc)


@app.patch("/api/memories/{mem_id}")
def edit_memory(mem_id: int, body: MemoryPatch):
    try:
        return memory.update(mem_id, actor="owner", **body.model_dump())
    except KeyError:
        _err(Exception("Memory not found"), 404)


@app.delete("/api/memories/{mem_id}")
def forget_memory(mem_id: int):
    if not memory.forget(mem_id):
        _err(Exception("Memory not found"), 404)
    return {"ok": True}


@app.get("/api/memories/search")
def search_memories(q: str):
    return memory.search(q, limit=15)


@app.get("/api/pending")
def pending():
    return memory.pending()


class ApproveIn(BaseModel):
    sensitivity: str = "personal"


@app.post("/api/pending/{pid}/approve")
def approve(pid: int, body: ApproveIn):
    try:
        return memory.approve(pid, sensitivity=body.sensitivity)
    except KeyError:
        _err(Exception("Not found"), 404)


@app.post("/api/pending/{pid}/reject")
def reject(pid: int):
    memory.reject(pid)
    return {"ok": True}


# ---------- Journal ----------

class JournalIn(BaseModel):
    text: str
    sensitivity: str = "personal"
    date: str = ""


@app.post("/api/journal")
def journal(body: JournalIn):
    try:
        mem = memory.add(body.text, type="episodic", source="journal", sensitivity=body.sensitivity,
                         happened_on=body.date, tags=["journal"])
        core.extract_memories(body.text, source="journal")
        return mem
    except ValueError as exc:
        _err(exc)


@app.get("/api/journal")
def journal_list():
    return [m for m in memory.list_all(type="episodic", limit=500) if m["source"] == "journal"]


# ---------- Interview ----------

@app.get("/api/interview/next")
def interview_next():
    return {"next": interview.next_questions(1), "progress": interview.progress()}


class AnswerIn(BaseModel):
    question_id: str
    answer: str
    sensitivity: str = "personal"


@app.post("/api/interview/answer")
def interview_answer(body: AnswerIn):
    try:
        mem = interview.answer(body.question_id, body.answer, sensitivity=body.sensitivity)
        return {"memory": mem, "next": interview.next_questions(1), "progress": interview.progress()}
    except (KeyError, ValueError) as exc:
        _err(exc)


# ---------- Identity ----------

class IdentityIn(BaseModel):
    yaml: str


@app.get("/api/identity")
def identity_get():
    return {"yaml": identity.raw(), "versions": identity.versions()}


@app.put("/api/identity")
def identity_put(body: IdentityIn):
    try:
        identity.save_raw(body.yaml)
        return {"yaml": identity.raw(), "versions": identity.versions()}
    except (yaml.YAMLError, ValueError) as exc:
        _err(Exception(f"Could not save: {exc}"))


@app.post("/api/identity/restore/{name}")
def identity_restore(name: str):
    try:
        identity.restore(name)
        return {"yaml": identity.raw(), "versions": identity.versions()}
    except FileNotFoundError:
        _err(Exception("Version not found"), 404)


# ---------- Import ----------

class ImportIn(BaseModel):
    text: str
    owner_name: str = ""
    chat_with: str = ""


@app.post("/api/import/whatsapp")
def import_whatsapp(body: ImportIn):
    try:
        result = importer.import_whatsapp(body.text, body.owner_name or master.owner_name(), body.chat_with)
        db.audit("owner", "import.whatsapp", chat_with=body.chat_with, created=result["memories_created"])
        return result
    except ValueError as exc:
        _err(exc)


# ---------- Audit ----------

@app.get("/api/audit")
def audit():
    return db.recent_audit(200)


# ---------- Web app ----------

app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # served from the root so it can make the whole app installable and work offline
    return FileResponse(WEB / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


DOWNLOADS = ("BrainCompanion-Windows.exe", "BrainCompanion-macOS.zip", "BrainCompanion-Linux",
             "BrainTray-Windows.exe", "BrainTray-macOS.zip", "BrainTray-Linux")


@app.get("/api/downloads")
def downloads_list():
    d = config.ROOT_DATA / "downloads"
    files = [{"name": n, "size": (d / n).stat().st_size, "url": f"/download/{n}"} for n in DOWNLOADS if (d / n).is_file()]
    return {"files": files, "script_zip": "/download/BrainCompanion-python.zip"}


@app.get("/download/{name}")
def download(name: str):
    if name == "BrainCompanion-python.zip":
        return Response(content=_companion_zip(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="BrainCompanion-python.zip"'})
    if name not in DOWNLOADS or not (config.ROOT_DATA / "downloads" / name).is_file():
        raise HTTPException(status_code=404, detail="Not available on this server yet.")
    return FileResponse(config.ROOT_DATA / "downloads" / name, filename=name)


_zip_cache: dict = {}


def _companion_zip() -> bytes:
    """The companion as a small Python app (works on Windows, macOS and Linux with Python installed)."""
    if "data" not in _zip_cache:
        import io
        import zipfile
        buf = io.BytesIO()
        root = config.ROOT
        files = ["companion/brain_companion.py", "companion/requirements.txt", "companion/start-companion.bat",
                 "companion/start-companion.sh", "companion/README.txt", "companion/brain_tray.py",
                 *[f"brain/{n}" for n in ("__init__.py", "config.py", "control.py", "devices.py", "settings.py",
                                         "providers.py", "voice.py", "web.py", "listener.py")]]
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                path = root / f
                if path.exists():
                    arc = f"BrainCompanion/{f.removeprefix('companion/')}"
                    z.write(path, arc)
        _zip_cache["data"] = buf.getvalue()
    return _zip_cache["data"]


# ---------- Chrome extension ----------

@app.websocket("/api/ext/ws")
async def extension_ws(ws: WebSocket):
    """The Brain extension in your Chrome. First message: {"type": "hello", "token": <API token>, ...}."""
    from . import extension
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 20)
    except Exception:
        await ws.close(code=4000)
        return
    found = accounts.token_user(str(hello.get("token") or "")) if hello.get("type") == "hello" else None
    if not found or (config.MULTIUSER and not found[0]):
        await ws.send_json({"type": "error", "error": "That API token isn't valid. Make one in Settings → API tokens."})
        await ws.close(code=4001)
        return
    user = found[0]
    key = str(accounts.user_dir(user)) if user else str(config.ROOT_DATA)
    conn = hub.Conn(ws, asyncio.get_running_loop(), user or {}, {"id": 0, "name": "Chrome"},
                    {"browser": str(hello.get("browser") or "Chrome")[:40], "sites": [str(s)[:80] for s in hello.get("sites", [])][:100]})
    extension.register(key, conn)
    tok = config.use_dir(accounts.prepare(user)) if user else None
    try:
        db.audit("system", "extension.connected", browser=conn.info["browser"], sites=len(conn.info["sites"]))
    finally:
        if user:
            config.reset_dir(tok)
    await ws.send_json({"type": "welcome"})
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") != "ping":
                extension.deliver(conn, msg)
    except Exception:
        pass
    finally:
        extension.unregister(key, conn)


@app.get("/api/ext/status")
def extension_status():
    from . import extension
    return extension.status(config.user_key())


@app.get("/api/ext/download")
def extension_download():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((config.ROOT / "extension").iterdir()):
            if f.is_file():
                z.write(f, f"ai-brain-extension/{f.name}")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=ai-brain-extension.zip"})


# ---------- Secrets vault ----------

class VaultIn(BaseModel):
    name: str
    sites: str = ""
    value: str = ""
    username: str = ""
    source: str = "local"
    ref: str = ""
    note: str = ""


@app.get("/api/vault")
def vault_get():
    from . import vault
    return {"items": vault.listing()}


@app.post("/api/vault")
def vault_save(body: VaultIn, request: Request):
    from . import vault
    _need_master(request)
    try:
        return vault.save(body.name, body.sites, body.value, body.username, body.source, body.ref, body.note)
    except vault.VaultError as exc:
        _err(exc)


@app.delete("/api/vault/{name}")
def vault_delete(name: str, request: Request):
    from . import vault
    _need_master(request)
    vault.delete(name)
    return {"ok": True}


# ---------- Memory engines and tracing ----------

@app.get("/api/memory-engines")
def memory_engines_get():
    from . import memory_engines, tracing
    return memory_engines.public() | {"tracing": tracing.status()}


@app.put("/api/memory-engines")
def memory_engines_put(body: dict, request: Request):
    from . import memory_engines
    _need_master(request)
    try:
        return memory_engines.save(body)
    except ValueError as exc:
        _err(exc)


# ---------- Skill marketplace ----------

def _is_admin(request) -> bool:
    u = getattr(request.state, "user", None)
    return not config.MULTIUSER or bool(u and u["role"] == "admin")


@app.get("/api/market/clawhub")
def market_clawhub(q: str = ""):
    from . import market
    try:
        return {"results": market.clawhub_search(q)}
    except market.MarketError as exc:
        _err(exc, 502)


class SlugIn(BaseModel):
    slug: str = ""
    id: str = ""
    name: str = ""
    approve: bool = True


@app.post("/api/market/clawhub/install")
def market_clawhub_install(body: SlugIn):
    from . import market
    try:
        return {"added": market.clawhub_install(body.slug)}
    except market.MarketError as exc:
        _err(exc, 502)


@app.get("/api/market/shelf")
def market_shelf(request: Request):
    from . import market
    return {"items": market.shelf(_is_admin(request)), "admin": _is_admin(request)}


@app.post("/api/market/shelf/publish")
def market_publish(body: SlugIn, request: Request):
    from . import market
    u = getattr(request.state, "user", None)
    try:
        return market.publish(body.name, (u or {}).get("name") or master.owner_name())
    except market.MarketError as exc:
        _err(exc)


@app.post("/api/market/shelf/install")
def market_shelf_install(body: SlugIn):
    from . import market
    try:
        return {"added": market.shelf_install(body.id)}
    except market.MarketError as exc:
        _err(exc)


@app.post("/api/market/shelf/moderate")
def market_moderate(body: SlugIn, request: Request):
    from . import market
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Only the admin approves skills.")
    market.moderate(body.id, body.approve)
    return {"ok": True}


@app.delete("/api/market/shelf/{item_id}")
def market_unpublish(item_id: str, request: Request):
    from . import market
    market.unpublish(item_id, _is_admin(request))
    return {"ok": True}


# ---------- Gmail push ----------

@app.get("/api/gmail")
def gmail_get(request: Request):
    from . import gmail
    return gmail.public(_site(request))


@app.put("/api/gmail")
def gmail_put(body: dict, request: Request):
    from . import gmail
    _need_master(request)
    try:
        gmail.save(body)
    except gmail.GmailError as exc:
        _err(exc)
    return gmail.public(_site(request))


@app.post("/api/gmail/connect")
def gmail_connect(request: Request):
    from . import gmail
    try:
        return {"url": gmail.auth_url(f"{_site(request)}/api/gmail/callback")}
    except gmail.GmailError as exc:
        _err(exc)


@app.get("/api/gmail/callback")
def gmail_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    from fastapi.responses import RedirectResponse
    from . import gmail
    if error or not code or gmail.state_owner(state) != config.user_key():
        return RedirectResponse("/#settings?gmail=failed")
    try:
        gmail.finish(code, f"{_site(request)}/api/gmail/callback")
    except Exception as exc:
        logging.getLogger("brain.gmail").warning("Gmail connect failed: %s", exc)
        return RedirectResponse("/#settings?gmail=failed")
    return RedirectResponse("/#settings?gmail=connected")


@app.post("/api/gmail/watch")
def gmail_watch():
    from . import gmail
    try:
        return gmail.watch()
    except gmail.GmailError as exc:
        _err(exc)


@app.post("/api/gmail/disconnect")
def gmail_disconnect():
    from . import gmail
    gmail.disconnect()
    return {"ok": True}


def _as_owner_of_gmail(secret: str):
    from . import gmail
    users = accounts.list_users() if config.MULTIUSER else [None]
    for u in users:
        if u and u["disabled"]:
            continue
        tok = config.use_dir(accounts.prepare(u)) if u else None
        role = config.set_role(u["role"]) if u else None
        try:
            if hmac.compare_digest((settings.load().get("gmail") or {}).get("secret", "") or "-", secret):
                yield True
                return
        finally:
            if u:
                config.reset_dir(tok)
                config._user_role.reset(role)


@app.post("/api/gmail/push/{secret}")
async def gmail_push(secret: str, request: Request):
    """Google Pub/Sub tells us a new email arrived. Always answer 204 quickly so Google doesn't resend."""
    from . import gmail
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    note = gmail.decode_push(body)
    for _ in _as_owner_of_gmail(secret):
        if note:
            config.spawn(gmail.handle_push, note)
    return Response(status_code=204)


# ---------- Coding workspaces ----------

class WorkspaceIn(BaseModel):
    name: str
    url: str = ""
    message: str = ""


@app.get("/api/workspaces")
def workspaces_list():
    from . import workspaces
    return {"workspaces": workspaces.list_all()}


@app.post("/api/workspaces")
def workspaces_create(body: WorkspaceIn):
    from . import workspaces
    try:
        return workspaces.create(body.name.strip(), body.url.strip())
    except (workspaces.WorkspaceError, ValueError) as exc:
        _err(exc)


@app.get("/api/workspaces/{name}/diff")
def workspaces_diff(name: str):
    from . import workspaces
    try:
        return {"diff": workspaces.diff(name), "files": workspaces.tree(name)}
    except workspaces.WorkspaceError as exc:
        _err(exc)


@app.post("/api/workspaces/{name}/{action}")
def workspaces_action(name: str, action: str, body: WorkspaceIn):
    from . import workspaces
    try:
        if action == "commit":
            return {"result": workspaces.commit(name, body.message)}
        if action == "discard":
            return {"result": workspaces.discard(name)}
    except workspaces.WorkspaceError as exc:
        _err(exc)
    _err(ValueError("Unknown action"), 404)


@app.delete("/api/workspaces/{name}")
def workspaces_delete(name: str):
    from . import workspaces
    try:
        workspaces.delete(name)
    except workspaces.WorkspaceError as exc:
        _err(exc)
    return {"ok": True}


@app.post("/api/channels/{channel}/register-webhook")
def channel_register_webhook(channel: str, request: Request):
    from . import channels, channels_extra
    url = f"{_site(request)}/api/channels/in/{channel}/{channels.inbox_secret()}"
    if not url.startswith("https://"):
        _err(ValueError("Your Brain needs an https:// address for this (see DEPLOY.md)."))
    try:
        return {"result": channels_extra.register_webhook(channel, url)}
    except Exception as exc:
        _err(exc)


# ---------- Backup and restore ----------

class BackupIn(BaseModel):
    passphrase: str


@app.post("/api/backup")
def backup_make(body: BackupIn, request: Request):
    from . import backup
    _need_master(request)
    try:
        data = backup.make(body.passphrase)
    except backup.BackupError as exc:
        _err(exc)
    name = f"ai-brain-backup-{time.strftime('%Y-%m-%d')}.brainbackup"
    return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/backup/restore")
async def backup_restore(file: UploadFile, request: Request):
    from . import backup
    _need_master(request)
    passphrase = request.headers.get("x-backup-passphrase", "")
    blob = await file.read()
    try:
        return await asyncio.to_thread(backup.restore, blob, passphrase)
    except backup.BackupError as exc:
        _err(exc)


# ---------- Use from my phone (Tailscale) ----------

class RemoteIn(BaseModel):
    password: str = ""
    enabled: bool | None = None


@app.get("/api/remote")
def remote_get():
    from . import remote
    return remote.status()


@app.get("/api/remote/state")
def remote_state(request: Request):
    from . import remote
    return {"signed_in": remote.session_ok(request.cookies.get(remote.COOKIE))}


@app.put("/api/remote")
def remote_put(body: RemoteIn, request: Request):
    from . import remote
    _need_master(request)
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "testclient") or request.headers.get("x-forwarded-for"):
        _err(ValueError("Change phone access on the computer itself."), 403)
    try:
        if body.password:
            remote.set_password(body.password)
        if body.enabled is not None:
            return remote.set_enabled(body.enabled)
        return remote.status()
    except remote.RemoteError as exc:
        _err(exc)


@app.post("/api/remote/login")
def remote_login(body: RemoteIn, request: Request):
    from . import remote
    ip = (request.client.host if request.client else "") + "|" + (request.headers.get("tailscale-user-login") or "")
    try:
        token = remote.login(body.password, ip)
    except remote.RemoteError as exc:
        _err(exc, 401)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp = JSONResponse({"ok": True})
    resp.set_cookie(remote.COOKIE, token, max_age=remote.SESSION_DAYS * 86400, httponly=True, samesite="strict", secure=secure)
    return resp


@app.post("/api/remote/logout")
def remote_logout(request: Request):
    from . import remote
    remote.logout(request.cookies.get(remote.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(remote.COOKIE)
    return resp


@app.post("/api/remote/logout-all")
def remote_logout_all(request: Request):
    from . import remote
    _need_master(request)
    remote.logout_all()
    return {"ok": True}


# ---------- Offline AI (Ollama on this computer) ----------

class OfflineIn(BaseModel):
    model: str = "qwen2.5:3b"


@app.get("/api/offline")
def offline_get():
    from . import offline
    return offline.status()


@app.post("/api/offline/pull")
def offline_pull(body: OfflineIn, request: Request):
    from . import offline
    _need_master(request)
    try:
        return offline.pull(body.model)
    except offline.OfflineError as exc:
        _err(exc)


# ---------- The phone app (Android): live link for phone control, and voice commands ----------

@app.websocket("/api/phone/ws")
async def phone_ws(ws: WebSocket):
    """First message: {"type": "hello", "token": <API token>, "name": "...", "accessibility": bool}."""
    from . import phonelink
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 20)
    except Exception:
        await ws.close(code=4000)
        return
    found = accounts.token_user(str(hello.get("token") or "")) if hello.get("type") == "hello" else None
    if not found or (config.MULTIUSER and not found[0]):
        await ws.send_json({"type": "error", "error": "That API token isn't valid. Make one in Master → API tokens."})
        await ws.close(code=4001)
        return
    user = found[0]
    key = str(accounts.user_dir(user)) if user else str(config.ROOT_DATA)
    conn = hub.Conn(ws, asyncio.get_running_loop(), user or {}, {"id": 0, "name": str(hello.get("name") or "My phone")[:40]},
                    {k: hello.get(k) for k in ("accessibility", "listening", "battery", "model")})
    phonelink.register(key, conn)
    tok = config.use_dir(accounts.prepare(user)) if user else None
    try:
        db.audit("system", "phone_app.connected", name=conn.device["name"])
    finally:
        if user:
            config.reset_dir(tok)
    await ws.send_json({"type": "welcome", "name": conn.device["name"]})
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") != "ping":
                phonelink.deliver(conn, msg)
    except Exception:
        pass
    finally:
        phonelink.unregister(key, conn)


@app.get("/api/phone")
def phone_status():
    from . import phonelink
    return phonelink.info(config.user_key())


class VoiceIn(BaseModel):
    text: str
    device: str = ""          # where it was said: "phone", "laptop", "siri"


@app.post("/api/voice/command")
def voice_command(body: VoiceIn, request: Request, format: str = "json"):
    """A spoken command from the phone app, the laptop listener or a Siri Shortcut. Runs one full Brain turn
    (with its tools, including controlling your devices) and returns the reply to speak."""
    from fastapi.responses import PlainTextResponse
    text = body.text.strip()[:2000]
    if not text:
        _err(ValueError("Say something after the wake word."))
    out: dict = {}
    try:
        out = core.think(text, mode="assistant", conversation="main", voice=True)
        reply = out["reply"]
    except llm.ModelUnavailable as exc:
        reply = str(exc).split("\n")[0]
    db.audit("owner", "voice.command", device=body.device or "app")
    if format == "text":
        return PlainTextResponse(reply)
    return {"reply": reply, "task": out.get("task")}


# ---------- Always listening for the activation word (this computer) ----------

class ListenerIn(BaseModel):
    enabled: bool
    lang: str = "en"


@app.get("/api/listener")
def listener_get():
    from . import listener
    return listener.local_status()


@app.put("/api/listener")
def listener_put(body: ListenerIn, request: Request):
    from . import listener
    _need_master(request)
    try:
        return listener.local_set(body.enabled, body.lang)
    except (RuntimeError, ValueError) as exc:
        _err(exc)


@app.post("/api/listener/learn")
def listener_learn(request: Request):
    from . import listener
    _need_master(request)
    try:
        return listener.learn()
    except Exception as exc:
        _err(exc)


@app.delete("/api/listener/learn")
def listener_forget(request: Request):
    from . import listener
    _need_master(request)
    listener.forget_learned()
    return {"ok": True}



@app.get("/api/phone/config")
def phone_config():
    """What the phone app needs: the activation words (Master → Wake words) and language."""
    c = settings.load().get("listener") or {}
    return {"wake_words": master.get().get("wake_words") or ["hey brain"], "lang": c.get("lang", "en")}
