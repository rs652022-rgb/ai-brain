"""End-to-end tests with fake models, so they run without API keys or Ollama."""
import importlib
import sqlite3

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    from brain import config
    importlib.reload(config)
    from brain import db, identity, llm, memory, core, interview, importer, server
    for mod in (db, identity, llm, memory, core, interview, importer, server):
        importlib.reload(mod)

    calls = {}

    def fake_chat(system, messages, private=False, **kw):
        calls["system"], calls["messages"], calls["private"] = system, messages, private
        return "I remember Amit is in Pune (#1).", "fake-model"

    def fake_json(system, prompt):
        if "durable facts" in system:
            return {"facts": [{"type": "semantic", "content": "Rahul is planning a trip to Goa.", "reason": "plan"}]}
        return {"summary": "s", "tags": ["family"], "people": ["Amit"], "mood": ""}

    monkeypatch.setattr(server.llm, "chat", fake_chat)
    monkeypatch.setattr(server.llm, "private_json", fake_json)
    monkeypatch.setattr(server.llm, "embed", lambda text: None)

    from fastapi.testclient import TestClient
    with TestClient(server.app) as client:
        yield client, calls, config


def test_memory_chat_and_approval_flow(app):
    client, calls, _ = app
    r = client.post("/api/memories", json={"content": "My brother Amit lives in Pune.", "type": "semantic"})
    assert r.status_code == 200 and r.json()["people"] == ["Amit"]

    client.post("/api/memories", json={"content": "My bank PIN hint", "sensitivity": "sealed"})

    r = client.post("/api/chat", json={"message": "Where does my brother live? I am also planning a Goa trip."})
    body = r.json()
    assert r.status_code == 200, body
    assert body["cited"] == [1]
    assert "Amit lives in Pune" in calls["system"]
    assert "bank PIN hint" not in calls["system"]  # sealed memories stay out by default
    assert "<safety_core>" in calls["system"]
    assert calls["private"] is False

    import time; time.sleep(0.3)                     # background extraction
    pending = client.get("/api/pending").json()
    assert pending and "Goa" in pending[0]["content"]
    assert client.get("/api/memories?q=Goa").json() == []   # not saved until approved
    client.post(f"/api/pending/{pending[0]['id']}/approve", json={})
    assert client.get("/api/memories?q=Goa").json()


def test_sealed_memories_route_to_local_model(app):
    client, calls, _ = app
    client.post("/api/memories", json={"content": "Private worry about the loan for the office", "sensitivity": "sealed"})
    client.post("/api/chat", json={"message": "what about the loan for the office", "use_sealed": True})
    assert calls["private"] is True


def test_forget_and_audit_is_append_only(app):
    client, _, config = app
    m = client.post("/api/memories", json={"content": "Something to forget later"}).json()
    assert client.delete(f"/api/memories/{m['id']}").status_code == 200
    assert client.get("/api/memories?q=forget").json() == []
    actions = [a["action"] for a in client.get("/api/audit").json()]
    assert "memory.forget" in actions
    conn = sqlite3.connect(config.DB_PATH)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM audit_log")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit_log SET action='x'")


def test_identity_versions_and_honesty_lock(app):
    client, calls, _ = app
    y = client.get("/api/identity").json()["yaml"]
    r = client.put("/api/identity", json={"yaml": y.replace("A mirror of its owner", "Rahul 2.0") + "\nlocked: {}\n"})
    assert r.status_code == 200 and r.json()["versions"]
    client.post("/api/chat", json={"message": "hello there"})
    assert "Rahul 2.0" in calls["system"] and "honesty" in calls["system"]
    assert "Fill in" not in calls["system"]         # template placeholders are not sent
    assert client.put("/api/identity", json={"yaml": "a: [unclosed"}).status_code == 400


def test_interview_and_journal(app):
    client, _, _ = app
    q = client.get("/api/interview/next").json()["next"][0]
    r = client.post("/api/interview/answer", json={"question_id": q["id"], "answer": "I grew up in Jaipur."})
    assert r.json()["progress"]["answered"] == 1
    assert r.json()["next"][0]["id"] != q["id"]
    j = client.post("/api/journal", json={"text": "Met a new client from London today, felt good about it."})
    assert j.status_code == 200 and client.get("/api/journal").json()


def test_whatsapp_import_keeps_only_owner_messages(app):
    client, _, _ = app
    export = (
        "12/03/2025, 9:15 pm - Rahul Sharma: Bhai kal meeting 11 baje rakhte hain, theek hai?\n"
        "12/03/2025, 9:16 pm - Neha: Ok sure\n"
        "12/03/2025, 9:17 pm - Rahul Sharma: Perfect, proposal bhi bhej deta hoon tonight\n"
        "13/03/2025, 10:01 am - Neha: <Media omitted>\n"
    )
    r = client.post("/api/import/whatsapp", json={"text": export, "chat_with": "Neha"}).json()
    assert r["mine"] == 2 and r["memories_created"] == 1
    mems = client.get("/api/memories?q=proposal").json()
    assert mems and "Ok sure" not in mems[0]["content"] and mems[0]["sensitivity"] == "private"


def test_stop_word(app):
    client, calls, _ = app
    r = client.post("/api/chat", json={"message": "stop"}).json()
    assert r["model"] == "none" and "system" not in calls
