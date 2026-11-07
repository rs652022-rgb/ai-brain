"""Even more chat channels: WeChat, WeCom, Feishu/Lark, DingTalk, QQ, Zalo, Nextcloud Talk, Synology Chat,
Rocket.Chat, Zulip, Viber, Facebook Messenger, Instagram, Webex and Nostr.

Same rules as every channel: strangers get a pairing code, groups need a mention, and every webhook is
checked with the service's own signature or secret, so nobody can post fake messages.
Webhook channels need the Brain online over HTTPS; Zulip, QQ and Nostr connect outwards.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
import xml.etree.ElementTree as ET

import httpx

from . import channels as ch
from . import config
from .channels_more import _private_ok, _reply

# ---------- Registration ----------

NEW = {
    #  channel        fields                                                              secrets                              needed                                   limit  webhook?
    "wechat":      (("app_id", "app_secret", "token"), ("app_secret", "token"), ("app_id", "app_secret", "token"), 600, True),
    "wecom":       (("corp_id", "agent_id", "secret", "token", "aes_key"), ("secret", "token", "aes_key"), ("corp_id", "agent_id", "secret", "token", "aes_key"), 2000, True),
    "feishu":      (("app_id", "app_secret", "verification_token", "encrypt_key", "domain"), ("app_secret", "verification_token", "encrypt_key"), ("app_id", "app_secret", "verification_token"), 4000, True),
    "dingtalk":    (("app_secret",), ("app_secret",), ("app_secret",), 4000, True),
    "qq":          (("app_id", "app_secret"), ("app_secret",), ("app_id", "app_secret"), 2000, False),
    "zalo":        (("app_id", "oa_secret", "access_token"), ("oa_secret", "access_token"), ("app_id", "oa_secret", "access_token"), 2000, True),
    "nextcloud":   (("server", "secret"), ("secret",), ("server", "secret"), 4000, True),
    "synology":    (("incoming_url", "token"), ("incoming_url", "token"), ("incoming_url", "token"), 2000, True),
    "rocketchat":  (("server", "user_id", "auth_token", "token"), ("auth_token", "token"), ("server", "user_id", "auth_token", "token"), 4000, True),
    "zulip":       (("site", "email", "api_key"), ("api_key",), ("site", "email", "api_key"), 9000, False),
    "viber":       (("auth_token", "bot_name"), ("auth_token",), ("auth_token",), 6000, True),
    "messenger":   (("page_token", "app_secret", "verify_token"), ("page_token", "app_secret"), ("page_token", "app_secret"), 1900, True),
    "instagram":   (("page_token", "app_secret", "verify_token"), ("page_token", "app_secret"), ("page_token", "app_secret"), 900, True),
    "webex":       (("bot_token", "secret"), ("bot_token", "secret"), ("bot_token",), 6000, True),
    "nostr":       (("private_key", "relays"), ("private_key",), ("private_key", "relays"), 4000, False),
}
for name, (fields, secret, need, limit, hook) in NEW.items():
    if name not in ch.CHANNELS:
        ch.CHANNELS.append(name)
    ch.FIELDS[name] = ("enabled",) + fields
    ch.SECRETS[name] = secret
    ch.NEEDS[name] = list(need)
    ch.LIMITS[name] = limit
    if hook:
        ch.WEBHOOK_CHANNELS.add(name)


def _cfg(name: str) -> dict:
    return ch.get_config()[name]


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))


# Short-lived access tokens most Chinese platforms use
_tokens: dict = {}


def _cached(key: str, fetch) -> str:
    tok, exp = _tokens.get(key, ("", 0))
    if time.time() < exp:
        return tok
    tok, ttl = fetch()
    _tokens[key] = (tok, time.time() + max(60, ttl - 300))
    return tok


def _post(url, **kw):
    r = httpx.post(url, timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{url.split('/')[2]}: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if r.content else {}


# ---------- WeChat Official Account ----------

def _wx_sig_ok(token: str, q: dict, extra: str = "") -> bool:
    parts = sorted([token, q.get("timestamp", ""), q.get("nonce", "")] + ([extra] if extra else []))
    return _eq(hashlib.sha1("".join(parts).encode()).hexdigest(), q.get("msg_signature") or q.get("signature", ""))


def wechat_verify(q: dict) -> str | None:
    return q.get("echostr") if _wx_sig_ok(_cfg("wechat")["token"], q) else None


def wechat_inbound(raw: bytes, q: dict) -> str:
    c = _cfg("wechat")
    if not _wx_sig_ok(c["token"], q):
        raise PermissionError("bad signature")
    x = ET.fromstring(raw)
    if x.findtext("MsgType") == "text":
        user = x.findtext("FromUserName")
        config.spawn(_reply, "wechat", user, "", user, x.findtext("Content") or "", True, False)
    return "success"                     # answer arrives a moment later as a customer-service message


def _wx_send(c, chat, text, media):
    tok = _cached(f"wx:{c['app_id']}", lambda: _wx_token(c))
    for part in ch.chunks(text, ch.LIMITS["wechat"]):
        out = _post(f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={tok}",
                    content=json.dumps({"touser": chat, "msgtype": "text", "text": {"content": part}}, ensure_ascii=False).encode())
        if out.get("errcode"):
            raise RuntimeError(f"WeChat: {out.get('errmsg')}")


def _wx_token(c):
    r = httpx.get("https://api.weixin.qq.com/cgi-bin/token", timeout=20,
                  params={"grant_type": "client_credential", "appid": c["app_id"], "secret": c["app_secret"]}).json()
    if "access_token" not in r:
        raise RuntimeError(f"WeChat token: {r.get('errmsg')}")
    return r["access_token"], r.get("expires_in", 7200)


# ---------- WeCom (WeChat Work) app ----------

def _wecom_key(c) -> bytes:
    return base64.b64decode(c["aes_key"] + "=")


def wecom_decrypt(c, encrypted: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = _wecom_key(c)
    d = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    plain = d.update(base64.b64decode(encrypted)) + d.finalize()
    plain = plain[:-plain[-1]]
    size = struct.unpack(">I", plain[16:20])[0]
    if plain[20 + size:].decode() != c["corp_id"]:
        raise PermissionError("not for this company")
    return plain[20:20 + size].decode()


def wecom_encrypt(c, text: str) -> str:
    """Used in tests and by the URL check."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = _wecom_key(c)
    body = os.urandom(16) + struct.pack(">I", len(text.encode())) + text.encode() + c["corp_id"].encode()
    pad = 32 - len(body) % 32
    body += bytes([pad]) * pad
    e = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(e.update(body) + e.finalize()).decode()


