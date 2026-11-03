"""Master Control: names, wake/rest commands, switches, PIN and app lock."""
import importlib

import pytest


@pytest.fixture()
def b(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_NO_SCHEDULER", "1")
    from brain import config
    importlib.reload(config)
    from brain import settings, master, llm, db, memory, web, knowledge, voice, core, identity, interview, importer, server
    for m in (settings, master, llm, db, memory, web, knowledge, voice, core, identity, interview, importer, server):
        importlib.reload(m)
    db.init(); knowledge.init()
    calls = []
    monkeypatch.setattr(llm, "chat", lambda system, msgs, **kw: (calls.append(system) or "answer", "fake"))
    monkeypatch.setattr(llm, "embed", lambda t: None)
    monkeypatch.setattr(llm, "private_json", lambda s, p: None)
    monkeypatch.setattr(master.time, "sleep", lambda s: None)
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        yield type("B", (), dict(c=c, master=master, core=core, settings=settings, voice=voice, calls=calls,
                                 knowledge=knowledge))


def test_rename_brain_and_owner_reaches_prompt_and_header(b):
    r = b.c.put("/api/master", json={"changes": {"brain_name": "Jarvis", "owner_name": "Rahul S", "greeting": "Namaste {first}!"}})
    assert r.json()["brain_name"] == "Jarvis" and r.json()["greeting"] == "Namaste Rahul!"
    b.core.think("hello", background_extract=False)
    assert "Your name is Jarvis" in b.calls[-1] and "Rahul S" in b.calls[-1]
    assert b.c.get("/api/status").json()["brain_name"] == "Jarvis"


def test_rest_and_wake_commands(b):
    b.c.put("/api/master", json={"changes": {"wake_words": "hey jarvis, utho jarvis", "sleep_words": ["go to sleep"]}})
    r = b.core.think("Go to sleep", background_extract=False)
    assert r["action"] == "rest" and b.master.get()["resting"]
    n = len(b.calls)
    r = b.core.think("what's the time", background_extract=False)
    assert r["action"] == "resting" and len(b.calls) == n            # no model call while resting
    assert b.knowledge.run_due() == []                               # no background learning while resting
    r = b.core.think("Utho Jarvis", background_extract=False)
    assert r["action"] == "wake" and not b.master.get()["resting"]
    r = b.core.think("Hey Jarvis, plan my day", background_extract=False)
    assert r["action"] == "chat" and b.calls[-1]                      # wake word stripped, real answer
    assert b.voice.cfg()["wake_word"] == "hey jarvis"


def test_web_switch_turns_off_web_lookups(b, monkeypatch):
    seen = []
    monkeypatch.setattr(b.knowledge, "web_answer_context", lambda q: seen.append(q) or [])
    b.c.put("/api/master", json={"changes": {"features": {"web": False}}})
    b.core.think("search the web for groq pricing", background_extract=False)
    assert seen == []


def test_settings_tab_save_keeps_master(b):
    b.c.put("/api/master", json={"changes": {"brain_name": "Jarvis"}})
    body = b.c.get("/api/settings").json()["settings"]
    assert "master" not in body                                      # PIN data never sent to the page
    b.c.put("/api/settings", json=body)
    assert b.master.brain_name() == "Jarvis"


def test_pin_protects_master_and_lock_app(b):
    assert b.c.post("/api/master/pin", json={"new_pin": "12"}).status_code == 400   # too short
    assert b.c.post("/api/master/pin", json={"new_pin": "4821"}).status_code == 200
    s = b.master.get()
    assert s["pin_hash"] and "4821" not in str(s)                     # only a hash is stored
    b.c.put("/api/master", json={"changes": {"lock_app": True}})
    b.c.post("/api/master/lock")
    assert b.c.put("/api/master", json={"changes": {"brain_name": "X"}}).status_code == 401
    assert b.c.get("/api/memories").status_code == 401                 # whole app locked
    assert b.c.get("/api/master/public").json()["unlocked"] is False   # lock screen can still load
    assert b.c.post("/api/master/unlock", json={"pin": "0000"}).status_code == 401
    assert b.c.post("/api/master/unlock", json={"pin": "4821"}).status_code == 200
    assert b.c.get("/api/memories").status_code == 200
    assert b.c.put("/api/master", json={"changes": {"brain_name": "Friday"}}).json()["brain_name"] == "Friday"


def test_too_many_wrong_pins_pause_guessing(b):
    b.c.post("/api/master/pin", json={"new_pin": "4821"})
    for _ in range(5):
        b.c.post("/api/master/unlock", json={"pin": "0000"})
    assert b.c.post("/api/master/unlock", json={"pin": "4821"}).status_code == 429


def test_export_leaves_out_keys_and_pin(b):
    s = b.settings.load(); s["providers"]["gemini"] = {"api_key": "AIzaSECRETKEY123"}; b.settings.save(s)
    b.c.post("/api/master/pin", json={"new_pin": "4821"})
    b.c.post("/api/memories", json={"content": "My brother Amit lives in Pune."})
    r = b.c.get("/api/master/export")
    assert r.status_code == 200 and "Amit lives in Pune" in r.text
    assert "AIzaSECRETKEY123" not in r.text and "pin_hash" not in r.text


def test_cannot_lock_app_without_pin(b):
    assert b.c.put("/api/master", json={"changes": {"lock_app": True}}).json()["lock_app"] is False
