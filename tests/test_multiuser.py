"""The web version: accounts, each user's private Brain, admin, security, and the companion link."""
import importlib
import io
import threading
import time
import zipfile

import pytest

SHOT = {"data": "aGVsbG8=", "mime": "image/jpeg", "width": 640, "height": 360, "scale": 0.5, "real": [1280, 720]}


@pytest.fixture()
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_NO_SCHEDULER", "1")
    monkeypatch.setenv("BRAIN_MULTIUSER", "1")
    from brain import config
    importlib.reload(config)
    from brain import (accounts, settings, master, control, devices, hub, agent, llm, db, memory, web, knowledge,
                       voice, core, identity, interview, importer, server)
    for m in (accounts, settings, master, control, devices, hub, agent, llm, db, memory, web, knowledge, voice, core,
              identity, interview, importer, server):
        importlib.reload(m)
    monkeypatch.setattr(llm, "chat", lambda system, msgs, **kw: ("answer", "fake"))
    monkeypatch.setattr(llm, "embed", lambda t: None)
    monkeypatch.setattr(llm, "private_json", lambda s, p: None)
    monkeypatch.setattr(accounts.time, "sleep", lambda s: None)
    monkeypatch.setattr(devices, "run_local", lambda *a: pytest.fail("device actions must never run on the server"))
    monkeypatch.setattr(devices, "look_local", lambda *a: pytest.fail("device actions must never run on the server"))
    from fastapi.testclient import TestClient
    with TestClient(server.app) as a:
        b = TestClient(server.app)
        yield type("W", (), dict(a=a, b=b, accounts=accounts, config=config, settings=settings, hub=hub,
                                 agent=agent, server=server, tmp=tmp_path, TestClient=TestClient))


