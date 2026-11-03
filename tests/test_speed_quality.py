"""Tests for the speed, voice and reply-quality fixes."""
import importlib
import json

import httpx
import pytest


@pytest.fixture()
def b(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_NO_SCHEDULER", "1")
    from brain import config
    importlib.reload(config)
    from brain import settings, llm, db, memory, web, knowledge, voice, core, identity, interview, importer, server
    for m in (settings, llm, db, memory, web, knowledge, voice, core, identity, interview, importer, server):
        importlib.reload(m)
    db.init(); knowledge.init()
    return type("B", (), dict(settings=settings, llm=llm, memory=memory, core=core, voice=voice, server=server))


class Net:
    """Fake network that records calls. Ollama is 'off' (connection refused)."""
    def __init__(self, mp, llm, reply="Bas badhiya, tum batao?", stream_chunks=None, chat_status=None):
        self.calls, self.payloads = [], []
        self.reply, self.chunks, self.chat_status = reply, stream_chunks, chat_status or {}
        mp.setattr(llm.httpx, "get", self.get)
        mp.setattr(llm.httpx, "post", self.post)
        mp.setattr(llm.httpx, "stream", self.stream)

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if "11434" in url:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={}, request=httpx.Request("GET", url))

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        if url.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]}, request=httpx.Request("POST", url))
        payload = kw.get("json") or {}
        self.payloads.append(payload)
        status = self.chat_status.get(json.dumps({k: payload.get(k) for k in ("reasoning_effort",)}, sort_keys=True))
        if status:
            return httpx.Response(status, json=[{"error": {"message": "Invalid reasoning_effort"}}], request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": [{"message": {"content": self.reply}}]}, request=httpx.Request("POST", url))

    def stream(self, method, url, **kw):
        self.calls.append(("STREAM", url))
        self.payloads.append(kw.get("json"))
        chunks = self.chunks or [self.reply]
        lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}" for c in chunks] + ["data: [DONE]"]
        body = "\n\n".join(lines).encode()

        class Ctx:
            def __enter__(s):
                return httpx.Response(200, content=body, request=httpx.Request(method, url))
            def __exit__(s, *a):
                return False
        return Ctx()


def gemini(b, **extra):
    b.settings.save({"order": ["gemini", "ollama"], "providers": {"gemini": {"api_key": "k", **extra}}, "embeddings": "auto"})


# ---------- Speed ----------

def test_one_turn_makes_one_ollama_check_and_one_embedding(b, monkeypatch):
    gemini(b)
    net = Net(monkeypatch, b.llm)
    b.memory.add("My brother Amit lives in Pune.", auto_tag=False)
    b.llm._ollama_state["at"] = 0
    b.llm._embed_cache.clear()
    net.calls.clear()
    b.core.think("Kuchh nahin sab badhiya, tum batao", background_extract=False)
    ollama = [c for c in net.calls if "11434" in c[1]]
    embeds = [c for c in net.calls if c[1].endswith("/embeddings")]
    assert len(ollama) <= 1, ollama            # was 5, each ~2 s on Windows
    assert len(embeds) == 1, embeds            # was 2
    net.calls.clear()
    b.core.think("aur kya chal raha hai", background_extract=False)
    assert not [c for c in net.calls if "11434" in c[1]]   # cached


def test_localhost_is_rewritten_to_127(b):
    b.settings.save({"order": ["ollama"], "providers": {"ollama": {"base_url": "http://localhost:11434"}}})
    assert b.settings.resolved("ollama")["base_url"] == "http://127.0.0.1:11434"


def test_thinking_is_turned_down_and_unsupported_values_are_skipped(b, monkeypatch):
    gemini(b, model="gemini-3.8-flash")
    net = Net(monkeypatch, b.llm, chat_status={json.dumps({"reasoning_effort": "none"}): 400})
    reply, _ = b.llm.chat("s", [{"role": "user", "content": "hi"}])
    efforts = [p.get("reasoning_effort") for p in net.payloads]
    assert efforts[:2] == ["none", "low"] and reply
    net.payloads.clear()
    b.llm.chat("s", [{"role": "user", "content": "hi again"}])
    assert [p.get("reasoning_effort") for p in net.payloads] == ["low"]   # remembered: no wasted call


def test_groq_gpt_oss_hides_reasoning(b):
    opts = b.llm.reasoning_options("groq", "openai/gpt-oss-120b")
    assert opts[0] == {"reasoning_effort": "low", "include_reasoning": False} and opts[-1] == {}


def test_reply_streams_in_pieces_and_strips_thoughts(b, monkeypatch):
    gemini(b)
    Net(monkeypatch, b.llm, stream_chunks=["<thou", "ght>planning the reply</thou", "ght>Bas ", "badhiya. ", "Tum batao?"])
    events = list(b.core.think_stream("sab badhiya, tum batao", background_extract=False, voice=True))
    texts = [v for k, v in events if k == "text"]
    final = [v for k, v in events if k == "final"][0]
    assert len(texts) >= 2                                   # arrives word by word
    assert "".join(texts) == final["reply"] == "Bas badhiya. Tum batao?"
    assert "thought" not in final["reply"].lower()


