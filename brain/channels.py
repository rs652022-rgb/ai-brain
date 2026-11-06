"""Chat channels: talk to your Brain from Telegram, Discord, Slack and email (like OpenClaw's channels).

Safety, as in OpenClaw:
- Nobody can talk to your Brain until you approve them. An unknown person who messages the bot gets a
  pairing code; you approve it in Brain → Channels. Email only accepts addresses you add yourself.
- In group chats it only answers approved people who mention it.
- Messages are handled as you (the owner), so approve only your own accounts.

Direct messages share the "main" conversation with the web app, so you can start on your laptop and
carry on from your phone. Group chats get their own conversation.

Everything connects outwards (long polling or WebSockets), so no public address is needed.
"""
import email
import email.utils
import imaplib
import json
import logging
import re
import secrets
import smtplib
import threading
import time
from email.message import EmailMessage

import httpx

from . import config, db, master, settings

log = logging.getLogger("brain.channels")

CHANNELS = ("telegram", "discord", "slack", "email")
SECRETS = {"telegram": ("token",), "discord": ("token",), "slack": ("bot_token", "app_token"), "email": ("password",)}
FIELDS = {
    "telegram": ("enabled", "token"),
    "discord": ("enabled", "token"),
    "slack": ("enabled", "bot_token", "app_token"),
    "email": ("enabled", "address", "password", "imap_host", "imap_port", "smtp_host", "smtp_port"),
}
LIMITS = {"telegram": 4000, "discord": 1900, "slack": 3500, "email": 100000}


# ---------- Settings ----------

def get_config() -> dict:
    saved = settings.load().get("channels") or {}
    out = {}
    for ch in CHANNELS:
        c = saved.get(ch) or {}
        out[ch] = {k: c.get(k, False if k == "enabled" else "") for k in FIELDS[ch]}
    return out


def public_config() -> dict:
    """Settings for the interface: secrets hidden."""
    out = get_config()
    for ch, keys in SECRETS.items():
        for k in keys:
            out[ch][f"has_{k}"] = bool(out[ch][k])
            out[ch][k] = ""
    return out


def save_config(changes: dict) -> dict:
    data = settings.load()
    cur = get_config()
    for ch, vals in (changes or {}).items():
        if ch not in CHANNELS or not isinstance(vals, dict):
            continue
        for k, v in vals.items():
            if k not in FIELDS[ch]:
                continue
            if k in SECRETS[ch] and not str(v).strip():
                continue                      # blank secret = keep the saved one
            cur[ch][k] = bool(v) if k == "enabled" else str(v).strip()[:300]
    data["channels"] = cur
    settings.save(data)
    db.audit("owner", "channels.update", enabled=[c for c in CHANNELS if cur[c]["enabled"]])
    restart()
    return public_config()


# ---------- Who may talk to the Brain (pairing) ----------

def _sender(channel: str, sender: str) -> dict | None:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM channel_senders WHERE channel=? AND sender=?", (channel, str(sender))).fetchone()
    return dict(r) if r else None


def senders() -> list[dict]:
    with db.session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM channel_senders ORDER BY status, id DESC").fetchall()]


def _pair_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4))


def request_pairing(channel: str, sender: str, name: str, chat: str) -> str:
    with db.session() as conn:
        conn.execute("DELETE FROM channel_senders WHERE status='pending' AND created_at < datetime('now', '-1 day')")
        r = conn.execute("SELECT code FROM channel_senders WHERE channel=? AND sender=?", (channel, str(sender))).fetchone()
        if r:
            return r["code"]
        pending = conn.execute("SELECT COUNT(*) FROM channel_senders WHERE status='pending'").fetchone()[0]
        if pending >= 20:
            return ""                         # someone is spamming the bot: stay silent
        code = _pair_code()
        conn.execute("INSERT INTO channel_senders(channel, sender, name, chat, status, code, created_at) VALUES(?,?,?,?,?,?,?)",
                     (channel, str(sender), name[:80], str(chat), "pending", code, db.now()))
    db.audit("system", "channel.pairing_request", channel=channel, name=name[:80])
    return code