def wecom_verify(q: dict) -> str | None:
    c = _cfg("wecom")
    if not _wx_sig_ok(c["token"], q, q.get("echostr", "")):
        return None
    return wecom_decrypt(c, q["echostr"])


def wecom_inbound(raw: bytes, q: dict) -> str:
    c = _cfg("wecom")
    enc = ET.fromstring(raw).findtext("Encrypt") or ""
    if not _wx_sig_ok(c["token"], q, enc):
        raise PermissionError("bad signature")
    x = ET.fromstring(wecom_decrypt(c, enc))
    if x.findtext("MsgType") == "text":
        user = x.findtext("FromUserName")
        config.spawn(_reply, "wecom", user, "", user, x.findtext("Content") or "", True, False)
    return "success"


def _wecom_send(c, chat, text, media):
    def fetch():
        r = httpx.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken", params={"corpid": c["corp_id"], "corpsecret": c["secret"]},
                      timeout=20).json()
        if r.get("errcode"):
            raise RuntimeError(f"WeCom token: {r.get('errmsg')}")
        return r["access_token"], r.get("expires_in", 7200)
    tok = _cached(f"wecom:{c['corp_id']}:{c['agent_id']}", fetch)
    for part in ch.chunks(text, ch.LIMITS["wecom"]):
        out = _post(f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={tok}",
                    json={"touser": chat, "msgtype": "text", "agentid": int(c["agent_id"]), "text": {"content": part}})
        if out.get("errcode"):
            raise RuntimeError(f"WeCom: {out.get('errmsg')}")


# ---------- Feishu / Lark ----------

