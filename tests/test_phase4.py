"""Round 4: more chat apps, Chrome extension, steering and goals with budgets, secrets vault, coding workspaces,
memory engines, tracing, skill marketplace, Gmail push."""
import base64
import importlib
import io
import json
import shutil
import time
import zipfile

import pytest

from tests.test_phase23 import b  # noqa: F401  (the shared test Brain)


@pytest.fixture()
def x(b):  # noqa: F811
    from brain import (extension, gmail, goals, market, memory_engines, tracing, vault, workspaces, channels_extra)
    for m in (vault, extension, goals, workspaces, memory_engines, tracing, market, gmail, channels_extra):
        importlib.reload(m)                       # channels_extra registers its apps on the reloaded channels module
    for m in (vault, extension, goals, workspaces, memory_engines, tracing, market, gmail, channels_extra):
        setattr(b, m.__name__.split(".")[-1], m)
    b.mp.setattr(b.push, "notify_async", lambda *a, **k: None)
    return b


# ---------- Secrets vault ----------

def test_vault_never_shows_values_and_only_fills_on_allowed_sites(x):
    v = x.vault
    v.save("github", "github.com", value="hunter2-secret", username="rahul")
    items = v.listing()
    assert items[0]["name"] == "github" and "value" not in items[0]
    assert "hunter2" not in (x.tmp / "vault.json").read_text()                 # encrypted at rest
    filled, used = v.resolve("pw={{secret:github}} user={{secret:github:username}}", "https://github.com/login")
    assert filled == "pw=hunter2-secret user=rahul" and "hunter2-secret" in used
    for bad in ("https://evil.com/github.com", "http://github.com/login", "https://github.com.evil.io/"):
        with pytest.raises(v.VaultError):
            v.resolve("{{secret:github}}", bad)
    assert v.resolve("{{secret:github}}", "https://gist.github.com/")[0] == "hunter2-secret"   # subdomains are fine
    assert v.scrub("the page says hunter2-secret", used) == "the page says [secret]"
    assert v.listing()[0]["uses"] == 3
    block = v.prompt_block()
    assert "{{secret:github}}" in block and "hunter2" not in block
    with pytest.raises(v.VaultError):
        v.save("x", "", value="a")                                           # needs a site
    r = x.c.get("/api/vault").json()
    assert r["items"][0]["name"] == "github" and "hunter2" not in json.dumps(r)


# ---------- Chrome extension ----------

def test_extension_types_vault_secret_without_the_model_seeing_it(x):
    x.vault.save("crm", "crm.example.com", value="S3cr3t!pw")
    sent = []

    def fake_call(key, payload, timeout=45):
        sent.append(payload)
        if payload["op"] == "tabs":
            return {"tabs": [{"id": 7, "title": "CRM", "url": "https://crm.example.com/login"}]}
        return {"tab": 7, "url": "https://crm.example.com/home", "title": "Home", "text": "Welcome. S3cr3t!pw", "elements": "[0] button: Go"}
    x.mp.setattr(x.extension, "call", fake_call)
    x.mp.setattr(x.extension, "status", lambda key: {"connected": True, "browser": "Chrome", "sites": ["crm.example.com"]})
    out = x.tools.run("my_browser", {"op": "type", "tab": 7, "target": "1", "text": "{{secret:crm}}"}, {})
    assert sent[-1]["text"] == "S3cr3t!pw" and sent[-1]["secret"] and sent[-1]["lock_sites"] == ["crm.example.com"]
    assert "S3cr3t" not in out and "[secret]" in out
    with pytest.raises(PermissionError):
        x.extension.act("k", {"op": "type", "tab": 7, "text": "4111 1111 1111 1111"})   # never card numbers
    # The extension download contains a valid manifest
    z = zipfile.ZipFile(io.BytesIO(x.c.get("/api/ext/download").content))
    manifest = json.loads(z.read("ai-brain-extension/manifest.json"))
    assert manifest["manifest_version"] == 3 and "optional_host_permissions" in manifest