def approve(code_or_id, allow: bool = True) -> dict:
    with db.session() as conn:
        r = conn.execute("SELECT * FROM channel_senders WHERE code=? OR id=?",
                         (str(code_or_id).upper().strip(), str(code_or_id))).fetchone()
        if not r:
            raise ValueError("No pairing request with that code.")
        conn.execute("UPDATE channel_senders SET status=?, code=NULL WHERE id=?", ("allowed" if allow else "blocked", r["id"]))
    db.audit("owner", "channel.approve" if allow else "channel.block", channel=r["channel"], name=r["name"])
    s = dict(r) | {"status": "allowed" if allow else "blocked"}
    if allow:
        try:
            send(s["channel"], s["chat"], f"✅ Approved. Hi {s['name'].split()[0] if s['name'] else ''}, I'm "
                                          f"{master.brain_name()}. Ask me anything.")
        except Exception:
            pass
    return s


def add_allowed(channel: str, sender: str, name: str = "") -> dict:
    """Add a sender yourself (email addresses, or ids you already know)."""
    if channel not in CHANNELS or not str(sender).strip():
        raise ValueError("Pick a channel and enter the address or id.")
    sender = str(sender).strip().lower() if channel == "email" else str(sender).strip()
    with db.session() as conn:
        conn.execute("INSERT INTO channel_senders(channel, sender, name, chat, status, created_at) VALUES(?,?,?,?,?,?) "
                     "ON CONFLICT(channel, sender) DO UPDATE SET status='allowed', code=NULL",
                     (channel, sender, name[:80], sender if channel == "email" else "", "allowed", db.now()))
    db.audit("owner", "channel.allow", channel=channel)
    return _sender(channel, sender)


def remove_sender(sid: int) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM channel_senders WHERE id=?", (sid,))
    db.audit("owner", "channel.remove_sender", id=sid)


def owner_targets() -> dict:
    """Where the Brain can message the owner: {channel: chat id} for running channels."""
    out = {}
    cfg = get_config()
    with db.session() as conn:
        rows = conn.execute("SELECT channel, chat FROM channel_senders WHERE status='allowed' AND chat != '' ORDER BY id").fetchall()
    for r in rows:
        if cfg[r["channel"]]["enabled"] and r["channel"] not in out:
            out[r["channel"]] = r["chat"]
    return out


def active_names() -> list[str]:
    st = status()
    return [c for c in CHANNELS if st.get(c, {}).get("state") == "running"]


# ---------- Handling a message (all channels) ----------

def chunks(text: str, limit: int) -> list[str]:
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        out.append(text[:cut])
        text = text[cut:].lstrip()
    return out + ([text] if text else [])


def handle(channel: str, sender: str, name: str, chat: str, text: str, is_dm: bool, mentioned: bool = False) -> dict | None:
    """One incoming message. Returns {'reply', 'media'} to send back, or None to stay silent."""
    from . import core
    text = (text or "").strip()
    if not text:
        return None
    who = _sender(channel, sender)
    if who and who["status"] == "blocked":
        return None
    if not who or who["status"] != "allowed":
        if not is_dm or channel == "email":
            return None                       # strangers in groups and unknown email senders: ignore
        code = request_pairing(channel, sender, name, chat)
        if not code:
            return None
        owner = master.owner_name()
        return {"reply": f"Hi! I'm {master.brain_name()}, {owner}'s private assistant. I only talk to people "
                         f"{owner.split()[0]} has approved.\n\nPairing code: {code}\n\nAsk {owner.split()[0]} to approve "
                         f"it in Brain → Channels.", "media": []}
    if not is_dm and not mentioned:
        return None                           # groups: only when mentioned
    if is_dm and who["chat"] != str(chat):
        with db.session() as conn:
            conn.execute("UPDATE channel_senders SET chat=? WHERE id=?", (str(chat), who["id"]))
    conversation = "main" if is_dm else f"{channel}:{chat}"
    db.audit("owner", "channel.message", channel=channel, dm=is_dm)
    try:
        out = core.think(text, mode=master.get()["default_mode"], conversation=conversation, channel=channel)
    except Exception as exc:
        log.exception("Channel reply failed")
        from . import llm
        msg = llm.friendly_failure([str(exc)]) if hasattr(llm, "friendly_failure") else str(exc)
        return {"reply": msg.split("\n\nDetails:")[0], "media": []}
    return {"reply": out.get("reply") or "…", "media": out.get("media", []), "task": out.get("task")}


def _media_bytes(m: dict) -> bytes | None:
    from . import tools
    try:
        return (tools.media_dir() / m["name"]).read_bytes()
    except (OSError, KeyError):
        return None


# ---------- Sending ----------

