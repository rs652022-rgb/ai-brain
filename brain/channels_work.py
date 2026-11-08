"""Workplace chat apps: Microsoft Teams (outgoing webhook) and Google Chat (HTTP app).

Both expect the answer in the reply to their own request, within a few seconds. The Brain answers
there when it is quick; when it needs longer, it says so and posts the answer through the space's
incoming webhook (if you added one).
"""
import base64
import hashlib
import hmac
import html
import re
import threading

import httpx

from . import channels as ch
from . import config
from .channels_more import _private_ok

NEW = {
    #  channel   fields                                secrets                              needed               limit
    "teams":   (("security_token", "incoming_url"), ("security_token", "incoming_url"), ("security_token",), 20000),
    "gchat":   (("audience", "incoming_url"), ("incoming_url",), ("audience",), 4000),
}
for name, (fields, secret, need, limit) in NEW.items():
    if name not in ch.CHANNELS:
        ch.CHANNELS.append(name)
    ch.FIELDS[name] = ("enabled",) + fields
    ch.SECRETS[name] = secret
    ch.NEEDS[name] = list(need)
    ch.LIMITS[name] = limit
    ch.WEBHOOK_CHANNELS.add(name)

TEAMS_WAIT = 4.0          # Teams gives up after 5 seconds
GCHAT_WAIT = 25.0         # Google Chat waits 30 seconds
GOOGLE_CHAT_ISSUER = "chat@system.gserviceaccount.com"
LATER = "Thinking… I'll post the answer here in a moment."


def _cfg(name: str) -> dict:
    return ch.get_config()[name]


def plain(text: str) -> str:
    """Message text without @mentions and HTML tags."""
    text = re.sub(r"<at>.*?</at>", " ", text or "", flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def answer_within(seconds: float, channel: str, sender: str, name: str, chat: str, text: str,
                  is_dm: bool, mentioned: bool) -> str | None:
    """The reply if it is ready in time. Otherwise None, and the reply is posted later with ch.send."""
    state = {"out": None, "late": False}
    lock, ready = threading.Lock(), threading.Event()

    def work():
        try:
            out = ch.handle(channel, sender, name, chat, text, is_dm, mentioned)
        except Exception:
            ch.log.exception("%s reply failed", channel)
            out = None
        with lock:
            state["out"] = out
            late = state["late"]
            ready.set()
        if late and out and _cfg(channel).get("incoming_url"):
            try:
                ch.send(channel, chat, out["reply"], out.get("media"))
            except Exception:
                ch.log.exception("%s late reply failed", channel)

    config.spawn(work)
    finished = ready.wait(seconds)
    with lock:
        if not finished and not ready.is_set():
            state["late"] = True
            return None
    out = state["out"]
    return out["reply"] if out else ""


# ---------- Microsoft Teams (outgoing webhook) ----------

def teams_signature_ok(token: str, raw: bytes, header: str) -> bool:
    if not token or not header.startswith("HMAC "):
        return False
    try:
        key = base64.b64decode(token)
    except ValueError:
        return False
    good = base64.b64encode(hmac.new(key, raw, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(good, header[5:].strip())


def teams_inbound(raw: bytes, headers: dict, activity: dict) -> dict:
    c = _cfg("teams")
    if not teams_signature_ok(c["security_token"], raw, headers.get("authorization", "")):
        raise PermissionError("bad signature")
    who = activity.get("from") or {}
    sender = who.get("aadObjectId") or who.get("id", "")
    chat = (activity.get("conversation") or {}).get("id", "")
    # Outgoing webhooks run only when someone @mentions the Brain in a team channel.
    reply = answer_within(TEAMS_WAIT, "teams", sender, who.get("name", ""), chat, plain(activity.get("text", "")),
                          False, True)
    if reply is None:
        reply = LATER if c.get("incoming_url") else "Still thinking; ask me again in a minute."
    return {"type": "message", "text": reply}


def _teams_send(c, chat, text, media):
    url = c.get("incoming_url")
    if not url:
        raise RuntimeError("Teams: add an incoming webhook URL to send messages.")
    _private_ok(url)
    for part in ch.chunks(text, ch.LIMITS["teams"]):
        card = {"type": "AdaptiveCard", "version": "1.4",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "body": [{"type": "TextBlock", "text": part, "wrap": True}]}
        r = httpx.post(url, timeout=30, json={"type": "message", "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]})
        if r.status_code >= 400:
            raise RuntimeError(f"Teams: HTTP {r.status_code}")


# ---------- Google Chat (HTTP endpoint app) ----------

def gchat_token_ok(audience: str, auth_header: str) -> bool:
    """Google signs each request; Google checks the token for us. The app's 'Authentication audience'
    must be set to 'HTTP endpoint URL', the same address as entered here."""
    if not audience or not auth_header.startswith("Bearer "):
        return False
    try:
        r = httpx.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": auth_header[7:].strip()},
                      timeout=10)
    except httpx.HTTPError:
        return False
    if r.status_code != 200:
        return False
    info = r.json()
    return (info.get("email") == GOOGLE_CHAT_ISSUER and str(info.get("email_verified")).lower() == "true"
            and hmac.compare_digest(str(info.get("aud", "")), audience))


def gchat_inbound(headers: dict, event: dict) -> dict:
    c = _cfg("gchat")
    if not gchat_token_ok(c["audience"], headers.get("authorization", "")):
        raise PermissionError("bad token")
    kind = event.get("type")
    if kind == "ADDED_TO_SPACE":
        return {"text": "Hi! Mention me with a question."}
    if kind != "MESSAGE":
        return {}
    msg = event.get("message") or {}
    space = event.get("space") or msg.get("space") or {}
    user = event.get("user") or msg.get("sender") or {}
    is_dm = space.get("type") == "DM" or space.get("spaceType") == "DIRECT_MESSAGE" or bool(space.get("singleUserBotDm"))
    text = (msg.get("argumentText") or msg.get("text") or "").strip()
    reply = answer_within(GCHAT_WAIT, "gchat", user.get("name", ""), user.get("displayName", ""),
                          space.get("name", ""), text, is_dm, True)   # in spaces it only gets @mentions
    if reply is None:
        reply = LATER if c.get("incoming_url") and not is_dm else "Still thinking; ask me again in a minute."
    return {"text": reply} if reply else {}


def _gchat_send(c, chat, text, media):
    url = c.get("incoming_url")
    if not url:
        raise RuntimeError("Google Chat: add a space webhook URL to send messages.")
    _private_ok(url)
    for part in ch.chunks(text, ch.LIMITS["gchat"]):
        r = httpx.post(url, timeout=30, json={"text": part})
        if r.status_code >= 400:
            raise RuntimeError(f"Google Chat: HTTP {r.status_code}")


ch.EXTRA_SENDERS.update({"teams": _teams_send, "gchat": _gchat_send})