def test_extension_websocket_needs_a_valid_token(x):
    from brain import accounts
    with x.c.websocket_connect("/api/ext/ws") as ws:
        ws.send_json({"type": "hello", "token": "brain_wrong"})
        assert ws.receive_json()["type"] == "error"
    token = accounts.make_token(0, "Chrome")[0]
    if token:
        with x.c.websocket_connect("/api/ext/ws") as ws:
            ws.send_json({"type": "hello", "token": token, "browser": "Chrome/130", "sites": ["mail.google.com"]})
            assert ws.receive_json()["type"] == "welcome"
            assert x.c.get("/api/ext/status").json()["connected"]


# ---------- Steering and goals ----------

def test_steering_note_reaches_the_next_step_and_stop_works(x):
    def first(system, msgs):
        x.goals.steer_chat("main", "only free options")
        return '<tool>{"name": "search_memory", "args": {"query": "tools"}}</tool>'
    x.script.extend([first, lambda s, m: "answer: " + m[-1]["content"]])
    out = x.core.think("find me tools", mode="assistant", background_extract=False)
    assert "only free options" in out["reply"]

    def second(system, msgs):
        x.goals.abort_chat("main")
        return '<tool>{"name": "search_memory", "args": {"query": "x"}}</tool>'
    x.script.extend([second, "never reached"])
    assert "Stopped" in x.core.think("again", mode="assistant", background_extract=False)["reply"]
    x.script.clear()
    x.script.append("fine")                       # a stale stop doesn't kill the next answer
    x.goals.abort_chat("main")
    assert x.core.think("third", mode="assistant", background_extract=False)["reply"] == "fine"


def test_goal_runs_until_done_and_respects_budget(x):
    delivered = []
    x.mp.setattr(x.core, "deliver_to_chat", lambda text, conversation="main": delivered.append(text))
    x.script.extend(["Found 5 so far.", "GOAL_DONE: here are 10 cafés"])
    g = x.goals.start("find 10 cafés", max_requests=10)
    g = x.goals.get(g["id"])
    assert g["status"] == "done" and g["result"] == "here are 10 cafés" and g["rounds"] == 2
    assert delivered and "10 cafés" in delivered[0]

    # A budget of 2 model requests: it stops when they're used up
    real_chat = x.llm.chat

    def counting(system, msgs, **kw):
        x.usage.check_limits()
        x.usage.record("fake", "model", True, 0.1, 100)
        return real_chat(system, msgs, **kw)
    x.mp.setattr(x.llm, "chat", counting)
    x.script.extend(["step"] * 10)
    g2 = x.goals.start("endless", max_requests=2)
    g2 = x.goals.get(g2["id"])
    assert g2["status"] == "over budget" and g2["requests"] <= 2
    assert x.c.get("/api/goals").json()["goals"][0]["id"] == g2["id"]
    # slash command starts one too
    x.script.clear(); x.script.extend(["GOAL_DONE: ok"])
    r = x.c.post("/api/chat", json={"message": "/goal tidy my notes --budget 5"}).json()
    assert "budget: 5" in r["reply"]


def test_daily_limits_stop_model_use(x):
    x.c.put("/api/limits", json={"daily_requests": 1, "daily_tokens": 0})
    x.usage.record("fake", "model", True, 0.1, 10)
    with pytest.raises(x.usage.OverBudget):
        x.usage.check_limits()
    x.c.put("/api/limits", json={"daily_requests": 0, "daily_tokens": 0})
    x.usage.check_limits()


# ---------- Coding workspaces ----------