def send(channel: str, chat: str, text: str, media: list | None = None) -> None:
    cfg = get_config()[channel]
    if channel == "telegram":
        for part in chunks(text, LIMITS[channel]):
            _tg(cfg["token"], "sendMessage", {"chat_id": chat, "text": part})
        for m in media or []:
            data = _media_bytes(m)
            if data:
                _tg(cfg["token"], "sendPhoto", {"chat_id": chat}, files={"photo": (m["name"], data)})
    elif channel == "discord":
        for part in chunks(text, LIMITS[channel]):
            _dc(cfg["token"], "POST", f"/channels/{chat}/messages", json={"content": part})
        for m in media or []:
            data = _media_bytes(m)
            if data:
                _dc(cfg["token"], "POST", f"/channels/{chat}/messages",
                    data={"payload_json": json.dumps({"content": ""})}, files={"files[0]": (m["name"], data)})
    elif channel == "slack":
        extra = "\n\n(I made an image; open the Brain app to see it.)" if media else ""
        for part in chunks(text + extra, LIMITS[channel]):
            _slack(cfg["bot_token"], "chat.postMessage", {"channel": chat, "text": part})
    elif channel == "email":
        _send_mail(cfg, chat, f"{master.brain_name()}", text)
    else:
        raise ValueError("Unknown channel")


def send_owner(channel: str, text: str) -> str:
    """Message the owner on one of their linked apps (the only place the Brain can send on its own)."""
    targets = owner_targets()
    if channel not in targets:
        raise ValueError(f"{channel.title()} isn't linked yet. Message your bot there once and approve the pairing.")
    send(channel, targets[channel], text)
    db.audit("brain", "channel.send_owner", channel=channel)
    return channel


# ---------- Telegram (Bot API, long polling) ----------

def _tg(token: str, method: str, data: dict | None = None, files=None, timeout: float = 30) -> dict:
    r = httpx.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=timeout)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method}: {body.get('description') or r.status_code}")
    return body.get("result")


def telegram_update(token: str, me: dict, upd: dict) -> None:
    msg = upd.get("message") or upd.get("edited_message")
    if not msg or (msg.get("from") or {}).get("is_bot"):
        return
    chat, frm = msg["chat"], msg.get("from") or {}
    is_dm = chat.get("type") == "private"
    text = msg.get("text") or msg.get("caption") or ""
    voice = msg.get("voice") or msg.get("audio")
    uname = me.get("username", "")
    mentioned = bool(uname and f"@{uname}".lower() in text.lower()) or \
        ((msg.get("reply_to_message") or {}).get("from") or {}).get("id") == me.get("id")
    if uname:
        text = re.sub(rf"@{re.escape(uname)}\b", "", text, flags=re.I).strip()
    who = _sender("telegram", frm.get("id"))
    if voice and who and who["status"] == "allowed":
        try:
            f = _tg(token, "getFile", {"file_id": voice["file_id"]})
            audio = httpx.get(f"https://api.telegram.org/file/bot{token}/{f['file_path']}", timeout=60).content
            from . import voice as v
            engine = v.cfg()["stt"] if v.cfg()["stt"] in ("groq", "freellmapi", "local") else "freellmapi"
            text = v.transcribe(audio, "voice.ogg", engine=engine)
        except Exception as exc:
            _tg(token, "sendMessage", {"chat_id": chat["id"], "text": f"I couldn't hear that voice note: {exc}"})
            return
    if not text:
        return
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or frm.get("username", "")
    try:
        _tg(token, "sendChatAction", {"chat_id": chat["id"], "action": "typing"})
    except Exception:
        pass
    out = handle("telegram", frm.get("id"), name, chat["id"], text, is_dm, mentioned)
    if out:
        send("telegram", str(chat["id"]), out["reply"], out.get("media"))


def _run_telegram(r: "Runner") -> None:
    token = r.cfg["token"]
    me = _tg(token, "getMe")
    r.set("running", f"@{me.get('username')}")
    offset = 0
    while not r.stop.is_set():
        try:
            updates = _tg(token, "getUpdates", {"offset": offset, "timeout": 25}, timeout=40) or []
        except httpx.HTTPError:
            time.sleep(3)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                telegram_update(token, me, upd)
            except Exception:
                log.exception("Telegram message failed")


# ---------- Discord (Gateway WebSocket + REST) ----------

