"""Provider layer tests with a fake HTTP layer: no real keys or network needed."""
import importlib
import json

import httpx
import pytest


class FakeHTTP:
    """Records requests and answers them according to per-host rules."""

    def __init__(self):
        self.calls = []
        self.rules = {}  # substring of URL -> (status, json)

    def _answer(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for part, (status, body) in self.rules.items():
            if part in url:
                return httpx.Response(status, json=body, request=httpx.Request(method, url))
        raise httpx.ConnectError("no route", request=httpx.Request(method, url))

    def post(self, url, **kw):
        return self._answer("POST", url, **kw)

    def get(self, url, **kw):
        return self._answer("GET", url, **kw)


def oa(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from brain import config
    importlib.reload(config)
    from brain import settings, llm, db, memory, core, identity, interview, importer, server
    for m in (settings, llm, db, memory, core, identity, interview, importer, server):
        importlib.reload(m)
    fake = FakeHTTP()
    monkeypatch.setattr(llm.httpx, "post", fake.post)
    monkeypatch.setattr(llm.httpx, "get", fake.get)
    db.init()
    return fake, settings, llm, memory, server


def setup(settings, order, **providers):
    settings.save({"order": order, "providers": providers, "embeddings": "none"})


def test_fallback_when_free_tier_is_exhausted(env):
    fake, settings, llm, *_ = env
    setup(settings, ["groq", "gemini"], groq={"api_key": "gk"}, gemini={"api_key": "gm"})
    fake.rules = {"api.groq.com": (429, {"error": {"message": "Rate limit reached"}}),
                  "generativelanguage": (200, oa("Hello from Gemini"))}
    reply, used = llm.chat("sys", [{"role": "user", "content": "hi"}])
    assert reply == "Hello from Gemini" and used.startswith("gemini:")
    groq_call = next(c for c in fake.calls if "groq" in c[1])
    assert groq_call[1].endswith("/chat/completions")
    assert groq_call[2]["headers"]["Authorization"] == "Bearer gk"
    assert groq_call[2]["json"]["messages"][0] == {"role": "system", "content": "sys"}


def test_all_fail_gives_clear_error(env):
    fake, settings, llm, *_ = env
    setup(settings, ["groq"], groq={"api_key": "bad"})
    fake.rules = {"api.groq.com": (401, {"error": {"message": "Invalid API Key"}})}
    with pytest.raises(llm.ModelUnavailable) as e:
        llm.chat("s", [{"role": "user", "content": "x"}])
    assert "key rejected" in str(e.value)


def test_provider_without_key_is_skipped(env):
    fake, settings, llm, *_ = env
    setup(settings, ["gemini", "openrouter"], openrouter={"api_key": "or"})
    fake.rules = {"openrouter.ai": (200, oa("free model reply"))}
    reply, used = llm.chat("s", [{"role": "user", "content": "x"}])
    assert used == "openrouter:openrouter/free"
    assert not any("generativelanguage" in c[1] for c in fake.calls)


def test_anthropic_format(env):
    fake, settings, llm, *_ = env
    setup(settings, ["anthropic"], anthropic={"api_key": "sk-ant"})
    fake.rules = {"api.anthropic.com": (200, {"content": [{"type": "text", "text": "Claude here"}]})}
    reply, _ = llm.chat("sys", [{"role": "user", "content": "hi"}])
    call = fake.calls[-1]
    assert reply == "Claude here" and call[2]["json"]["system"] == "sys"
    assert call[2]["headers"]["x-api-key"] == "sk-ant"


def test_sealed_conversations_never_leave_the_laptop(env):
    fake, settings, llm, *_ = env
    setup(settings, ["gemini", "ollama"], gemini={"api_key": "gm"})
    fake.rules = {"127.0.0.1:11434/api/chat": (200, {"message": {"content": "local answer"}}),
                  "generativelanguage": (200, oa("cloud"))}
    reply, used = llm.chat("s", [{"role": "user", "content": "x"}], private=True)
    assert reply == "local answer" and used.startswith("ollama:")
    assert not any("generativelanguage" in c[1] for c in fake.calls)


def test_private_json_stays_local_unless_allowed(env):
    fake, settings, llm, *_ = env
    setup(settings, ["gemini"], gemini={"api_key": "gm"})
    fake.rules = {"generativelanguage": (200, oa('```json\n{"tags": ["work"]}\n```'))}
    assert llm.private_json("tag", "text") is None           # Ollama off, cloud not allowed
    s = settings.load(); s["cloud_private_tasks"] = True; settings.save(s)
    assert llm.private_json("tag", "text") == {"tags": ["work"]}   # code fences handled


def test_embeddings_switch_does_not_mix_vectors(env):
    fake, settings, llm, memory, _ = env
    settings.save({"order": ["gemini"], "providers": {"gemini": {"api_key": "gm"}}, "embeddings": "gemini"})
    fake.rules = {"/embeddings": (200, {"data": [{"embedding": [1.0, 0.0, 0.0]}]})}
    m = memory.add("Planning a trip to Goa in December", auto_tag=False)
    assert memory.index_coverage() == {"model": "gemini:gemini-embedding-2", "indexed": 1, "total": 1}
    s = settings.load(); s["providers"]["gemini"]["embed_model"] = "other-embed"; settings.save(s)
    assert memory.index_coverage()["indexed"] == 0     # old vectors are not compared with new ones
    assert memory.reindex()["done"] == 1
    assert memory.index_coverage()["indexed"] == 1
    assert memory.search("Goa trip")[0]["id"] == m["id"]


def test_list_models_puts_free_first(env):
    fake, settings, llm, *_ = env
    setup(settings, ["openrouter"], openrouter={"api_key": "or"})
    fake.rules = {"openrouter.ai/api/v1/models": (200, {"data": [{"id": "x/paid"}, {"id": "y/model:free"}, {"id": "openrouter/free"}]})}
    assert llm.list_models("openrouter")[:2] == ["openrouter/free", "y/model:free"]


def test_settings_api_masks_and_keeps_keys(env):
    fake, settings, llm, memory, server = env
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        body = {"order": ["groq"], "providers": {"groq": {"api_key": "gsk_secret1234"}}, "embeddings": "none"}
        r = c.put("/api/settings", json=body).json()
        masked = r["settings"]["providers"]["groq"]["api_key"]
        assert masked == "••••1234" and "secret" not in json.dumps(r)
        body["providers"]["groq"]["api_key"] = masked          # browser sends the masked value back
        c.put("/api/settings", json=body)
        assert settings.load()["providers"]["groq"]["api_key"] == "gsk_secret1234"
        fake.rules = {"api.groq.com": (200, oa("OK"))}
        assert c.post("/api/settings/test/groq").json()["ok"] is True
        st = c.get("/api/status").json()
        assert st["chain"][0]["id"] == "groq" and st["chain"][0]["ready"]


def test_gemini_list_shaped_error_is_readable(env):
    fake, settings, llm, *_ = env
    setup(settings, ["gemini"], gemini={"api_key": "bad"})
    fake.rules = {"generativelanguage": (400, [{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}])}
    r = llm.test_provider("gemini")
    assert r["ok"] is False and "API key not valid" in r["error"]
    fake.rules = {"generativelanguage": (404, [{"error": {"message": "models/gemini-x is not found"}}])}
    assert "not found" in llm.test_provider("gemini")["error"]


def test_busy_model_falls_back_to_backup_model_then_skips_it(env):
    fake, settings, llm, *_ = env
    llm._cooldown.clear()
    setup(settings, ["gemini"], gemini={"api_key": "k", "model": "gemini-3.8-flash", "fallbacks": ["gemini-2.5-flash"]})
    seen = []

    def post(url, **kw):
        model = kw["json"]["model"]; seen.append(model)
        if model == "gemini-3.8-flash":
            return httpx.Response(503, json=[{"error": {"message": "high demand"}}], request=httpx.Request("POST", url))
        return httpx.Response(200, json=oa("hi from backup"), request=httpx.Request("POST", url))
    fake.post = post
    import brain.llm as L
    L.httpx.post = post
    reply, used = llm.chat("s", [{"role": "user", "content": "hello"}])
    assert reply == "hi from backup" and used == "gemini:gemini-2.5-flash"
    seen.clear()
    llm.chat("s", [{"role": "user", "content": "again"}])
    assert seen == ["gemini-2.5-flash"]          # the busy model is skipped for a while


def test_bad_key_does_not_retry_every_model(env):
    fake, settings, llm, *_ = env
    llm._cooldown.clear()
    setup(settings, ["groq"], groq={"api_key": "bad", "fallbacks": ["a", "b", "c"]})
    fake.rules = {"api.groq.com": (401, {"error": {"message": "Invalid API Key"}})}
    with pytest.raises(llm.ModelUnavailable):
        llm.chat("s", [{"role": "user", "content": "x"}])
    assert len([c for c in fake.calls if "chat/completions" in c[1]]) == 1


def test_discover_picks_free_chat_models_and_saves_backups(env):
    fake, settings, llm, *_ = env
    setup(settings, ["openrouter"], openrouter={"api_key": "or"})
    models = [{"id": i} for i in ["openai/gpt-5:paid", "meta-llama/llama-3.3-70b-instruct:free", "openrouter/free",
                                   "google/gemma-3-4b-it:free", "some/embed-model:free", "qwen/qwen3-235b:free", "x/broken:free"]]

    def get(url, **kw):
        return httpx.Response(200, json={"data": models}, request=httpx.Request("GET", url))

    def post(url, **kw):
        m = kw["json"]["model"]
        if m == "x/broken:free":
            return httpx.Response(429, json={"error": {"message": "quota"}}, request=httpx.Request("POST", url))
        return httpx.Response(200, json=oa("OK"), request=httpx.Request("POST", url))
    import brain.llm as L
    L.httpx.get, L.httpx.post = get, post
    r = llm.discover("openrouter")
    assert r["ok"] and r["model"] == "openrouter/free"
    chosen = [r["model"], *r["fallbacks"]]
    assert "openai/gpt-5:paid" not in r["tested"] and "some/embed-model:free" not in r["tested"]
    assert "x/broken:free" not in chosen and len(chosen) <= 4
    saved = settings.resolved("openrouter")
    assert saved["model"] == r["model"] and saved["fallbacks"] == r["fallbacks"]


def test_discover_all_skips_unusable_providers(env):
    fake, settings, llm, *_ = env
    setup(settings, ["anthropic", "groq", "ollama"], anthropic={"api_key": "x"})
    out = {r["provider"]: r for r in llm.discover_all()}
    assert out["anthropic"]["skipped"].startswith("paid") and out["groq"]["skipped"] == "no API key"
    assert out["ollama"]["skipped"] == "Ollama is not running"
