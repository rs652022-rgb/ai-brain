"""Faster replies: kept-open connections, quick move-on from silent models, word-by-word Claude and
Ollama, embeddings that never hold a reply, and a timer on every streamed reply."""
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
    from brain import settings, llm, db, memory, knowledge, core, netpool
    for m in (settings, llm, db, memory, knowledge, core):
        importlib.reload(m)
    db.init(); knowledge.init()
    llm._cooldown.clear(); llm._embed_cache.clear()
    monkeypatch.setattr(netpool, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("off")))
    return type("B", (), dict(settings=settings, llm=llm, memory=memory, core=core, netpool=netpool))


def sse_stream(bodies, seen):
    """A fake httpx.stream: bodies maps a URL part or model name to a body (bytes) or an exception."""
    def stream(method, url, **kw):
        seen.append((url, (kw.get("json") or {}).get("model"), kw.get("timeout")))
        body = next(v for k, v in bodies.items() if k in url or k == (kw.get("json") or {}).get("model"))

        class Ctx:
            def __enter__(s):
                if isinstance(body, Exception):
                    raise body
                return httpx.Response(200, content=body, request=httpx.Request(method, url))

            def __exit__(s, *a):
                return False
        return Ctx()
    return stream


def claude_events(*words):
    lines = [{"type": "message_start"}] + [
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": w}} for w in words] + [{"type": "message_stop"}]
    return "\n\n".join(f"event: x\ndata: {json.dumps(e)}" for e in lines).encode()


def test_one_connection_pool_is_reused_and_keeps_no_cookies(b):
    pool = b.netpool._pool()
    assert pool is b.netpool._pool()
    assert pool.cookies.jar._policy.allowed_domains() == ()


def test_claude_replies_word_by_word(b, monkeypatch):
    b.settings.save({"order": ["anthropic"], "providers": {"anthropic": {"api_key": "k"}}, "embeddings": "none"})
    seen = []
    monkeypatch.setattr(b.llm.httpx, "stream", sse_stream({"/messages": claude_events("Hello", " there", "!")}, seen))
    out = list(b.llm.chat_stream("s", [{"role": "user", "content": "hi"}]))
    assert [v for k, v in out if k == "text"] == ["Hello", " there", "!"]
    assert out[-1][0] == "done" and out[-1][1].startswith("anthropic:")


def test_local_model_replies_word_by_word(b, monkeypatch):
    b.settings.save({"order": ["ollama"], "providers": {}, "embeddings": "none"})
    body = "\n".join(json.dumps({"message": {"content": w}, "done": d}) for w, d in (("Na", False), ("maste", False), ("", True))).encode()
    seen = []
    monkeypatch.setattr(b.llm.httpx, "stream", sse_stream({"/api/chat": body}, seen))
    monkeypatch.setattr(b.llm, "online", lambda: True)
    out = list(b.llm.chat_stream("s", [{"role": "user", "content": "hi"}]))
    assert [v for k, v in out if k == "text"] == ["Na", "maste"]
    assert seen[0][2].read == 60                     # a local model may need loading first


def test_silent_model_is_dropped_quickly_and_skipped_next_time(b, monkeypatch):
    b.settings.save({"order": ["groq", "gemini"], "providers": {"groq": {"api_key": "g"}, "gemini": {"api_key": "m"}},
                     "embeddings": "none"})
    ok = b'data: {"choices":[{"delta":{"content":"from gemini"}}]}\n\ndata: [DONE]'
    seen = []
    monkeypatch.setattr(b.llm.httpx, "stream", sse_stream({"api.groq.com": httpx.ReadTimeout("silent"),
                                                           "generativelanguage": ok}, seen))
    out = list(b.llm.chat_stream("s", [{"role": "user", "content": "hi"}], voice=True))
    assert ("text", "from gemini") in out
    assert seen[0][2].read == 8                      # spoken turn: 8 seconds to start, not a minute
    seen.clear()
    list(b.llm.chat_stream("s", [{"role": "user", "content": "again"}]))
    assert "groq" not in seen[0][0]                  # the silent model goes to the back for a while
    assert seen[0][2].read == 12


def test_slow_embedding_falls_back_to_words_and_is_not_retried_at_once(b, monkeypatch):
    b.settings.save({"order": ["gemini"], "providers": {"gemini": {"api_key": "m"}}, "embeddings": "gemini"})
    calls = []

    def post(url, **kw):
        calls.append((url, kw.get("timeout")))
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(b.llm.httpx, "post", post)
    assert b.llm.embed("first") is None
    assert calls[0][1].read == 4
    assert b.llm.embed("second") is None and len(calls) == 1


def test_no_memories_means_no_embedding_call(b, monkeypatch):
    monkeypatch.setattr(b.llm, "embed", lambda text: pytest.fail("embedded with nothing to search"))
    assert b.memory.search("what did I decide about pricing") == []


def test_streamed_reply_reports_how_long_it_took(b, monkeypatch):
    b.settings.save({"order": ["gemini"], "providers": {"gemini": {"api_key": "m"}}, "embeddings": "none"})
    ok = b'data: {"choices":[{"delta":{"content":"Theek hai."}}]}\n\ndata: [DONE]'
    monkeypatch.setattr(b.llm.httpx, "stream", sse_stream({"generativelanguage": ok}, []))
    monkeypatch.setattr(b.llm, "online", lambda: True)
    events = list(b.core.think_stream("kal ka plan batao", background_extract=False, voice=True))
    t = events[-1][1]["timing"]
    assert 0 <= t["ready_ms"] <= t["first_ms"] <= t["total_ms"]
