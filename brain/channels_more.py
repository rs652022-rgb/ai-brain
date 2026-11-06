"""More chat channels: WhatsApp, Signal, Matrix, Mattermost, IRC, Twitch, SMS (Twilio) and LINE.

Same rules as the other channels: strangers get a pairing code, groups need a mention, direct
messages share your main conversation.

WhatsApp, SMS and LINE deliver messages by webhook, so the Brain must be reachable over HTTPS
(the web version, or a Cloudflare Tunnel on your laptop). Every webhook is checked with the
service's signature, so nobody else can post fake messages.
"""
import base64
import hashlib
import hmac
import json
import re
import socket
import ssl
import time
import uuid

import httpx

from . import channels as ch
from . import config, settings

# ---------- Registration ----------

NEW = {
    #  channel      fields (besides enabled)                                             secrets                      needed                         limit
    "whatsapp":   (("access_token", "phone_number_id", "app_secret", "verify_token"), ("access_token", "app_secret"), ("access_token", "phone_number_id", "app_secret"), 4000),
    "signal":     (("base_url", "number"), (), ("base_url", "number"), 4000),
    "matrix":     (("homeserver", "access_token", "user_id"), ("access_token",), ("homeserver", "access_token"), 15000),
    "mattermost": (("url", "token"), ("token",), ("url", "token"), 15000),
    "irc":        (("server", "port", "nick", "password", "channels"), ("password",), ("server", "nick"), 400),
    "twitch":     (("nick", "token", "channels"), ("token",), ("nick", "token", "channels"), 450),
    "sms":        (("account_sid", "auth_token", "from_number"), ("auth_token",), ("account_sid", "auth_token", "from_number"), 1500),
    "line":       (("access_token", "channel_secret"), ("access_token", "channel_secret"), ("access_token", "channel_secret"), 4500),
}
for name, (fields, secret, need, limit) in NEW.items():
    if name not in ch.CHANNELS:
        ch.CHANNELS.append(name)
    ch.FIELDS[name] = ("enabled",) + fields
    ch.SECRETS[name] = secret
    ch.NEEDS[name] = list(need)
    ch.LIMITS[name] = limit
ch.WEBHOOK_CHANNELS.update({"whatsapp", "sms", "line"})


def _private_ok(url: str) -> None:
    if not settings.url_allowed(url):
        raise RuntimeError("That address must be a public internet address on a shared server.")


def _cfg(name: str) -> dict:
    return ch.get_config()[name]


# ---------- WhatsApp (Meta's official Cloud API) ----------

GRAPH = "https://graph.facebook.com/v21.0"


def _wa_send(cfg, chat, text, media):
    h = {"Authorization": f"Bearer {cfg['access_token']}"}
    for part in ch.chunks(text, ch.LIMITS["whatsapp"]):
        r = httpx.post(f"{GRAPH}/{cfg['phone_number_id']}/messages", headers=h, timeout=30,
                       json={"messaging_product": "whatsapp", "to": chat, "type": "text", "text": {"body": part}})
        if r.status_code >= 400:
            raise RuntimeError(f"WhatsApp: {r.text[:200]}")
    for m in media:
        data = ch._media_bytes(m)
        if not data:
            continue
        up = httpx.post(f"{GRAPH}/{cfg['phone_number_id']}/media", headers=h, timeout=60,
                        data={"messaging_product": "whatsapp", "type": "image/png"}, files={"file": (m["name"], data, "image/png")})
        if up.status_code < 400:
            httpx.post(f"{GRAPH}/{cfg['phone_number_id']}/messages", headers=h, timeout=30,
                       json={"messaging_product": "whatsapp", "to": chat, "type": "image", "image": {"id": up.json()["id"]}})


def whatsapp_verify(params: dict) -> str | None:
    """Meta's one-time check when you register the webhook."""
    cfg = _cfg("whatsapp")
    if params.get("hub.mode") == "subscribe" and cfg["verify_token"] and \
            hmac.compare_digest(params.get("hub.verify_token", ""), cfg["verify_token"]):
        return params.get("hub.challenge", "")
    return None


def whatsapp_inbound(raw: bytes, headers: dict) -> None:
    cfg = _cfg("whatsapp")
    sig = headers.get("x-hub-signature-256", "")
    good = "sha256=" + hmac.new(cfg["app_secret"].encode(), raw, hashlib.sha256).hexdigest()
    if not cfg["app_secret"] or not hmac.compare_digest(sig, good):
        raise PermissionError("bad signature")
    body = json.loads(raw)
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            v = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in v.get("contacts", [])}
            for msg in v.get("messages", []):
                text = (msg.get("text") or {}).get("body", "")
                if msg.get("type") in ("audio", "voice"):
                    text = _wa_audio(cfg, (msg.get("audio") or msg.get("voice") or {}).get("id", "")) or text
                sender = msg.get("from", "")
                config.spawn(_reply, "whatsapp", sender, names.get(sender, ""), sender, text, True, False)