def _fs_base(c) -> str:
    return "https://open.larksuite.com" if (c.get("domain") or "").lower().startswith("lark") else "https://open.feishu.cn"


def feishu_decrypt(key: str, encrypted: str) -> dict:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    k = hashlib.sha256(key.encode()).digest()
    raw = base64.b64decode(encrypted)
    d = Cipher(algorithms.AES(k), modes.CBC(raw[:16])).decryptor()
    plain = d.update(raw[16:]) + d.finalize()
    return json.loads(plain[:-plain[-1]].decode())


def feishu_inbound(body: dict) -> dict:
    c = _cfg("feishu")
    if "encrypt" in body:
        if not c["encrypt_key"]:
            raise PermissionError("encrypted event but no encrypt key set")
        body = feishu_decrypt(c["encrypt_key"], body["encrypt"])
    token = body.get("token") or (body.get("header") or {}).get("token")
    if not _eq(token, c["verification_token"]):
        raise PermissionError("bad token")
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}
    if (body.get("header") or {}).get("event_type") == "im.message.receive_v1":
        ev = body["event"]
        msg = ev["message"]
        if msg.get("message_type") == "text":
            text = json.loads(msg.get("content") or "{}").get("text", "")
            mentioned = bool(msg.get("mentions"))
            text = re.sub(r"@_user_\d+\s*", "", text).strip()
            sender = ((ev.get("sender") or {}).get("sender_id") or {}).get("open_id", "")
            config.spawn(_reply, "feishu", sender, "", msg["chat_id"], text, msg.get("chat_type") == "p2p", mentioned)
    return {"ok": True}


def _fs_send(c, chat, text, media):
    base = _fs_base(c)

    def fetch():
        r = httpx.post(f"{base}/open-apis/auth/v3/tenant_access_token/internal",
                       json={"app_id": c["app_id"], "app_secret": c["app_secret"]}, timeout=20).json()
        if r.get("code"):
            raise RuntimeError(f"Feishu token: {r.get('msg')}")
        return r["tenant_access_token"], r.get("expire", 7200)
    tok = _cached(f"fs:{c['app_id']}", fetch)
    for part in ch.chunks(text, ch.LIMITS["feishu"]):
        out = _post(f"{base}/open-apis/im/v1/messages?receive_id_type=chat_id", headers={"Authorization": f"Bearer {tok}"},
                    json={"receive_id": chat, "msg_type": "text", "content": json.dumps({"text": part}, ensure_ascii=False)})
        if out.get("code"):
            raise RuntimeError(f"Feishu: {out.get('msg')}")


# ---------- DingTalk (enterprise robot, HTTP mode) ----------

def dingtalk_sign(secret: str, timestamp: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()).decode()


_ding_hooks: dict = {}


def dingtalk_inbound(body: dict, headers: dict) -> None:
    c = _cfg("dingtalk")
    ts = headers.get("timestamp", "")
    if not _eq(headers.get("sign", ""), dingtalk_sign(c["app_secret"], ts)) or abs(time.time() * 1000 - int(ts or 0)) > 3600_000:
        raise PermissionError("bad signature")
    chat = body.get("conversationId", "")
    if body.get("sessionWebhook"):
        _ding_hooks[(config.user_key(), chat)] = body["sessionWebhook"]
    text = ((body.get("text") or {}).get("content") or "").strip()
    is_dm = body.get("conversationType") == "1"
    config.spawn(_reply, "dingtalk", body.get("senderStaffId") or body.get("senderId", ""), body.get("senderNick", ""),
                 chat, text, is_dm, bool(body.get("isInAtList")) or is_dm)


def _ding_send(c, chat, text, media):
    hook = _ding_hooks.get((config.user_key(), chat))
    if not hook or not hook.startswith("https://oapi.dingtalk.com/"):
        raise RuntimeError("DingTalk can only reply to a recent message.")
    for part in ch.chunks(text, ch.LIMITS["dingtalk"]):
        _post(hook, json={"msgtype": "text", "text": {"content": part}})


# ---------- QQ (official bot platform, WebSocket gateway) ----------