def signup(c, email, name="Test", pw="password123"):
    r = c.post("/api/auth/signup", json={"email": email, "password": pw, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_must_log_in(w):
    assert w.a.get("/api/status").status_code == 401
    me = w.a.get("/api/auth/me").json()
    assert me == {"multiuser": True, "user": None, "signup_open": True, "first_user": True, "email_reset": False}
    assert w.a.get("/").status_code == 200 and w.a.get("/manifest.webmanifest").status_code == 200
    assert w.a.get("/sw.js").status_code == 200


def test_signup_login_logout_and_rules(w):
    admin = signup(w.a, "Rahul@Example.com", "Rahul Sharma")
    assert admin["role"] == "admin" and admin["email"] == "rahul@example.com"
    assert w.a.get("/api/status").json()["owner"] == "Rahul Sharma"
    user = signup(w.b, "amit@example.com", "Amit")
    assert user["role"] == "user"
    assert w.b.post("/api/auth/signup", json={"email": "amit@example.com", "password": "password123", "name": "X"}).status_code == 409
    assert w.b.post("/api/auth/signup", json={"email": "x@example.com", "password": "short", "name": "X"}).status_code == 400
    assert w.b.post("/api/auth/signup", json={"email": "not-an-email", "password": "password123", "name": "X"}).status_code == 400
    w.b.post("/api/auth/logout")
    assert w.b.get("/api/status").status_code == 401
    assert w.b.post("/api/auth/login", json={"email": "amit@example.com", "password": "wrong-pass"}).status_code == 401
    assert w.b.post("/api/auth/login", json={"email": "AMIT@example.com", "password": "password123"}).status_code == 200
    assert w.b.get("/api/status").json()["owner"] == "Amit"
    # stored safely
    import sqlite3
    row = sqlite3.connect(w.tmp / "accounts.db").execute("SELECT pw_hash, pw_salt FROM users WHERE id=2").fetchone()
    assert "password123" not in row[0] and len(row[0]) == 64
    sess = sqlite3.connect(w.tmp / "accounts.db").execute("SELECT token_hash FROM sessions").fetchall()
    assert all(w.b.cookies.get("brain_auth") != s[0] for s in sess)


def test_too_many_wrong_passwords(w):
    signup(w.a, "a@example.com")
    for _ in range(5):
        w.b.post("/api/auth/login", json={"email": "a@example.com", "password": "nope-nope"})
    assert w.b.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"}).status_code == 429


def test_each_user_has_a_private_brain(w):
    signup(w.a, "a@example.com", "Asha")
    signup(w.b, "b@example.com", "Bala")
    w.a.post("/api/memories", json={"content": "My secret project is Falcon.", "type": "semantic"})
    w.a.put("/api/settings", json={"order": ["gemini"], "providers": {"gemini": {"api_key": "AIza-asha-key-1234"}}})
    w.a.put("/api/master", json={"changes": {"brain_name": "Jarvis"}})
    assert [m["content"] for m in w.a.get("/api/memories").json()] == ["My secret project is Falcon."]
    assert w.b.get("/api/memories").json() == []
    assert "gemini" not in w.b.get("/api/settings").json()["settings"]["providers"] or \
        not w.b.get("/api/settings").json()["settings"]["providers"]["gemini"].get("has_key")
    assert w.b.get("/api/master/public").json()["brain_name"] == "Brain"
    assert (w.tmp / "users" / "1" / "brain.db").exists() and (w.tmp / "users" / "2" / "brain.db").exists()
    assert not (w.tmp / "brain.db").exists() or w.b.get("/api/memories").json() == []
    exported = w.b.get("/api/master/export").text
    assert "Falcon" not in exported


def test_admin_controls(w):
    signup(w.a, "admin@example.com", "Admin")
    u = signup(w.b, "user@example.com", "User")
    assert w.b.get("/api/admin/users").status_code == 403
    users = w.a.get("/api/admin/users").json()["users"]
    assert [x["email"] for x in users] == ["admin@example.com", "user@example.com"]
    w.a.post("/api/admin/signup", json={"open": False})
    c = w.TestClient(w.server.app)
    assert c.post("/api/auth/signup", json={"email": "new@example.com", "password": "password123", "name": "N"}).status_code == 403
    temp = w.a.post(f"/api/admin/users/{u['id']}/reset").json()["temporary_password"]
    assert w.b.get("/api/status").status_code == 401                  # reset logs them out
    assert c.post("/api/auth/login", json={"email": "user@example.com", "password": temp}).status_code == 200
    w.a.patch(f"/api/admin/users/{u['id']}", json={"changes": {"disabled": True}})
    assert c.get("/api/status").status_code == 401
    assert w.a.delete(f"/api/admin/users/{u['id']}").status_code == 200
    assert not (w.tmp / "users" / str(u["id"])).exists()


def test_account_password_and_delete(w):
    signup(w.a, "a@example.com")
    u = signup(w.b, "b@example.com")
    assert w.b.post("/api/account/password", json={"old": "wrong", "new": "newpassword1"}).status_code == 401
    assert w.b.post("/api/account/password", json={"old": "password123", "new": "newpassword1"}).status_code == 200
    assert w.b.get("/api/status").status_code == 200            # this session stays logged in
    assert w.b.post("/api/account/delete", json={"password": "newpassword1"}).status_code == 200
    assert not (w.tmp / "users" / str(u["id"])).exists()
    assert w.b.post("/api/auth/login", json={"email": "b@example.com", "password": "newpassword1"}).status_code == 401


def test_other_websites_blocked_and_server_stays_safe(w):
    signup(w.a, "a@example.com")
    signup(w.b, "b@example.com")
    assert w.b.post("/api/memories", json={"content": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert w.b.post("/api/memories", json={"content": "x"}, headers={"Origin": "http://testserver"}).status_code == 200
    # a normal user can't point the server at its own private network
    w.b.put("/api/settings", json={"order": ["custom", "ollama"], "providers": {"custom": {"base_url": "http://127.0.0.1:9911/v1", "model": "m"}}})
    tok, role = w.config.use_dir(w.tmp / "users" / "2"), w.config.set_role("user")
    try:
        from brain import llm
        assert [p["id"] for p in llm._chain()] == [] and not llm.ollama_available(fresh=True)
        assert w.settings.url_allowed("https://8.8.8.8/v1") and not w.settings.url_allowed("http://169.254.169.254/latest")
    finally:
        w.config.reset_dir(tok); w.config._user_role.reset(role)
    for path in ("/api/voice/install", "/api/control/install"):
        r = w.b.post(path, json={"component": "pc"})
        assert r.status_code in (400, 401, 403) and r.status_code != 200


def companion(w, client):
    code = client.post("/api/companion/code").json()["code"]
    r = w.TestClient(w.server.app).post("/api/companion/pair", json={"code": code, "name": "Rahul-PC"})
    assert r.status_code == 200
    return r.json()["token"]


def test_companion_runs_actions_on_the_users_own_computer(w):
    signup(w.a, "a@example.com", "Asha")
    signup(w.b, "b@example.com", "Bala")
    token = companion(w, w.a)
    assert w.TestClient(w.server.app).post("/api/companion/pair", json={"code": "WRONG123"}).status_code == 401
    w.a.put("/api/control", json={"changes": {"enabled": True, "devices": {"pc": True}}})
    assert w.a.get("/api/control").json()["settings"]["folders"] == ["~/Desktop", "~/Documents", "~/Downloads"]
    ws_client = w.TestClient(w.server.app)
    with ws_client.websocket_connect("/api/companion/ws") as ws:
        ws.send_json({"type": "hello", "token": token, "system": "Windows", "pc_installed": True, "adb_installed": False,
                      "phones": []})
        assert ws.receive_json()["type"] == "welcome"
        st = w.a.get("/api/control").json()["status"]
        assert st["connected"] and st["system"] == "Windows" and st["device_name"] == "Rahul-PC"
        assert w.a.get("/api/companion/devices").json()[0]["online"] is True
        assert w.b.get("/api/control").json()["status"]["connected"] is False       # not Bala's computer

        out = {}
        t = threading.Thread(target=lambda: out.update(r=w.a.get("/api/control/look/pc")))
        t.start()
        msg = ws.receive_json()
        assert msg["op"] == "look" and msg["device"] == "pc" and msg["folders"] == ["~/Desktop", "~/Documents", "~/Downloads"]
        ws.send_json({"id": msg["id"], "ok": True, "result": {"text": "windows", "shot": SHOT}})
        t.join(5)
        assert out["r"].status_code == 200 and out["r"].json()["image"].startswith("data:image/jpeg;base64,")

        # a permission refusal on the computer comes back as "blocked"
        t = threading.Thread(target=lambda: out.update(r=w.a.get("/api/control/look/pc")))
        t.start()
        msg = ws.receive_json()
        ws.send_json({"id": msg["id"], "ok": False, "kind": "permission", "error": "outside the folders"})
        t.join(5)
        assert out["r"].status_code == 400 and "outside" in out["r"].json()["detail"]
    time.sleep(0.1)
    assert w.a.get("/api/control").json()["status"]["connected"] is False
    r = w.a.get("/api/control/look/pc")
    assert r.status_code == 400 and "isn't connected" in r.json()["detail"]
    # removing the computer revokes its token
    cid = w.a.get("/api/companion/devices").json()[0]["id"]
    w.a.delete(f"/api/companion/devices/{cid}")
    with ws_client.websocket_connect("/api/companion/ws") as ws:
        ws.send_json({"type": "hello", "token": token})
        assert ws.receive_json()["type"] == "error"


def test_tasks_are_private(w):
    signup(w.a, "a@example.com")
    signup(w.b, "b@example.com")
    w.a.put("/api/control", json={"changes": {"enabled": True, "devices": {"pc": True}}})
    t = w.a.post("/api/control/task", json={"goal": "open notepad", "device": "pc"}).json()
    assert w.b.get(f"/api/control/task/{t['id']}").status_code == 404
    assert w.b.get("/api/control/tasks").json() == []
    assert w.b.post("/api/control/stop").json()["stopped"] == 0
    for _ in range(50):
        status = w.a.get(f"/api/control/task/{t['id']}").json()["status"]
        if status != "running":
            break
        time.sleep(0.1)
    assert status == "failed"                                                       # no computer connected


def test_companion_download(w):
    r = w.a.get("/download/BrainCompanion-python.zip")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "BrainCompanion/brain_companion.py" in names and "BrainCompanion/brain/devices.py" in names
    assert "BrainCompanion/start-companion.bat" in names
    assert w.a.get("/download/../../etc/passwd").status_code == 404
    assert w.a.get("/download/BrainCompanion-Windows.exe").status_code == 404