def _wa_audio(cfg, media_id: str) -> str:
    try:
        h = {"Authorization": f"Bearer {cfg['access_token']}"}
        meta = httpx.get(f"{GRAPH}/{media_id}", headers=h, timeout=20).json()
        audio = httpx.get(meta["url"], headers=h, timeout=60).content
        from . import voice
        return voice.transcribe(audio, "voice.ogg", engine="freellmapi" if voice.cfg()["stt"] == "browser" else voice.cfg()["stt"])
    except Exception:
        return ""


def _reply(channel, sender, name, chat, text, is_dm, mentioned):
    try:
        out = ch.handle(channel, sender, name, chat, text, is_dm, mentioned)
        if out:
            ch.send(channel, chat, out["reply"], out.get("media"))
    except Exception:
        ch.log.exception("%s reply failed", channel)


# ---------- SMS (Twilio) ----------

def _sms_send(cfg, chat, text, media):
    for part in ch.chunks(text + ("\n(Image made: open the Brain app.)" if media else ""), ch.LIMITS["sms"]):
        r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}/Messages.json",
                       auth=(cfg["account_sid"], cfg["auth_token"]), data={"From": cfg["from_number"], "To": chat, "Body": part},
                       timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Twilio: {r.text[:200]}")


def twilio_signature(token: str, url: str, params: dict) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def sms_inbound(url: str, params: dict, headers: dict) -> None:
    cfg = _cfg("sms")
    if not cfg["auth_token"] or not hmac.compare_digest(headers.get("x-twilio-signature", ""),
                                                        twilio_signature(cfg["auth_token"], url, params)):
        raise PermissionError("bad signature")
    sender = params.get("From", "")
    config.spawn(_reply, "sms", sender, "", sender, params.get("Body", ""), True, False)


# ---------- LINE ----------

def _line_send(cfg, chat, text, media):
    for part in ch.chunks(text, ch.LIMITS["line"])[:5]:
        r = httpx.post("https://api.line.me/v2/bot/message/push", headers={"Authorization": f"Bearer {cfg['access_token']}"},
                       json={"to": chat, "messages": [{"type": "text", "text": part}]}, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"LINE: {r.text[:200]}")


def line_inbound(raw: bytes, headers: dict) -> None:
    cfg = _cfg("line")
    good = base64.b64encode(hmac.new(cfg["channel_secret"].encode(), raw, hashlib.sha256).digest()).decode()
    if not cfg["channel_secret"] or not hmac.compare_digest(headers.get("x-line-signature", ""), good):
        raise PermissionError("bad signature")
    for ev in json.loads(raw).get("events", []):
        if ev.get("type") != "message" or (ev.get("message") or {}).get("type") != "text":
            continue
        src = ev.get("source", {})
        is_dm = src.get("type") == "user"
        chat = src.get("userId") if is_dm else (src.get("groupId") or src.get("roomId"))
        text = ev["message"].get("text", "")
        mentioned = bool((ev["message"].get("mention") or {}).get("mentionees"))
        config.spawn(_reply, "line", src.get("userId", ""), "", chat, text, is_dm, mentioned)


# ---------- Signal (through signal-cli-rest-api, which you run yourself) ----------