def _dc(token: str, method: str, path: str, **kw) -> dict:
    r = httpx.request(method, f"https://discord.com/api/v10{path}", headers={"Authorization": f"Bot {token}"}, timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Discord {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if r.content else {}


def discord_message(token: str, me_id: str, d: dict) -> None:
    author = d.get("author") or {}
    if author.get("bot") or author.get("id") == me_id:
        return
    is_dm = not d.get("guild_id")
    mentioned = any(m.get("id") == me_id for m in d.get("mentions", []))
    text = re.sub(rf"<@!?{me_id}>", "", d.get("content") or "").strip()
    try:
        _dc(token, "POST", f"/channels/{d['channel_id']}/typing")
    except Exception:
        pass
    out = handle("discord", author.get("id"), author.get("global_name") or author.get("username", ""),
                 d["channel_id"], text, is_dm, mentioned)
    if out:
        send("discord", d["channel_id"], out["reply"], out.get("media"))


def _run_discord(r: "Runner") -> None:
    from websockets.sync.client import connect
    token = r.cfg["token"]
    intents = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)      # guilds, guild messages, DMs, message content
    with connect("wss://gateway.discord.gg/?v=10&encoding=json", open_timeout=20, max_size=8 * 1024 * 1024) as ws:
        hello = json.loads(ws.recv(timeout=20))
        interval = hello["d"]["heartbeat_interval"] / 1000
        state = {"seq": None, "me": ""}
        lock = threading.Lock()

        def beat():
            while not r.stop.wait(interval):
                with lock:
                    ws.send(json.dumps({"op": 1, "d": state["seq"]}))
        threading.Thread(target=beat, daemon=True).start()
        ws.send(json.dumps({"op": 2, "d": {"token": token, "intents": intents,
                                           "properties": {"os": "linux", "browser": "ai-brain", "device": "ai-brain"}}}))
        while not r.stop.is_set():
            try:
                msg = json.loads(ws.recv(timeout=5))
            except TimeoutError:
                continue
            if msg.get("s") is not None:
                state["seq"] = msg["s"]
            if msg.get("op") == 9:
                raise RuntimeError("Discord refused the bot token or its intents. Turn on Message Content Intent "
                                   "in the Discord developer portal.")
            if msg.get("op") == 7:
                return                          # Discord asked us to reconnect
            if msg.get("t") == "READY":
                state["me"] = msg["d"]["user"]["id"]
                r.set("running", msg["d"]["user"]["username"])
            elif msg.get("t") == "MESSAGE_CREATE":
                config.spawn(_safe, discord_message, token, state["me"], msg["d"])


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:
        log.exception("Channel message failed")


# ---------- Slack (Socket Mode) ----------

def _slack(token: str, method: str, data: dict) -> dict:
    r = httpx.post(f"https://slack.com/api/{method}", headers={"Authorization": f"Bearer {token}"}, json=data, timeout=30)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method}: {body.get('error')}")
    return body


def slack_event(bot_token: str, me: str, ev: dict) -> None:
    if ev.get("bot_id") or ev.get("subtype") or ev.get("user") == me:
        return
    is_dm = ev.get("channel_type") == "im"
    mentioned = ev.get("type") == "app_mention" or f"<@{me}>" in (ev.get("text") or "")
    if ev.get("type") == "message" and not is_dm:
        return                                  # channel messages arrive again as app_mention when mentioned
    text = re.sub(rf"<@{me}>", "", ev.get("text") or "").strip()
    out = handle("slack", ev.get("user"), ev.get("user", ""), ev["channel"], text, is_dm, mentioned)
    if out:
        send("slack", ev["channel"], out["reply"], out.get("media"))


def _run_slack(r: "Runner") -> None:
    from websockets.sync.client import connect
    bot, app = r.cfg["bot_token"], r.cfg["app_token"]
    me = _slack(bot, "auth.test", {}).get("user_id", "")
    url = httpx.post("https://slack.com/api/apps.connections.open", headers={"Authorization": f"Bearer {app}"},
                     timeout=20).json()
    if not url.get("ok"):
        raise RuntimeError(f"Slack app token: {url.get('error')} (needs connections:write and Socket Mode on)")
    with connect(url["url"], open_timeout=20) as ws:
        r.set("running", me)
        while not r.stop.is_set():
            try:
                msg = json.loads(ws.recv(timeout=5))
            except TimeoutError:
                continue
            if msg.get("envelope_id"):
                ws.send(json.dumps({"envelope_id": msg["envelope_id"]}))
            if msg.get("type") == "disconnect":
                return
            if msg.get("type") == "events_api":
                config.spawn(_safe, slack_event, bot, me, msg["payload"]["event"])


# ---------- Email (IMAP in, SMTP out) ----------

