"""Gmail push alerts: Gmail tells your Brain the moment an email arrives, instead of the Brain
checking every two minutes. Uses Google's own free Gmail API "watch" with Google Cloud Pub/Sub.

Set up once (free; your own Google Cloud project, so your mail goes only between Google and your Brain):
1. console.cloud.google.com → new project → enable "Gmail API" and "Cloud Pub/Sub API".
2. Pub/Sub → Topics → create "brain-gmail". On the topic, add principal
   gmail-api-push@system.gserviceaccount.com with role "Pub/Sub Publisher".
3. On the topic, create a Push subscription to the push URL shown in the Brain.
4. APIs & Services → Credentials → OAuth client ID (Web application) with the redirect URL shown in the
   Brain. Copy the client ID and secret into the Brain, then press "Connect Gmail".
The Brain asks only for read access (gmail.readonly). Your Brain must be online over HTTPS.
It renews the watch every few days by itself (Google ends it after 7 days).
"""
import base64
import email
import json
import secrets
import time
import urllib.parse

import httpx

from . import config, db, settings, vault

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
_states: dict[str, tuple[str, float]] = {}


class GmailError(RuntimeError):
    pass


def cfg() -> dict:
    return {"client_id": "", "client_secret": "", "topic": "", "refresh": "", "history_id": "", "expires": 0,
            "address": "", "secret": "", "enabled": False} | (settings.load().get("gmail") or {})


def _store(c: dict) -> None:
    data = settings.load()
    data["gmail"] = c
    settings.save(data)


def push_secret() -> str:
    c = cfg()
    if not c["secret"]:
        c["secret"] = secrets.token_urlsafe(24)
        _store(c)
    return c["secret"]


def public(base_url: str) -> dict:
    c = cfg()
    return {"client_id": c["client_id"], "has_secret": bool(c["client_secret"]), "topic": c["topic"],
            "connected": bool(c["refresh"]), "address": c["address"], "enabled": c["enabled"],
            "watch_until": time.strftime("%Y-%m-%d %H:%M", time.localtime(c["expires"] / 1000)) if c["expires"] else "",
            "push_url": f"{base_url}/api/gmail/push/{push_secret()}", "redirect_url": f"{base_url}/api/gmail/callback"}


def save(changes: dict) -> None:
    c = cfg()
    for k in ("client_id", "topic"):
        if k in changes:
            c[k] = str(changes[k]).strip()[:300]
    if changes.get("client_secret"):
        c["client_secret"] = vault._fernet().encrypt(str(changes["client_secret"]).strip().encode()).decode()
    if "enabled" in changes:
        c["enabled"] = bool(changes["enabled"])
    if c["topic"] and not c["topic"].startswith("projects/"):
        raise GmailError("The topic looks like projects/<your-project>/topics/brain-gmail.")
    _store(c)
    db.audit("owner", "gmail.settings", enabled=c["enabled"])


def _secret(c: dict) -> str:
    return vault._fernet().decrypt(c["client_secret"].encode()).decode() if c["client_secret"] else ""


def auth_url(redirect_url: str) -> str:
    c = cfg()
    if not (c["client_id"] and c["client_secret"]):
        raise GmailError("Enter your OAuth client ID and secret first.")
    state = secrets.token_urlsafe(20)
    _states[state] = (config.user_key(), time.time())
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect_url, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state, "include_granted_scopes": "true"})


def state_owner(state: str) -> str | None:
    found = _states.pop(state or "", None)
    return found[0] if found and time.time() - found[1] < 900 else None


def finish(code: str, redirect_url: str) -> str:
    c = cfg()
    r = httpx.post("https://oauth2.googleapis.com/token", timeout=20, data={
        "code": code, "client_id": c["client_id"], "client_secret": _secret(c), "redirect_uri": redirect_url,
        "grant_type": "authorization_code"})
    if r.status_code != 200 or "refresh_token" not in r.json():
        raise GmailError(f"Google didn't give access: {r.text[:200]}")
    c["refresh"] = vault._fernet().encrypt(r.json()["refresh_token"].encode()).decode()
    c["enabled"] = True
    _store(c)
    prof = _api("GET", "/profile")
    c = cfg()
    c["address"] = prof.get("emailAddress", "")
    _store(c)
    db.audit("owner", "gmail.connected")
    if c["topic"]:
        watch()
    return c["address"]