def _qq_token(c) -> str:
    def fetch():
        r = httpx.post("https://bots.qq.com/app/getAppAccessToken", json={"appId": c["app_id"], "clientSecret": c["app_secret"]},
                       timeout=20).json()
        if "access_token" not in r:
            raise RuntimeError(f"QQ token: {r}")
        return r["access_token"], int(r.get("expires_in", 7200))
    return _cached(f"qq:{c['app_id']}", fetch)


_qq_last: dict = {}                       # chat -> last message id (QQ requires replying to a message)


def _qq_send(c, chat, text, media):
    h = {"Authorization": f"QQBot {_qq_token(c)}"}
    kind, oid = chat.split(":", 1)
    path = f"/v2/users/{oid}/messages" if kind == "user" else f"/v2/groups/{oid}/messages"
    for i, part in enumerate(ch.chunks(text, ch.LIMITS["qq"])[:5]):
        _post(f"https://api.sgroup.qq.com{path}", headers=h,
              json={"content": part, "msg_type": 0, "msg_id": _qq_last.get((config.user_key(), chat), ""), "msg_seq": i + 1})


def qq_event(t: str, d: dict) -> None:
    if t == "C2C_MESSAGE_CREATE":
        oid = (d.get("author") or {}).get("user_openid", "")
        chat, is_dm, mentioned = f"user:{oid}", True, False
    elif t == "GROUP_AT_MESSAGE_CREATE":
        oid = (d.get("author") or {}).get("member_openid", "")
        chat, is_dm, mentioned = f"group:{d.get('group_openid')}", False, True
    else:
        return
    _qq_last[(config.user_key(), chat)] = d.get("id", "")
    _reply("qq", oid, "", chat, (d.get("content") or "").strip(), is_dm, mentioned)


def _run_qq(r):
    from websockets.sync.client import connect
    c = r.cfg
    h = {"Authorization": f"QQBot {_qq_token(c)}"}
    url = httpx.get("https://api.sgroup.qq.com/gateway", headers=h, timeout=20).json()["url"]
    with connect(url, open_timeout=20) as ws:
        hello = json.loads(ws.recv(timeout=20))
        interval = hello["d"]["heartbeat_interval"] / 1000
        seq = {"s": None}

        def beat():
            while not r.stop.wait(interval):
                ws.send(json.dumps({"op": 1, "d": seq["s"]}))
        threading.Thread(target=beat, daemon=True).start()
        ws.send(json.dumps({"op": 2, "d": {"token": f"QQBot {_qq_token(c)}", "intents": 1 << 25, "shard": [0, 1]}}))
        while not r.stop.is_set():
            try:
                m = json.loads(ws.recv(timeout=5))
            except TimeoutError:
                continue
            if m.get("s"):
                seq["s"] = m["s"]
            if m.get("t") == "READY":
                r.set("running", (m["d"].get("user") or {}).get("username", "QQ bot"))
            elif m.get("op") == 0:
                config.spawn(ch._safe, qq_event, m.get("t"), m.get("d") or {})
            elif m.get("op") in (7, 9):
                return


# ---------- Zalo Official Account ----------

def zalo_inbound(raw: bytes, headers: dict) -> None:
    c = _cfg("zalo")
    body = json.loads(raw)
    mac = hashlib.sha256(f"{c['app_id']}{raw.decode()}{body.get('timestamp', '')}{c['oa_secret']}".encode()).hexdigest()
    if not _eq(headers.get("x-zevent-signature", "").replace("mac=", ""), mac):
        raise PermissionError("bad signature")
    if body.get("event_name") == "user_send_text":
        uid = (body.get("sender") or {}).get("id", "")
        config.spawn(_reply, "zalo", uid, "", uid, (body.get("message") or {}).get("text", ""), True, False)


def _zalo_send(c, chat, text, media):
    for part in ch.chunks(text, ch.LIMITS["zalo"]):
        out = _post("https://openapi.zalo.me/v3.0/oa/message/cs", headers={"access_token": c["access_token"]},
                    json={"recipient": {"user_id": chat}, "message": {"text": part}})
        if out.get("error"):
            raise RuntimeError(f"Zalo: {out.get('message')}")