def _send_mail(cfg: dict, to: str, subject: str, body: str, reply_to: str = "") -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["address"], to, subject
    if reply_to:
        msg["In-Reply-To"] = msg["References"] = reply_to
    msg.set_content(body)
    port = int(cfg.get("smtp_port") or 587)
    cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with cls(cfg["smtp_host"], port, timeout=30) as s:
        if port != 465:
            s.starttls()
        s.login(cfg["address"], cfg["password"])
        s.send_message(msg)


def _plain_body(m) -> str:
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    return m.get_payload(decode=True).decode(m.get_content_charset() or "utf-8", "replace")


def email_message(cfg: dict, raw: bytes) -> None:
    m = email.message_from_bytes(raw)
    sender = email.utils.parseaddr(m.get("From", ""))[1].lower()
    body = _plain_body(m)
    body = re.split(r"\n(?:On .{5,80} wrote:|-----Original Message-----|>)", body)[0].strip()   # drop quoted replies
    subject = m.get("Subject", "")
    text = f"{subject}\n\n{body}".strip() if subject and not subject.lower().startswith("re:") else body
    out = handle("email", sender, email.utils.parseaddr(m.get("From", ""))[0], sender, text[:8000], True)
    if out:
        _send_mail(cfg, sender, subject if subject.lower().startswith("re:") else f"Re: {subject or 'your message'}",
                   out["reply"], m.get("Message-ID", ""))


def _run_email(r: "Runner") -> None:
    cfg = r.cfg
    r.set("running", cfg["address"])
    while not r.stop.is_set():
        with imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg.get("imap_port") or 993)) as im:
            im.login(cfg["address"], cfg["password"])
            im.select("INBOX")
            _, ids = im.search(None, "UNSEEN")
            for num in (ids[0].split() if ids and ids[0] else [])[:20]:
                _, data = im.fetch(num, "(RFC822)")
                im.store(num, "+FLAGS", "\\Seen")
                try:
                    email_message(cfg, data[0][1])
                except Exception:
                    log.exception("Email reply failed")
        r.stop.wait(60)


# ---------- Running the channels in the background ----------

RUNNERS = {"telegram": _run_telegram, "discord": _run_discord, "slack": _run_slack, "email": _run_email}
_runners: dict[tuple[str, str], "Runner"] = {}
_lock = threading.Lock()


class Runner:
    def __init__(self, channel: str, cfg: dict):
        self.channel, self.cfg = channel, cfg
        self.stop = threading.Event()
        self.state, self.detail = "starting", ""

    def set(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail

    def loop(self) -> None:
        delay = 5
        while not self.stop.is_set():
            try:
                RUNNERS[self.channel](self)
                delay = 5
            except Exception as exc:
                self.set("error", str(exc)[:300])
                log.warning("%s channel: %s", self.channel, exc)
                if "token" in str(exc).lower() or "Unauthorized" in str(exc) or "invalid_auth" in str(exc):
                    delay = 300                 # wrong token: don't hammer the service
            if self.stop.wait(delay):
                break
            delay = min(300, delay * 2)
        self.set("off")


def _ready(channel: str, c: dict) -> bool:
    need = {"telegram": ["token"], "discord": ["token"], "slack": ["bot_token", "app_token"],
            "email": ["address", "password", "imap_host", "smtp_host"]}[channel]
    return bool(c.get("enabled")) and all(c.get(k) for k in need)


def restart() -> None:
    """(Re)start this user's channels to match their settings. While resting the Brain still listens,
    so your wake word works from your phone too."""
    me = config.user_key()
    cfg = get_config()
    with _lock:
        for ch in CHANNELS:
            key = (me, ch)
            old = _runners.get(key)
            want = _ready(ch, cfg[ch])
            if old and (not want or old.cfg != cfg[ch]):
                old.stop.set()
                _runners.pop(key, None)
                old = None
            if want and not old:
                run = Runner(ch, dict(cfg[ch]))
                _runners[key] = run
                config.spawn(run.loop)


def stop_mine() -> None:
    me = config.user_key()
    with _lock:
        for key in [k for k in _runners if k[0] == me]:
            _runners.pop(key).stop.set()


def status() -> dict:
    me = config.user_key()
    cfg = get_config()
    out = {}
    for ch in CHANNELS:
        r = _runners.get((me, ch))
        out[ch] = {"state": r.state if r else ("off" if not cfg[ch]["enabled"] else "incomplete"),
                   "detail": r.detail if r else ""}
    return out
