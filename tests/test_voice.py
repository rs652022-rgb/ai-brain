"""Voice tests: commands, transcription routing, speech cleanup and settings."""
import base64
import importlib

import httpx
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
    return type("V", (), dict(voice=voice, core=core, memory=memory, settings=settings, server=server))


def test_spoken_everyday_commands(v):
    r = v.core.think("switch to coach mode", background_extract=False)
    assert r["action"] == "mode" and r["new_mode"] == "coach"
    r = v.core.think("Add to my journal: met a new client from Dubai today", background_extract=False)
    assert r["action"] == "journal"
    assert v.memory.get(r["memory_id"])["source"] == "journal"
    r = v.core.think("remember that my GST return is due on the 20th", background_extract=False)
    assert r["action"] == "remember" and "GST return" in v.memory.get(r["memory_id"])["content"]
    for word in ("stop", "Stop!", "ruko", "be quiet"):
        assert v.core.think(word)["action"] == "stop"


def test_groq_transcription(v, monkeypatch):
    s = v.settings.load(); s["providers"]["groq"] = {"api_key": "gsk_x"}; s["voice"] = {"stt": "groq", "language": "en"}
    v.settings.save(s)
    seen = {}

    def fake_post(url, **kw):
        seen.update(url=url, **kw)
        return httpx.Response(200, json={"text": " Learn about AI voice agents "}, request=httpx.Request("POST", url))
    monkeypatch.setattr(v.voice.httpx, "post", fake_post)
    assert v.voice.transcribe(b"x" * 5000) == "Learn about AI voice agents"
    assert seen["url"].endswith("/audio/transcriptions") and seen["data"]["language"] == "en"
    assert seen["headers"]["Authorization"] == "Bearer gsk_x"


def test_transcription_errors_are_clear(v):
    with pytest.raises(v.voice.VoiceError, match="empty"):
        v.voice.transcribe(b"tiny", engine="groq")
    with pytest.raises(v.voice.VoiceError, match="Groq key"):
        v.voice.transcribe(b"x" * 5000, engine="groq")


def test_api_transcribe_and_speak_without_piper_voice(v, monkeypatch):
    from fastapi.testclient import TestClient
    s = v.settings.load(); s["voice"] = {"tts": "piper", "edge_voice": "en-IN-PrabhatNeural"}; v.settings.save(s)
    monkeypatch.setattr(v.voice, "transcribe", lambda audio, filename="": "hello brain")
    with TestClient(v.server.app) as c:
        r = c.post("/api/voice/transcribe", json={"data_base64": base64.b64encode(b"x" * 5000).decode()})
        assert r.json()["text"] == "hello brain"
        r = c.post("/api/voice/speak", json={"text": "hi"})
        assert r.status_code == 400 and ("not downloaded" in r.json()["detail"] or "not installed" in r.json()["detail"])
        info = c.get("/api/voice").json()
        assert info["config"]["stt"] == "browser" and "piper_installed" in info


def test_voice_settings_saved_and_validated(v):
    from fastapi.testclient import TestClient
    with TestClient(v.server.app) as c:
        body = c.get("/api/settings").json()["settings"]
        body["voice"] = {"stt": "local", "tts": "piper", "rate": 1.2, "language": "hi", "evil": "x", "speak_replies": "yes"}
        c.put("/api/settings", json=body)
    cfg = v.voice.cfg()
    assert cfg["stt"] == "local" and cfg["tts"] == "piper" and cfg["rate"] == 1.2 and cfg["language"] == "hi"
    assert "evil" not in cfg and cfg["speak_replies"] is True     # wrong types are ignored


def test_speech_cleanup():
    from brain.voice import clean_for_speech
    text = ("**Groq** allows 30 requests/min [W1] (#12).\n- Free tier\n```py\nprint(1)\n```\n"
            "Sources:\n[1] Groq — https://groq.com/pricing")
    out = clean_for_speech(text)
    assert "Groq allows 30 requests/min" in out and "Free tier" in out and "code shown on screen" in out
    for gone in ("**", "[W1]", "#12", "https://", "Sources", "print(1)"):
        assert gone not in out


def test_piper_voice_name_is_validated(v):
    with pytest.raises(v.voice.VoiceError, match="look like"):
        v.voice.download_piper_voice("../../etc/passwd")