def _token() -> str:
    c = cfg()
    if not c["refresh"]:
        raise GmailError("Gmail isn't connected.")
    r = httpx.post("https://oauth2.googleapis.com/token", timeout=20, data={
        "client_id": c["client_id"], "client_secret": _secret(c), "grant_type": "refresh_token",
        "refresh_token": vault._fernet().decrypt(c["refresh"].encode()).decode()})
    if r.status_code != 200:
        raise GmailError(f"Google refused the saved access (connect Gmail again): {r.text[:150]}")
    return r.json()["access_token"]


def _api(method: str, path: str, **kw) -> dict:
    r = httpx.request(method, API + path, headers={"Authorization": f"Bearer {_token()}"}, timeout=25, **kw)
    if r.status_code >= 400:
        raise GmailError(f"Gmail said {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def watch() -> dict:
    c = cfg()
    if not c["topic"]:
        raise GmailError("Enter your Pub/Sub topic first.")
    out = _api("POST", "/watch", json={"topicName": c["topic"], "labelIds": ["INBOX"], "labelFilterBehavior": "INCLUDE"})
    c = cfg()
    c["history_id"] = str(out.get("historyId", c["history_id"]))
    c["expires"] = int(out.get("expiration", 0))
    _store(c)
    db.audit("brain", "gmail.watch", until=c["expires"])
    return out


def renew_if_needed() -> None:
    """Called by the scheduler: renew a day before Google ends the watch."""
    c = cfg()
    if c["enabled"] and c["refresh"] and c["topic"] and (not c["expires"] or c["expires"] / 1000 - time.time() < 86400):
        watch()


def disconnect() -> None:
    c = cfg()
    if c["refresh"]:
        try:
            _api("POST", "/stop")
        except Exception:
            pass
    c.update({"refresh": "", "history_id": "", "expires": 0, "address": "", "enabled": False})
    _store(c)
    db.audit("owner", "gmail.disconnected")


def decode_push(body: dict) -> dict:
    data = (body.get("message") or {}).get("data") or ""
    try:
        return json.loads(base64.b64decode(data + "=" * (-len(data) % 4)))
    except Exception:
        return {}


def handle_push(note: dict) -> int:
    """New history from Gmail: read each new inbox email and decide whether it needs the owner."""
    from . import master, workflows
    c = cfg()
    if not (c["enabled"] and c["refresh"]) or master.get()["resting"]:
        return 0
    if note.get("emailAddress") and c["address"] and note["emailAddress"].lower() != c["address"].lower():
        return 0
    start = c["history_id"]
    if not start:
        c["history_id"] = str(note.get("historyId", ""))
        _store(c)
        return 0
    hist = _api("GET", "/history", params={"startHistoryId": start, "historyTypes": "messageAdded", "labelId": "INBOX"})
    ids = []
    for h in hist.get("history", []):
        for m in h.get("messagesAdded", []):
            mid = m.get("message", {}).get("id")
            if mid and mid not in ids and "INBOX" in m.get("message", {}).get("labelIds", ["INBOX"]):
                ids.append(mid)
    c = cfg()
    c["history_id"] = str(hist.get("historyId") or note.get("historyId") or start)
    _store(c)
    n = 0
    for mid in ids[:10]:
        raw = _api("GET", f"/messages/{mid}", params={"format": "raw"}).get("raw", "")
        m = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        from .channels import _plain_body
        workflows.handle_email(m.get("From", ""), m.get("Subject", ""), _plain_body(m))
        n += 1
    db.audit("brain", "gmail.push", new=n)
    return n