def _sig_send(cfg, chat, text, media):
    _private_ok(cfg["base_url"])
    body = {"message": text[:6000], "number": cfg["number"], "recipients": [chat]}
    imgs = [ch._media_bytes(m) for m in media]
    if any(imgs):
        body["base64_attachments"] = [base64.b64encode(i).decode() for i in imgs if i]
    r = httpx.post(f"{cfg['base_url'].rstrip('/')}/v2/send", json=body, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Signal: {r.text[:200]}")


def signal_envelope(cfg, env: dict) -> None:
    dm = env.get("dataMessage") or {}
    text = dm.get("message") or ""
    if not text or env.get("source") == cfg["number"]:
        return
    group = (dm.get("groupInfo") or {}).get("groupId")
    me = cfg["number"]
    mentioned = any(m.get("number") == me for m in dm.get("mentions") or [])
    _reply("signal", env.get("sourceNumber") or env.get("source"), env.get("sourceName", ""),
           f"group.{group}" if group else (env.get("sourceNumber") or env.get("source")), text, not group, mentioned)


def _run_signal(r):
    cfg = r.cfg
    _private_ok(cfg["base_url"])
    base = cfg["base_url"].rstrip("/")
    httpx.get(f"{base}/v1/about", timeout=10).raise_for_status()
    r.set("running", cfg["number"])
    while not r.stop.is_set():
        res = httpx.get(f"{base}/v1/receive/{cfg['number']}", timeout=60)
        if res.status_code >= 400:
            raise RuntimeError(f"signal-cli: {res.text[:200]}")
        for item in res.json() or []:
            signal_envelope(cfg, item.get("envelope") or {})
        r.stop.wait(2)


# ---------- Matrix ----------

def _mx(cfg, method, path, **kw):
    base = cfg["homeserver"].rstrip("/")
    _private_ok(base)
    res = httpx.request(method, f"{base}/_matrix/client/v3{path}", headers={"Authorization": f"Bearer {cfg['access_token']}"},
                        timeout=kw.pop("timeout", 40), **kw)
    if res.status_code >= 400:
        raise RuntimeError(f"Matrix {path}: {res.text[:200]}")
    return res.json()


def _mx_send(cfg, chat, text, media):
    for part in ch.chunks(text + ("\n(Image made: open the Brain app.)" if media else ""), ch.LIMITS["matrix"]):
        _mx(cfg, "PUT", f"/rooms/{chat}/send/m.room.message/{uuid.uuid4().hex}", json={"msgtype": "m.text", "body": part})


_mx_members: dict = {}


def matrix_event(cfg, me: str, room: str, ev: dict) -> None:
    if ev.get("type") != "m.room.message" or ev.get("sender") == me:
        return
    body = (ev.get("content") or {}).get("body", "")
    if room not in _mx_members:
        try:
            _mx_members[room] = len(_mx(cfg, "GET", f"/rooms/{room}/joined_members").get("joined", {}))
        except Exception:
            _mx_members[room] = 3
    is_dm = _mx_members[room] <= 2
    local = me.split(":")[0].lstrip("@")
    mentioned = me in body or bool(local and re.search(rf"\b{re.escape(local)}\b", body, re.I))
    _reply("matrix", ev["sender"], ev["sender"], room, body, is_dm, mentioned)


def _run_matrix(r):
    cfg = r.cfg
    me = cfg.get("user_id") or _mx(cfg, "GET", "/account/whoami")["user_id"]
    first = _mx(cfg, "GET", "/sync", params={"timeout": 0, "filter": '{"room":{"timeline":{"limit":1}}}'})
    since = first["next_batch"]                         # skip history: only new messages
    r.set("running", me)
    while not r.stop.is_set():
        data = _mx(cfg, "GET", "/sync", params={"since": since, "timeout": 25000}, timeout=40)
        since = data["next_batch"]
        rooms = data.get("rooms", {})
        for room in rooms.get("invite", {}):           # join so strangers can be told how to pair
            try:
                _mx(cfg, "POST", f"/rooms/{room}/join", json={})
            except Exception:
                pass
        for room, info in rooms.get("join", {}).items():
            for ev in info.get("timeline", {}).get("events", []):
                config.spawn(ch._safe, matrix_event, cfg, me, room, ev)


# ---------- Mattermost ----------

def _mm_send(cfg, chat, text, media):
    base = cfg["url"].rstrip("/")
    _private_ok(base)
    for part in ch.chunks(text + ("\n(Image made: open the Brain app.)" if media else ""), ch.LIMITS["mattermost"]):
        res = httpx.post(f"{base}/api/v4/posts", headers={"Authorization": f"Bearer {cfg['token']}"},
                         json={"channel_id": chat, "message": part}, timeout=30)
        if res.status_code >= 400:
            raise RuntimeError(f"Mattermost: {res.text[:200]}")


def mattermost_event(me: str, data: dict) -> None:
    post = json.loads(data.get("post") or "{}")
    if not post or post.get("user_id") == me:
        return
    mentions = json.loads(data.get("mentions") or "[]")
    _reply("mattermost", post["user_id"], data.get("sender_name", "").lstrip("@"), post["channel_id"],
           re.sub(r"@\S+", "", post.get("message", "")).strip() if me in mentions else post.get("message", ""),
           data.get("channel_type") == "D", me in mentions)


def _run_mattermost(r):
    from websockets.sync.client import connect
    cfg = r.cfg
    base = cfg["url"].rstrip("/")
    _private_ok(base)
    me = httpx.get(f"{base}/api/v4/users/me", headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=20).json()
    if "id" not in me:
        raise RuntimeError(f"Mattermost token: {me.get('message', 'invalid')}")
    with connect(base.replace("https://", "wss://").replace("http://", "ws://") + "/api/v4/websocket", open_timeout=20) as ws:
        ws.send(json.dumps({"seq": 1, "action": "authentication_challenge", "data": {"token": cfg["token"]}}))
        r.set("running", me.get("username", ""))
        while not r.stop.is_set():
            try:
                msg = json.loads(ws.recv(timeout=5))
            except TimeoutError:
                continue
            if msg.get("event") == "posted":
                config.spawn(ch._safe, mattermost_event, me["id"], msg.get("data", {}))


# ---------- IRC and Twitch ----------

_irc_socks: dict = {}


def _irc_send(cfg, chat, text, media, key="irc"):
    sock = _irc_socks.get((config.user_key(), key))
    if not sock:
        raise RuntimeError(f"{key.upper()} isn't connected.")
    for line in text.splitlines():
        for part in ch.chunks(line, ch.LIMITS[key]) or [""]:
            if part.strip():
                sock.sendall(f"PRIVMSG {chat} :{part}\r\n".encode("utf-8", "replace"))
                time.sleep(0.6)                        # stay under flood limits


def irc_line(key: str, nick: str, line: str, send_raw) -> None:
    if line.startswith("PING"):
        send_raw("PONG" + line[4:])
        return
    m = re.match(r"^(?:@\S+ )?:([^!\s]+)!(\S+) PRIVMSG (\S+) :(.*)$", line)
    if not m:
        return
    who, userhost, target, text = m.groups()
    is_dm = target.lower() == nick.lower()
    mentioned = bool(re.search(rf"\b{re.escape(nick)}\b", text, re.I))
    if mentioned:
        text = re.sub(rf"^@?{re.escape(nick)}[:,]?\s*", "", text, flags=re.I)
    sender = f"{who}!{userhost}" if key == "irc" else who.lower()     # Twitch logins are verified; IRC nicks aren't
    config.spawn(_reply, key, sender, who, who if is_dm else target, text, is_dm, mentioned)


def _run_irc_like(r, key: str, host: str, port: int, nick: str, password: str, chans: list[str]):
    ctx = ssl.create_default_context()
    raw = socket.create_connection((host, port), timeout=30)
    sock = ctx.wrap_socket(raw, server_hostname=host)
    sock.settimeout(300)

    def send_raw(s):
        sock.sendall((s + "\r\n").encode("utf-8", "replace"))
    if password:
        send_raw(f"PASS {password}")
    send_raw(f"NICK {nick}")
    if key == "irc":
        send_raw(f"USER {nick} 0 * :{nick}")
    _irc_socks[(config.user_key(), key)] = sock
    buf = b""
    joined = False
    try:
        while not r.stop.is_set():
            data = sock.recv(4096)
            if not data:
                raise RuntimeError("disconnected")
            buf += data
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                text = line.decode("utf-8", "replace")
                if not joined and (" 001 " in text or " 376 " in text):
                    for c in chans:
                        send_raw(f"JOIN {c}")
                    joined = True
                    r.set("running", f"{nick} in {', '.join(chans) or 'DMs only'}")
                if " 464 " in text or "Login authentication failed" in text:
                    raise RuntimeError("wrong password or token")
                irc_line(key, nick, text, send_raw)
    finally:
        _irc_socks.pop((config.user_key(), key), None)
        sock.close()


def _chans(s: str) -> list[str]:
    return [c if c.startswith("#") else f"#{c}" for c in re.split(r"[\s,]+", s or "") if c]


def _run_irc(r):
    c = r.cfg
    _run_irc_like(r, "irc", c["server"], int(c.get("port") or 6697), c["nick"], c.get("password", ""), _chans(c.get("channels")))


def _run_twitch(r):
    c = r.cfg
    tok = c["token"] if c["token"].startswith("oauth:") else f"oauth:{c['token']}"
    _run_irc_like(r, "twitch", "irc.chat.twitch.tv", 6697, c["nick"].lower(), tok, [x.lower() for x in _chans(c["channels"])])


ch.EXTRA_SENDERS.update({
    "whatsapp": _wa_send, "sms": _sms_send, "line": _line_send, "signal": _sig_send, "matrix": _mx_send,
    "mattermost": _mm_send, "irc": lambda c, chat, t, m: _irc_send(c, chat, t, m, "irc"),
    "twitch": lambda c, chat, t, m: _irc_send(c, chat, t, m, "twitch"),
})
ch.RUNNERS.update({"signal": _run_signal, "matrix": _run_matrix, "mattermost": _run_mattermost, "irc": _run_irc,
                   "twitch": _run_twitch})
