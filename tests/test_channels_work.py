"""Microsoft Teams and Google Chat: signed requests only, answers in the reply or posted later."""
import base64
import hashlib
import hmac
import importlib
import json
import time

import pytest

KEY = base64.b64encode(b"teams-shared-secret-for-tests-01").decode()


@pytest.fixture()
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_NO_SCHEDULER", "1")
    from brain import config
    importlib.reload(config)
    from brain import settings, llm, db, memory, web, knowledge, voice, core, identity, interview, importer, server
    for m in (settings, llm, db, memory, web, knowledge, voice, core, identity, interview, importer, server):
        importlib.reload(m)
    db.init(); knowledge.init()
    monkeypatch.setattr(llm, "embed", lambda t: None)
    monkeypatch.setattr(llm, "private_json", lambda s, p: None)
    from brain import channels as ch, channels_work as cw
    ch.save_config({"teams": {"enabled": True, "security_token": KEY, "incoming_url": "https://hooks.example.com/t"},
                    "gchat": {"enabled": True, "audience": "https://brain.example.com/in"}})
    from fastapi.testclient import TestClient
    return type("W", (), dict(c=TestClient(server.app), ch=ch, cw=cw, llm=llm, mp=monkeypatch,
                              url=lambda name: f"/api/channels/in/{name}/{ch.inbox_secret()}"))


def _signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = base64.b64encode(hmac.new(base64.b64decode(KEY), raw, hashlib.sha256).digest()).decode()
    return raw, {"Authorization": f"HMAC {sig}", "Content-Type": "application/json"}


def _activity(text="<at>Brain</at> what is on today?"):
    return {"type": "message", "text": text, "from": {"aadObjectId": "aad-1", "name": "Asha"},
            "conversation": {"id": "19:team-chan"}}


def test_mentions_and_tags_are_removed(w):
    assert w.cw.plain("<at>Brain</at> hi <b>there</b> &amp; you") == "hi there & you"


def test_teams_rejects_a_bad_signature(w):
    raw, h = _signed(_activity())
    h["Authorization"] = "HMAC bm9wZQ=="
    assert w.c.post(w.url("teams"), content=raw, headers=h).status_code == 403


def test_teams_answers_an_approved_person_in_the_reply(w):
    w.ch.add_allowed("teams", "aad-1", "Asha")
    w.mp.setattr(w.llm, "chat", lambda *a, **k: ("Two meetings today.", "fake"))
    raw, h = _signed(_activity())
    r = w.c.post(w.url("teams"), content=raw, headers=h)
    assert r.status_code == 200 and r.json() == {"type": "message", "text": "Two meetings today."}


def test_teams_ignores_strangers(w):
    raw, h = _signed(_activity())
    assert w.c.post(w.url("teams"), content=raw, headers=h).json()["text"] == ""


def test_slow_answer_is_posted_later_through_the_webhook(w):
    w.ch.add_allowed("teams", "aad-1", "Asha")
    w.mp.setattr(w.cw, "TEAMS_WAIT", 0.2)
    sent = []
    w.mp.setattr(w.cw.ch, "handle", lambda *a: (time.sleep(0.6), {"reply": "Done now.", "media": []})[1])
    w.mp.setattr(w.cw.ch, "send", lambda channel, chat, text, media=None: sent.append((channel, chat, text)))
    raw, h = _signed(_activity())
    assert w.c.post(w.url("teams"), content=raw, headers=h).json()["text"] == w.cw.LATER
    for _ in range(30):
        if sent:
            break
        time.sleep(0.1)
    assert sent == [("teams", "19:team-chan", "Done now.")]


def test_gchat_needs_a_google_signed_token(w):
    w.mp.setattr(w.cw, "gchat_token_ok", lambda aud, hdr: False)
    r = w.c.post(w.url("gchat"), json={"type": "MESSAGE"}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 403


def test_gchat_token_checks_issuer_and_audience(w):
    class R:
        status_code = 200
        def __init__(self, info): self.info = info
        def json(self): return self.info
    good = {"email": w.cw.GOOGLE_CHAT_ISSUER, "email_verified": "true", "aud": "https://brain.example.com/in"}
    for info, ok in ((good, True), (good | {"aud": "https://other"}, False), (good | {"email": "x@evil.com"}, False)):
        w.mp.setattr(w.cw.httpx, "get", lambda *a, **k: R(info))
        assert w.cw.gchat_token_ok("https://brain.example.com/in", "Bearer tok") is ok
    assert w.cw.gchat_token_ok("https://brain.example.com/in", "") is False


def test_gchat_dm_answers_in_the_reply(w):
    w.mp.setattr(w.cw, "gchat_token_ok", lambda aud, hdr: True)
    w.ch.add_allowed("gchat", "users/42", "Ravi")
    w.mp.setattr(w.llm, "chat", lambda *a, **k: ("Namaste Ravi!", "fake"))
    event = {"type": "MESSAGE", "space": {"name": "spaces/dm1", "type": "DM"},
             "user": {"name": "users/42", "displayName": "Ravi"}, "message": {"text": "hello"}}
    r = w.c.post(w.url("gchat"), json=event, headers={"Authorization": "Bearer x"})
    assert r.json() == {"text": "Namaste Ravi!"}