# ---------- Nextcloud Talk (bots) ----------

def nextcloud_inbound(raw: bytes, headers: dict) -> None:
    c = _cfg("nextcloud")
    rnd = headers.get("x-nextcloud-talk-random", "")
    good = hmac.new(c["secret"].encode(), rnd.encode() + raw, hashlib.sha256).hexdigest()
    if not _eq(headers.get("x-nextcloud-talk-signature", "").lower(), good):
        raise PermissionError("bad signature")
    body = json.loads(raw)
    if body.get("type") != "Create":
        return
    actor = body.get("actor") or {}
    try:
        text = json.loads((body.get("object") or {}).get("content") or "{}").get("message", "")
    except ValueError:
        text = ""
    room = (body.get("target") or {}).get("id", "")
    config.spawn(_reply, "nextcloud", actor.get("id", ""), actor.get("name", ""), room, text, True, False)


def _nc_send(c, chat, text, media):
    base = c["server"].rstrip("/")
    _private_ok(base)
    for part in ch.chunks(text, ch.LIMITS["nextcloud"]):
        rnd = secrets.token_hex(32)
        sig = hmac.new(c["secret"].encode(), (rnd + part).encode(), hashlib.sha256).hexdigest()
        r = httpx.post(f"{base}/ocs/v2.php/apps/spreed/api/v1/bot/{chat}/message", timeout=30, json={"message": part},
                       headers={"OCS-APIRequest": "true", "X-Nextcloud-Talk-Bot-Random": rnd,
                                "X-Nextcloud-Talk-Bot-Signature": sig, "Accept": "application/json"})
        if r.status_code >= 400:
            raise RuntimeError(f"Nextcloud Talk: HTTP {r.status_code}")


# ---------- Synology Chat (chatbot integration) ----------

def synology_inbound(form: dict) -> None:
    c = _cfg("synology")
    if not _eq(form.get("token", ""), c["token"]):
        raise PermissionError("bad token")
    uid = str(form.get("user_id", ""))
    config.spawn(_reply, "synology", uid, form.get("username", ""), uid, form.get("text", ""), True, False)