def test_stream_endpoint(b, monkeypatch):
    gemini(b)
    Net(monkeypatch, b.llm, stream_chunks=["Hello ", "Rahul!"])
    from fastapi.testclient import TestClient
    with TestClient(b.server.app) as c:
        lines = [json.loads(l) for l in c.post("/api/chat/stream", json={"message": "hi", "voice": True}).text.splitlines()]
    assert [l["type"] for l in lines][-1] == "final"
    assert "".join(l["text"] for l in lines if l["type"] == "text") == "Hello Rahul!"


def test_stream_fails_over_before_first_word(b, monkeypatch):
    b.settings.save({"order": ["gemini"], "providers": {"gemini": {"api_key": "k", "model": "busy", "fallbacks": ["ok"]}}})
    b.llm._cooldown.clear()

    def stream(method, url, **kw):
        model = kw["json"]["model"]

        class Ctx:
            def __enter__(s):
                if model == "busy":
                    return httpx.Response(503, json={"error": {"message": "high demand"}}, request=httpx.Request(method, url))
                body = 'data: {"choices":[{"delta":{"content":"from backup"}}]}\n\ndata: [DONE]'.encode()
                return httpx.Response(200, content=body, request=httpx.Request(method, url))
            def __exit__(s, *a):
                return False
        return Ctx()
    monkeypatch.setattr(b.llm.httpx, "stream", stream)
    out = list(b.llm.chat_stream("s", [{"role": "user", "content": "hi"}]))
    assert out == [("text", "from backup"), ("done", "gemini:ok")]


# ---------- Reply quality ----------

def test_leaked_thoughts_removed_from_old_history(b, monkeypatch):
    gemini(b)
    net = Net(monkeypatch, b.llm)
    b.core._save_message("main", "user", "hello", "mirror")
    b.core._save_message("main", "assistant", "<thought>* User input: hello...</thought>Hey! Kya chal raha hai?", "mirror")
    b.core.think("sab badhiya", background_extract=False)
    sent = net.payloads[-1]["messages"]
    assert all("<thought>" not in m["content"] for m in sent)
    assert any(m["content"] == "Hey! Kya chal raha hai?" for m in sent)


def test_voice_prompt_is_conversational_and_short(b):
    system = b.core.build_system_prompt("mirror", [], voice=True)
    assert "<voice_conversation>" in system and "1 to 3 short sentences" in system
    assert "Hinglish" in system and "Never describe your reasoning" in system
    assert "<voice_conversation>" not in b.core.build_system_prompt("mirror", [])


def test_spoken_turn_uses_short_replies(b, monkeypatch):
    gemini(b)
    net = Net(monkeypatch, b.llm)
    b.core.think("hello", voice=True, background_extract=False)
    assert net.payloads[-1]["max_tokens"] == 350


# ---------- Natural voice ----------

def test_edge_voice_is_default_and_old_browser_setting_upgrades(b):
    assert b.voice.cfg()["tts"] == "edge"
    s = b.settings.load(); s["voice"] = {"tts": "browser"}; b.settings.save(s)
    assert b.voice.cfg()["tts"] == "edge"           # saved before natural voice existed
    s["voice"] = {"tts": "browser", "edge_voice": "en-IN-NeerjaNeural"}; b.settings.save(s)
    assert b.voice.cfg()["tts"] == "browser"        # an explicit later choice is respected


def test_edge_speak_cleans_text_and_returns_mp3(b, monkeypatch):
    import sys, types
    said = {}

    class Communicate:
        def __init__(self, text, voice, **kw):
            said.update(text=text, voice=voice, rate=kw.get("rate"))
        async def stream(self):
            yield {"type": "audio", "data": b"ID3fake"}
            yield {"type": "SentenceBoundary"}
    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    monkeypatch.setattr(b.voice, "has_module", lambda n: True)
    s = b.settings.load(); s["voice"] = {"tts": "edge", "edge_voice": "hi-IN-MadhurNeural", "rate": 1.1}; b.settings.save(s)
    audio, mime = b.voice.speak("**Acha** [W1] see https://x.com (#12) theek hai.")
    assert audio == b"ID3fake" and mime == "audio/mpeg"
    assert said["voice"] == "hi-IN-MadhurNeural" and said["rate"] == "+10%"
    assert "**" not in said["text"] and "https" not in said["text"] and "[W1]" not in said["text"]


def test_edge_voice_never_hangs_when_offline(b, monkeypatch):
    import sys, time, types, asyncio

    class Communicate:
        def __init__(self, *a, **kw):
            pass
        async def stream(self):
            await asyncio.sleep(3600)        # service never answers
            yield {}
    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    monkeypatch.setattr(b.voice, "has_module", lambda n: True)
    import brain.voice as V
    monkeypatch.setattr(V.asyncio if hasattr(V, "asyncio") else asyncio, "wait_for", asyncio.wait_for)
    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(asyncio, "wait_for", lambda coro, timeout: real_wait_for(coro, 0.5))
    start = time.time()
    with pytest.raises(b.voice.VoiceError, match="unavailable"):
        b.voice.speak("hello there")
    assert time.time() - start < 5