@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_workspace_patch_diff_keep_and_discard(x):
    w = x.workspaces
    assert x.c.post("/api/workspaces", json={"name": "site"}).status_code == 200
    w.write("site", "index.html", "<p>2024</p>\n")
    w.commit("site", "add page")
    patch = "--- a/index.html\n+++ b/index.html\n@@ -1 +1 @@\n-<p>2024</p>\n+<p>2026</p>\n"
    assert "applied" in x.tools.run("code", {"op": "patch", "workspace": "site", "patch": patch}, {})
    d = x.c.get("/api/workspaces/site/diff").json()["diff"]
    assert "+<p>2026</p>" in d and "-<p>2024</p>" in d
    assert x.c.post("/api/workspaces/site/commit", json={"name": "site", "message": "year"}).json()["result"].startswith("Kept")
    w.write("site", "junk.txt", "x")
    x.c.post("/api/workspaces/site/discard", json={"name": "site"})
    assert "junk.txt" not in w.tree("site")
    bad = x.tools.run("code", {"op": "read", "workspace": "site", "path": "../../etc/passwd"}, {})
    assert "outside" in bad
    assert "failed" in x.tools.run("code", {"op": "patch", "workspace": "site", "patch": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-nope\n+yes\n"}, {})
    assert "Made branch" in w.branch("site", "fix footer")
    assert any(ws["name"] == "site--fix-footer" for ws in w.list_all())


# ---------- Memory engines and tracing ----------

def test_lancedb_engine_finds_nearest_memories(x):
    pytest.importorskip("lancedb")
    me = x.memory_engines
    data = x.settings.load()
    data["memory_engines"] = {"vector": "lancedb"}
    x.settings.save(data)
    me.lance_add(1, [1.0, 0.0, 0.0])
    me.lance_add(2, [0.0, 1.0, 0.0])
    assert me.lance_search([0.9, 0.1, 0.0], limit=1) == [1]
    me.lance_delete(1)
    assert me.lance_search([0.9, 0.1, 0.0], limit=1) == [2]


def test_honcho_off_by_default_and_tracing_is_a_no_op(x):
    assert not x.memory_engines.honcho_on()
    assert "deep_memory" not in x.tools.available({})
    with x.tracing.span("test") as sp:
        assert sp is None
    assert x.c.get("/api/memory-engines").json()["tracing"]["on"] is False


# ---------- Skill marketplace ----------

class _Resp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code, self._data, self.content = status, data, content

    def json(self):
        return self._data


def test_clawhub_search_and_install_arrive_as_suggested(x):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("invoice-chaser/SKILL.md", "---\nname: invoice-chaser\ndescription: Follow up on unpaid invoices\n---\nBe polite.")
    seen = []

    def fake_get(url, params=None, **kw):
        seen.append((url, params))
        if url.endswith("/search"):
            return _Resp(data={"results": [{"slug": "invoice-chaser", "displayName": "Invoice chaser", "summary": "Follow up", "version": "1.0"}]})
        return _Resp(content=buf.getvalue())
    x.mp.setattr(x.market.httpx, "get", fake_get)
    r = x.c.get("/api/market/clawhub", params={"q": "invoice"}).json()
    assert r["results"][0]["slug"] == "invoice-chaser" and seen[0][1]["nonSuspiciousOnly"] == "true"
    assert x.c.post("/api/market/clawhub/install", json={"slug": "invoice-chaser"}).json()["added"] == ["invoice-chaser"]
    assert next(s for s in x.skills.list_all() if s["name"] == "invoice-chaser")["status"] == "suggested"
    assert x.c.post("/api/market/clawhub/install", json={"slug": "../../etc"}).status_code == 502


def test_server_shelf_publish_and_install(x):
    x.skills.save("proposal-writer", "How I write proposals", "Start with the client's problem.", "active", "owner")
    item = x.c.post("/api/market/shelf/publish", json={"name": "proposal-writer"}).json()
    assert item["status"] == "approved"                       # laptop: you're the admin
    shelf = x.c.get("/api/market/shelf").json()["items"]
    assert shelf[0]["name"] == "proposal-writer" and "body" not in shelf[0]
    x.skills.delete("proposal-writer") if hasattr(x.skills, "delete") else None
    assert x.c.post("/api/market/shelf/install", json={"id": item["id"]}).json()["added"]


# ---------- Gmail push ----------

def test_gmail_push_reads_new_mail_through_the_watcher(x):
    g = x.gmail
    g.save({"client_id": "cid", "client_secret": "csecret", "topic": "projects/p/topics/t"})
    c = g.cfg()
    c.update(refresh="x", history_id="100", enabled=True, address="me@example.com")
    g._store(c)
    raw = base64.urlsafe_b64encode(b"From: Boss <boss@x.com>\r\nSubject: Urgent\r\n\r\nCall me").decode()

    def fake_api(method, path, **kw):
        if path == "/history":
            assert kw["params"]["startHistoryId"] == "100"
            return {"historyId": "105", "history": [{"messagesAdded": [{"message": {"id": "m1", "labelIds": ["INBOX"]}}]}]}
        return {"raw": raw}
    x.mp.setattr(g, "_api", fake_api)
    handled = []
    x.mp.setattr(x.workflows, "handle_email", lambda s, subj, body, c=None: handled.append((s, subj, body.strip())))
    secret = g.push_secret()
    data = base64.b64encode(json.dumps({"emailAddress": "me@example.com", "historyId": 105}).encode()).decode()
    r = x.c.post(f"/api/gmail/push/{secret}", json={"message": {"data": data}})
    assert r.status_code == 204
    assert handled == [("Boss <boss@x.com>", "Urgent", "Call me")]
    assert g.cfg()["history_id"] == "105"
    assert x.c.post("/api/gmail/push/wrong", json={"message": {"data": data}}).status_code == 204
    assert len(handled) == 1
    pub = x.c.get("/api/gmail").json()
    assert pub["connected"] and "csecret" not in json.dumps(pub) and pub["push_url"].endswith(secret)


# ---------- More chat apps: the crypto each service uses ----------

def test_new_channel_crypto_round_trips(x):
    cx = x.channels_extra
    key = base64.b64encode(b"k" * 32).decode().rstrip("=")
    c = {"aes_key": key, "corp_id": "corp1"}
    assert cx.wecom_decrypt(c, cx.wecom_encrypt(c, "<xml>hi</xml>")) == "<xml>hi</xml>"
    with pytest.raises(PermissionError):
        cx.wecom_decrypt({"aes_key": key, "corp_id": "other"}, cx.wecom_encrypt(c, "x"))
    from coincurve import PrivateKey
    a, bk = PrivateKey(), PrivateKey()
    a_pub, b_pub = a.public_key.format()[1:].hex(), bk.public_key.format()[1:].hex()
    msg = cx.nip04_encrypt(a, b_pub, "hello brain")
    assert cx.nip04_decrypt(bk, a_pub, msg) == "hello brain"
    ev = cx.nostr_event(a, a_pub, 4, msg, [["p", b_pub]])
    assert cx.nostr_verify(ev)
    ev["content"] = "tampered"
    assert not cx.nostr_verify(ev)
    import hashlib
    q = {"timestamp": "1", "nonce": "2"}
    q["signature"] = hashlib.sha1("".join(sorted(["tok", "1", "2"])).encode()).hexdigest()
    assert cx._wx_sig_ok("tok", q) and not cx._wx_sig_ok("bad", q)
    assert len(x.channels.CHANNELS) >= 27


def test_housekeeping_uses_freellmapi_fast_pool(x):
    data = x.settings.load()
    data["order"] = ["ollama"]; x.settings.save(data)
    assert x.llm.light() is None                              # FreeLLMAPI not in use: normal routing
    data = x.settings.load()
    data.setdefault("providers", {})["freellmapi"] = {"api_key": "k"}
    data["order"] = ["freellmapi"]
    x.settings.save(data)
    assert x.llm.light() == ("freellmapi", "auto:fast")
    data = x.settings.load(); data["light_model"] = ""; x.settings.save(data)
    assert x.llm.light() is None


def test_encrypted_backup_restores_memories_and_vault(x):
    from brain import backup, memory
    memory.add("I prefer morning meetings", type="semantic", auto_tag=False)
    x.vault.save("github", "github.com", value="pw-123456", username="me")
    r = x.c.post("/api/backup", json={"passphrase": "correct horse"})
    assert r.status_code == 200
    blob = r.content
    assert blob.startswith(backup.MAGIC) and b"morning meetings" not in blob and b"pw-123456" not in blob
    memory.add("a newer memory", type="semantic", auto_tag=False)
    bad = x.c.post("/api/backup/restore", files={"file": ("b.brainbackup", blob)}, headers={"X-Backup-Passphrase": "wrong pass"})
    assert bad.status_code == 400 and "passphrase" in bad.json()["detail"]
    (x.tmp / ".vault.key").unlink()                     # like a brand-new computer: a different vault key
    x.vault._fernet()
    ok = x.c.post("/api/backup/restore", files={"file": ("b.brainbackup", blob)}, headers={"X-Backup-Passphrase": "correct horse"})
    assert ok.status_code == 200, ok.text
    contents = [m["content"] for m in memory.list_all()]
    assert "I prefer morning meetings" in contents and "a newer memory" not in contents
    assert x.vault.resolve("{{secret:github}}", "https://github.com/")[0] == "pw-123456"
    with pytest.raises(backup.BackupError):
        backup.make("short")


def test_phone_access_only_over_tailscale_with_password(x):
    from fastapi.testclient import TestClient
    from brain import remote, server
    importlib.reload(remote)
    x.mp.setattr(remote, "start", lambda: "")                   # no real listener in tests
    phone = TestClient(server.app, base_url="http://100.101.1.2:8766", client=("100.101.1.2", 50000))
    wifi = TestClient(server.app, base_url="http://192.168.1.20:8765", client=("192.168.1.20", 50000))
    assert phone.get("/api/status").status_code == 403           # off by default
    assert x.c.put("/api/remote", json={"enabled": True}).status_code == 400      # needs a password first
    assert x.c.put("/api/remote", json={"password": "short"}).status_code == 400
    assert x.c.put("/api/remote", json={"password": "a-long-phone-pass", "enabled": True}).json()["enabled"]
    assert wifi.get("/api/status").status_code == 403           # Wi-Fi and the internet: never
    r = phone.get("/api/status")
    assert r.status_code == 401 and r.json()["remote"]
    assert phone.post("/api/remote/login", json={"password": "wrong"}).status_code == 401
    assert phone.put("/api/remote", json={"enabled": False}).status_code in (401, 403)   # settings only on the computer
    assert phone.post("/api/remote/login", json={"password": "a-long-phone-pass"}).status_code == 200
    assert phone.get("/api/status").status_code == 200
    assert phone.put("/api/remote", json={"enabled": False}).status_code == 403
    assert phone.post("/api/master/unlock", json={"pin": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    for _ in range(8):
        phone.post("/api/remote/login", json={"password": "nope"})
    assert "Too many" in phone.post("/api/remote/login", json={"password": "a-long-phone-pass"}).json()["detail"]
    x.c.post("/api/remote/logout-all")
    assert phone.get("/api/status").status_code == 401
    assert remote.is_tailnet("100.64.0.1") and not remote.is_tailnet("192.168.1.2") and remote.is_tailnet("fd7a:115c:a1e0::1")


def test_phone_app_link_controls_the_phone_and_voice_commands_work(x):
    from brain import accounts, devices, phonelink
    importlib.reload(phonelink)
    token = accounts.make_token(0, "Phone")[0]
    with x.c.websocket_connect("/api/phone/ws") as ws:
        ws.send_json({"type": "hello", "token": "brain_bad"})
        assert ws.receive_json()["type"] == "error"
    sent = []

    def fake_call(key, payload, timeout=40):
        sent.append(payload)
        if payload["op"] == "screen":
            return {"app": "WhatsApp", "elements": [{"label": "Search", "kind": "EditText", "tap": True, "x": 100, "y": 200, "edit": True}]}
        return {}
    x.mp.setattr(phonelink, "call", fake_call)
    with x.c.websocket_connect("/api/phone/ws") as ws:
        ws.send_json({"type": "hello", "token": token, "name": "Rahul's Pixel", "accessibility": True})
        assert ws.receive_json()["type"] == "welcome"
        assert x.c.get("/api/phone").json()["connected"]
        st = devices.status()
        assert st["phones"][0]["via"] == "app"
        view = devices.look("phone")
        assert "WhatsApp" in view["text"] and "[1] \"Search\"" in view["text"]
        assert devices.run("phone_tap_element", {"number": 1})["text"].startswith("Tapped [1]")
        assert sent[-1] == {"op": "tap", "x": 100, "y": 200, "node": None}
        devices.run("phone_key", {"key": "home"})
        assert sent[-1] == {"op": "key", "name": "home"}
        with pytest.raises(PermissionError):
            devices.run("phone_type", {"text": "4111 1111 1111 1111"})
    assert not x.c.get("/api/phone").json()["connected"]
    # Voice commands (phone app, laptop listener, Siri): one Brain turn, reply to speak
    x.script.append("Done, I set it for 7 am.")
    r = x.c.post("/api/voice/command", json={"text": "wake me at 7", "device": "phone"})
    assert r.json()["reply"] == "Done, I set it for 7 am."
    x.script.append("Plain reply")
    assert x.c.post("/api/voice/command?format=text", json={"text": "hi"}).text == "Plain reply"


def test_offline_routing_uses_only_local_models(x):
    data = x.settings.load()
    data.setdefault("providers", {})["freellmapi"] = {"api_key": "k"}
    data["order"] = ["freellmapi", "ollama"]
    x.settings.save(data)
    x.mp.setattr(x.llm, "online", lambda: False)
    assert [p["id"] for p in x.llm._chain()] == ["ollama"]
    x.mp.setattr(x.llm, "online", lambda: True)
    assert [p["id"] for p in x.llm._chain()] == ["freellmapi", "ollama"]


def test_listener_learns_custom_activation_words(x):
    from brain import listener, master
    importlib.reload(listener)
    master.update({"wake_words": ["hey jarvis"]})
    x.mp.setattr(listener.Listener, "transcribe", lambda self, pcm: "a job is")
    assert listener.learn(b"\0\0")["added"]
    assert listener.wake_list() == ["hey jarvis", "a job is"]
    lis = listener.Listener(x.tmp, None, ["hey jarvis"], aliases=listener.learned())
    assert lis._wake_split("a job is open the calculator") == (True, "open the calculator")
    assert lis._wake_split("a job this open the calculator") == (True, "open the calculator")
    assert lis._wake_split("the brain is big") == (False, "")
    x.mp.setattr(listener.Listener, "transcribe", lambda self, pcm: "hi")
    assert not listener.learn(b"\0\0")["added"]                  # too short: would wake on everyday talk
    listener.forget_learned()
    assert listener.wake_list() == ["hey jarvis"]
    st = x.c.get("/api/listener").json()
    assert st["available"] and st["state"] == "off"


def test_voice_commands_prefer_freellmapi_fast_models(x):
    data = x.settings.load()
    data.setdefault("providers", {})["freellmapi"] = {"api_key": "k"}
    data["order"] = ["freellmapi", "gemini"]
    x.settings.save(data)
    assert x.llm.voice_model() == ("freellmapi", "auto:fast")
    x.script.append("Okay.")
    x.c.post("/api/voice/command", json={"text": "what's next today", "device": "phone"})
    assert x.calls[-1][2]["prefer"] == ("freellmapi", "auto:fast")
    data = x.settings.load(); data["voice_model"] = ""; x.settings.save(data)
    assert x.llm.voice_model() is None