def _syno_send(c, chat, text, media):
    url = c["incoming_url"]
    _private_ok(url)
    for part in ch.chunks(text, ch.LIMITS["synology"]):
        r = httpx.post(url, data={"payload": json.dumps({"text": part, "user_ids": [int(chat)]})}, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Synology Chat: HTTP {r.status_code}")


# ---------- Rocket.Chat (outgoing webhook + REST) ----------

def rocketchat_inbound(body: dict) -> None:
    c = _cfg("rocketchat")
    if not _eq(body.get("token", ""), c["token"]):
        raise PermissionError("bad token")
    if body.get("bot") or body.get("user_id") == c["user_id"]:
        return
    room = body.get("channel_id", "")
    is_dm = room.startswith(body.get("user_id", "~~")) or room.endswith(body.get("user_id", "~~"))
    me = c.get("user_id", "")
    text = body.get("text", "")
    config.spawn(_reply, "rocketchat", body.get("user_id", ""), body.get("user_name", ""), room, text, is_dm,
                 bool(re.search(r"@\w+", text)) and not is_dm and me != "")


def _rc_send(c, chat, text, media):
    base = c["server"].rstrip("/")
    _private_ok(base)
    for part in ch.chunks(text, ch.LIMITS["rocketchat"]):
        _post(f"{base}/api/v1/chat.postMessage", headers={"X-Auth-Token": c["auth_token"], "X-User-Id": c["user_id"]},
              json={"roomId": chat, "text": part})


# ---------- Zulip (event queue) ----------

def _zulip(c, method, path, **kw):
    site = c["site"].rstrip("/")
    _private_ok(site)
    r = httpx.request(method, f"{site}/api/v1{path}", auth=(c["email"], c["api_key"]), timeout=kw.pop("timeout", 30), **kw)
    data = r.json()
    if data.get("result") != "success":
        raise RuntimeError(f"Zulip: {data.get('msg')}")
    return data


def _zulip_send(c, chat, text, media):
    for part in ch.chunks(text, ch.LIMITS["zulip"]):
        if chat.startswith("stream:"):
            _, stream, topic = chat.split(":", 2)
            _zulip(c, "POST", "/messages", data={"type": "stream", "to": stream, "topic": topic, "content": part})
        else:
            _zulip(c, "POST", "/messages", data={"type": "private", "to": json.dumps([chat]), "content": part})


def zulip_message(c, msg: dict, flags: list) -> None:
    if msg.get("sender_email") == c["email"]:
        return
    text = re.sub(r"@\*\*[^*]+\*\*", "", msg.get("content", "")).strip()
    if msg.get("type") == "private":
        _reply("zulip", msg["sender_email"], msg.get("sender_full_name", ""), msg["sender_email"], text, True, False)
    else:
        chat = f"stream:{msg.get('display_recipient')}:{msg.get('subject')}"
        _reply("zulip", msg["sender_email"], msg.get("sender_full_name", ""), chat, text, False, "mentioned" in flags)


def _run_zulip(r):
    c = r.cfg
    q = _zulip(c, "POST", "/register", data={"event_types": json.dumps(["message"])})
    qid, last = q["queue_id"], q["last_event_id"]
    r.set("running", c["email"])
    while not r.stop.is_set():
        data = _zulip(c, "GET", "/events", params={"queue_id": qid, "last_event_id": last}, timeout=100)
        for ev in data.get("events", []):
            last = max(last, ev["id"])
            if ev.get("type") == "message":
                config.spawn(ch._safe, zulip_message, c, ev["message"], ev.get("flags", []))


# ---------- Viber ----------

def viber_inbound(raw: bytes, headers: dict) -> None:
    c = _cfg("viber")
    good = hmac.new(c["auth_token"].encode(), raw, hashlib.sha256).hexdigest()
    if not _eq(headers.get("x-viber-content-signature", ""), good):
        raise PermissionError("bad signature")
    body = json.loads(raw)
    if body.get("event") == "message" and (body.get("message") or {}).get("type") == "text":
        s = body.get("sender") or {}
        config.spawn(_reply, "viber", s.get("id", ""), s.get("name", ""), s.get("id", ""), body["message"].get("text", ""), True, False)


def _viber_send(c, chat, text, media):
    for part in ch.chunks(text, ch.LIMITS["viber"]):
        out = _post("https://chatapi.viber.com/pa/send_message", headers={"X-Viber-Auth-Token": c["auth_token"]},
                    json={"receiver": chat, "type": "text", "text": part, "sender": {"name": c.get("bot_name") or "Brain"}})
        if out.get("status"):
            raise RuntimeError(f"Viber: {out.get('status_message')}")


# ---------- Facebook Messenger and Instagram (Meta) ----------

def meta_verify(name: str, q: dict) -> str | None:
    c = _cfg(name)
    if q.get("hub.mode") == "subscribe" and _eq(q.get("hub.verify_token", ""), c["verify_token"]):
        return q.get("hub.challenge", "")
    return None


def meta_inbound(name: str, raw: bytes, headers: dict) -> None:
    c = _cfg(name)
    good = "sha256=" + hmac.new(c["app_secret"].encode(), raw, hashlib.sha256).hexdigest()
    if not _eq(headers.get("x-hub-signature-256", ""), good):
        raise PermissionError("bad signature")
    for entry in json.loads(raw).get("entry", []):
        for ev in entry.get("messaging", []):
            msg = ev.get("message") or {}
            if msg.get("text") and not msg.get("is_echo"):
                sid = (ev.get("sender") or {}).get("id", "")
                config.spawn(_reply, name, sid, "", sid, msg["text"], True, False)


def _meta_send(name):
    def send(c, chat, text, media):
        for part in ch.chunks(text, ch.LIMITS[name]):
            _post("https://graph.facebook.com/v21.0/me/messages", params={"access_token": c["page_token"]},
                  json={"recipient": {"id": chat}, "message": {"text": part}, "messaging_type": "RESPONSE"})
    return send


# ---------- Webex ----------

def webex_inbound(raw: bytes, headers: dict) -> None:
    c = _cfg("webex")
    if c["secret"]:
        good = hmac.new(c["secret"].encode(), raw, hashlib.sha1).hexdigest()
        if not _eq(headers.get("x-spark-signature", ""), good):
            raise PermissionError("bad signature")
    data = (json.loads(raw).get("data") or {})
    mid = data.get("id")
    if not mid:
        return
    config.spawn(_webex_fetch, c, mid)


def _webex_fetch(c, mid):
    h = {"Authorization": f"Bearer {c['bot_token']}"}
    m = httpx.get(f"https://webexapis.com/v1/messages/{mid}", headers=h, timeout=20).json()
    me = httpx.get("https://webexapis.com/v1/people/me", headers=h, timeout=20).json()
    if m.get("personId") == me.get("id"):
        return
    is_dm = m.get("roomType") == "direct"
    _reply("webex", m.get("personEmail", ""), m.get("personEmail", ""), m.get("roomId", ""),
           (m.get("text") or "").replace(me.get("displayName", "~~"), "").strip(), is_dm, not is_dm)


def _webex_send(c, chat, text, media):
    for part in ch.chunks(text, ch.LIMITS["webex"]):
        _post("https://webexapis.com/v1/messages", headers={"Authorization": f"Bearer {c['bot_token']}"},
              json={"roomId": chat, "markdown": part})


# ---------- Nostr (encrypted direct messages, NIP-04) ----------

def _nostr_keys(c) -> tuple:
    from coincurve import PrivateKey
    raw = c["private_key"].strip()
    if raw.startswith("nsec"):
        raw = _bech32_decode(raw).hex()
    pk = PrivateKey(bytes.fromhex(raw))
    return pk, pk.public_key.format(compressed=True)[1:].hex()


def _bech32_decode(s: str) -> bytes:
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    data = [charset.index(x) for x in s.lower().split("1", 1)[1][:-6]]
    acc, bits, out = 0, 0, []
    for v in data:
        acc, bits = (acc << 5) | v, bits + 5
        while bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


def _shared(pk, their_hex: str) -> bytes:
    from coincurve import PublicKey
    return PublicKey(b"\x02" + bytes.fromhex(their_hex)).multiply(pk.secret).format(compressed=True)[1:]


def nip04_encrypt(pk, their_hex: str, text: str) -> str:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    iv = os.urandom(16)
    p = padding.PKCS7(128).padder()
    data = p.update(text.encode()) + p.finalize()
    e = Cipher(algorithms.AES(_shared(pk, their_hex)), modes.CBC(iv)).encryptor()
    return base64.b64encode(e.update(data) + e.finalize()).decode() + "?iv=" + base64.b64encode(iv).decode()


def nip04_decrypt(pk, their_hex: str, content: str) -> str:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    ct, iv = content.split("?iv=")
    d = Cipher(algorithms.AES(_shared(pk, their_hex)), modes.CBC(base64.b64decode(iv))).decryptor()
    u = padding.PKCS7(128).unpadder()
    return (u.update(d.update(base64.b64decode(ct)) + d.finalize()) + u.finalize()).decode()


def nostr_event(pk, pub: str, kind: int, content: str, tags: list) -> dict:
    ev = {"pubkey": pub, "created_at": int(time.time()), "kind": kind, "tags": tags, "content": content}
    ev["id"] = hashlib.sha256(json.dumps([0, pub, ev["created_at"], kind, tags, content], separators=(",", ":"),
                                         ensure_ascii=False).encode()).hexdigest()
    ev["sig"] = pk.sign_schnorr(bytes.fromhex(ev["id"])).hex()
    return ev


def nostr_verify(ev: dict) -> bool:
    from coincurve import PublicKeyXOnly
    want = hashlib.sha256(json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
                                     separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    try:
        return want == ev["id"] and PublicKeyXOnly(bytes.fromhex(ev["pubkey"])).verify(bytes.fromhex(ev["sig"]), bytes.fromhex(want))
    except Exception:
        return False


_nostr_out: dict = {}


def _relays(c) -> list[str]:
    return [x for x in re.split(r"[\s,]+", c.get("relays") or "") if x.startswith("wss://")][:6]


def _nostr_send(c, chat, text, media):
    from websockets.sync.client import connect
    pk, pub = _nostr_keys(c)
    ev = nostr_event(pk, pub, 4, nip04_encrypt(pk, chat, text[:ch.LIMITS["nostr"]]), [["p", chat]])
    sent = 0
    for url in _relays(c):
        try:
            with connect(url, open_timeout=10) as ws:
                ws.send(json.dumps(["EVENT", ev]))
                sent += 1
        except Exception:
            pass
    if not sent:
        raise RuntimeError("No Nostr relay accepted the message.")


def nostr_incoming(c, pk, pub, ev: dict) -> None:
    if ev.get("kind") != 4 or ev.get("pubkey") == pub or not nostr_verify(ev):
        return
    if not any(t[:2] == ["p", pub] for t in ev.get("tags", [])):
        return
    try:
        text = nip04_decrypt(pk, ev["pubkey"], ev["content"])
    except Exception:
        return
    _reply("nostr", ev["pubkey"], ev["pubkey"][:12], ev["pubkey"], text, True, False)


def _run_nostr(r):
    from websockets.sync.client import connect
    c = r.cfg
    pk, pub = _nostr_keys(c)
    relays = _relays(c)
    if not relays:
        raise RuntimeError("Add at least one relay (wss://…).")
    seen = set()
    with connect(relays[0], open_timeout=15) as ws:
        ws.send(json.dumps(["REQ", "brain", {"kinds": [4], "#p": [pub], "since": int(time.time())}]))
        r.set("running", f"npub {pub[:12]}… on {relays[0]}")
        while not r.stop.is_set():
            try:
                m = json.loads(ws.recv(timeout=10))
            except TimeoutError:
                continue
            if m[0] == "EVENT" and m[2]["id"] not in seen:
                seen.add(m[2]["id"])
                config.spawn(ch._safe, nostr_incoming, c, pk, pub, m[2])


# ---------- Wire up ----------

ch.EXTRA_SENDERS.update({
    "wechat": _wx_send, "wecom": _wecom_send, "feishu": _fs_send, "dingtalk": _ding_send, "qq": _qq_send,
    "zalo": _zalo_send, "nextcloud": _nc_send, "synology": _syno_send, "rocketchat": _rc_send, "zulip": _zulip_send,
    "viber": _viber_send, "messenger": _meta_send("messenger"), "instagram": _meta_send("instagram"),
    "webex": _webex_send, "nostr": _nostr_send,
})
ch.RUNNERS.update({"qq": _run_qq, "zulip": _run_zulip, "nostr": _run_nostr})


# ---------- One-click webhook registration (for services that need an API call, not a settings page) ----------

def register_webhook(name: str, url: str) -> str:
    c = _cfg(name)
    if name == "viber":
        r = httpx.post("https://chatapi.viber.com/pa/set_webhook", headers={"X-Viber-Auth-Token": c["auth_token"]},
                       json={"url": url, "event_types": ["message", "conversation_started"], "send_name": True}, timeout=20).json()
        if r.get("status") != 0:
            raise RuntimeError(f"Viber said: {r.get('status_message', r)}")
        return "Viber will now send messages to your Brain."
    if name == "webex":
        if not c.get("secret"):
            raise RuntimeError("Enter a webhook secret first (any long random word).")
        h = {"Authorization": f"Bearer {c['bot_token']}"}
        for w in httpx.get("https://webexapis.com/v1/webhooks", headers=h, timeout=20).json().get("items", []):
            if w.get("name") == "ai-brain":
                httpx.delete(f"https://webexapis.com/v1/webhooks/{w['id']}", headers=h, timeout=20)
        r = httpx.post("https://webexapis.com/v1/webhooks", headers=h, timeout=20,
                       json={"name": "ai-brain", "targetUrl": url, "resource": "messages", "event": "created", "secret": c["secret"]})
        if r.status_code >= 300:
            raise RuntimeError(f"Webex said {r.status_code}: {r.text[:200]}")
        return "Webex will now send messages to your Brain."
    raise RuntimeError("This channel is set up on the service's own website.")
