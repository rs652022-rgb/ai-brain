"""Voice turn-taking, Hindi voices and settings that must not change by themselves."""
import importlib

import pytest


@pytest.fixture()
def v(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_NO_SCHEDULER", "1")
    from brain import config
    importlib.reload(config)
    from brain import settings, llm, db, memory, web, knowledge, voice, core, identity, interview, importer, server
    for m in (settings, llm, db, memory, web, knowledge, voice, core, identity, interview, importer, server):
        importlib.reload(m)
    db.init(); knowledge.init()
    monkeypatch.setattr(llm, "chat", lambda *a, **k: ("answer", "fake"))
    monkeypatch.setattr(llm, "embed", lambda t: None)
    monkeypatch.setattr(llm, "private_json", lambda s, p: None)
    return type("V", (), dict(voice=voice, core=core, settings=settings, llm=llm, mp=monkeypatch))


# ---------- Settings stay as saved ----------

def test_page_save_keeps_voice_keys_and_discovery_mark(v):
    current = {"voice": {"tts": "edge", "edge_voice": "hi-IN-SwaraNeural", "rate": 1.1},
               "providers": {"groq": {"api_key": "gsk_real", "model": "m1", "auto_discovered": True}},
               "thinking": "deep"}
    incoming = {"voice": {"rate": 1.2, "edge_voice": None},
                "providers": {"groq": {"api_key": "••••real", "model": "m1"}}}
    out = v.settings.merge_page_save(current, incoming)
    assert out["voice"] == {"tts": "edge", "edge_voice": "hi-IN-SwaraNeural", "rate": 1.2}
    assert out["providers"]["groq"]["api_key"] == "gsk_real"
    assert out["providers"]["groq"]["auto_discovered"] is True
    assert out["thinking"] == "deep"


def test_empty_voice_form_does_not_wipe_voice(v):
    out = v.settings.merge_page_save({"voice": {"tts": "edge"}}, {"voice": {}})
    assert out["voice"] == {"tts": "edge"}


def test_background_discovery_keeps_users_model(v):
    s = v.settings.load()
    s["providers"]["groq"] = {"api_key": "gsk_x", "model": "my-choice"}
    v.settings.save(s)
    v.mp.setattr(v.llm, "list_models", lambda pid: ["fast-a", "fast-b"])
    v.mp.setattr(v.llm, "_is_free", lambda pid, m: True)
    v.mp.setattr(v.llm, "_try_model", lambda p, m: (m, True, "", 1.0))
    v.llm.discover("groq", keep_user_model=True)
    p = v.settings.load()["providers"]["groq"]
    assert p["model"] == "my-choice" and p["auto_discovered"] is True
    assert set(p["fallbacks"]) <= {"fast-a", "fast-b"} and p["fallbacks"]


def test_barge_in_setting_is_kept(v):
    assert v.voice.DEFAULTS["barge_in"] is True
    s = v.settings.load(); s["voice"] = {"barge_in": False}
    v.settings.save(s)
    assert v.voice.cfg()["barge_in"] is False


# ---------- Hindi voice ----------

def test_hindi_text_gets_a_hindi_voice(v):
    assert v.voice.has_hindi("हाँ, बस two minutes") and not v.voice.has_hindi("just two minutes")
    assert v.voice.edge_voice_for("हाँ, मैं कर दूँगा", "en-IN-PrabhatNeural", "en").startswith("hi-IN")
    assert v.voice.edge_voice_for("Sure, done.", "en-IN-PrabhatNeural", "en") == "en-IN-PrabhatNeural"


def test_voice_prompt_asks_for_devanagari(v):
    assert "Devanagari" in v.core._VOICE_STYLE and v.core.INTERRUPTED in v.core._VOICE_STYLE


# ---------- Talking over the Brain ----------

def _stream(v, pieces):
    def fake(system, msgs, **kw):
        for p in pieces:
            yield "text", p
        yield "done", "fake:model"
    v.mp.setattr(v.llm, "chat_stream", fake)


def test_interrupt_shortens_saved_reply(v):
    _stream(v, ["First part. ", "Second part. ", "Third part."])
    list(v.core.think_stream("tell me a story", background_extract=False))
    assert v.core.voice_interrupted("main", "First part.") is True
    last = v.core.history("main")[-1]
    assert last["role"] == "assistant" and last["content"] == "First part. [interrupted]"
    assert v.core.voice_interrupted("main", "First part.") is False      # already marked


def test_hanging_up_mid_reply_saves_what_was_heard(v):
    _stream(v, ["Hello there. ", "More words that were never said."])
    gen = v.core.think_stream("hi brain, explain taxes to me", background_extract=False)
    for kind, _ in gen:
        if kind == "text":
            break
    gen.close()
    last = v.core.history("main")[-1]
    assert last["role"] == "assistant" and last["content"] == "Hello there. [interrupted]"
