"""Local web server. Binds to 127.0.0.1 only, so nothing outside your laptop can reach it."""
import logging
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket
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
    if config.MULTIUSER:
        accounts.init()
    db.init()
    knowledge.init()
    identity.ensure()
    settings.load()
    _start_scheduler()
    db.audit("system", "startup", providers=settings.load().get("order", []))
    yield


app = FastAPI(title="AI Brain", version="0.1.0", lifespan=lifespan)


SESSION_COOKIE = "brain_session"
# Reachable without unlocking: the interface itself, the look and name for the lock screen, and unlocking.
OPEN_PATHS = ("/api/master/public", "/api/master/unlock", "/api/master/lock")


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
AUTH_COOKIE = "brain_auth"
# Reachable without logging in (on a server with accounts)
PUBLIC_API = ("/api/auth/", "/api/health", "/api/companion/pair", "/api/companion/ws")
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
        if _host_of(host) not in LOCAL_HOSTS or (origin and origin != "null" and _host_of(origin) not in LOCAL_HOSTS):
            return JSONResponse(status_code=403, content={"detail": "Only this computer can use the Brain."})
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


@app.post("/api/chat")
def chat(body: ChatIn):
    try:
        return core.think(body.message, mode=body.mode, use_sealed=body.use_sealed, web_override=body.web or None,
                          voice=body.voice)
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
                                                 web_override=body.web or None, voice=body.voice):
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
def chat_history():
    return core.history()


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
    try:
        while True:
            hub.deliver(conn, await ws.receive_json())
    except Exception:
        pass
    finally:
        hub.unregister(key, conn)


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


DOWNLOADS = ("BrainCompanion-Windows.exe", "BrainCompanion-macOS.zip", "BrainCompanion-Linux")


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
                 "companion/start-companion.sh", "companion/README.txt",
                 *[f"brain/{n}" for n in ("__init__.py", "config.py", "control.py", "devices.py", "settings.py",
                                         "providers.py", "voice.py", "web.py")]]
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                path = root / f
                if path.exists():
                    arc = f"BrainCompanion/{f.removeprefix('companion/')}"
                    z.write(path, arc)
        _zip_cache["data"] = buf.getvalue()
    return _zip_cache["data"]
