"""Recall relevance, the forget command, and friendly errors."""
import importlib

import numpy as np
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
    monkeypatch.setattr(llm, "private_json", lambda s, p: None)
    return type("B", (), dict(memory=memory, knowledge=knowledge, core=core, llm=llm, server=server, monkeypatch=monkeypatch))


def realistic_embeddings(b, topics):
    """Like real models: everything is somewhat similar (shared direction), same-topic texts much more so."""
    rng = np.random.default_rng(7)
    base, dirs = rng.normal(size=96), {t: rng.normal(size=96) for t in topics}

    def embed(text):
        low = text.lower()
        v = base * 1.0 + rng.normal(size=96) * 0.35
        for t, d in dirs.items():
            if t in low:
                v = v + d * 1.6
        return list(v / np.linalg.norm(v))
    b.monkeypatch.setattr(b.llm, "embed", embed)
    b.monkeypatch.setattr(b.llm, "embedding_label", lambda: "test:embed")


def seed(b):
    for t in ["Learned some mathematics: algebra and equations", "mathematics: geometry and circles",
              "mathematics quiz answers 7 x 8 = 56", "My brother Amit lives in Pune", "I prefer chai over coffee",
              "Client call with Dubai team every Monday"]:
        b.memory.add(t, auto_tag=False)


@pytest.mark.parametrize("msg", ["hello", "Hi!", "kuchh nahin sab badhiya, tum batao", "kaise ho bhai", "thanks", "good morning"])
def test_small_talk_recalls_nothing(b, msg):
    realistic_embeddings(b, ["mathematics", "brother", "chai", "client"])
    seed(b)
    assert b.memory.search(msg) == []


def test_relevant_things_are_still_found(b):
    realistic_embeddings(b, ["mathematics", "brother", "chai", "client"])
    seed(b)
    got = [m["content"] for m in b.memory.search("Where does my brother live?")]
    assert got and "Amit" in got[0] and not any("mathematics" in g for g in got)
    got = [m["content"] for m in b.memory.search("When is my client call?")]
    assert got and "Client call" in got[0]


def test_hinglish_fillers_do_not_match(b):
    b.monkeypatch.setattr(b.llm, "embed", lambda t: None)
    b.memory.add("Aaj kya hai: mathematics test", auto_tag=False)
    assert b.memory.search("aaj kya plan hai") == [] or all("mathematics" not in m["content"] for m in b.memory.search("tum kya kar rahe ho"))
    assert b.memory.search("tum kya kar rahe ho") == []


def test_forget_command_lists_then_deletes_only_on_click(b):
    b.monkeypatch.setattr(b.llm, "embed", lambda t: None)
    seed(b)
    b.knowledge.learn_note("Mathematics notes: trigonometry basics", title="Math notes")
    r = b.core.think("mathematics topics remove these from your memory because I was not talking about Mathematics",
                     background_extract=False)
    assert r["action"] == "forget" and r["forget"]["topic"].lower() == "mathematics"
    ids = [m["id"] for m in r["forget"]["memories"]]
    assert len(ids) == 3 and r["forget"]["knowledge"]
    assert len(b.memory.list_all()) == 6                      # nothing deleted yet
    from fastapi.testclient import TestClient
    with TestClient(b.server.app) as c:
        res = c.post("/api/forget", json={"memory_ids": ids, "source_ids": [k["source_id"] for k in r["forget"]["knowledge"]],
                                          "clear_chat": True}).json()
    assert res == {"memories": 3, "knowledge": 1, "chat_cleared": True}
    left = [m["content"] for m in b.memory.list_all()]
    assert len(left) == 3 and not any("mathematics" in t for t in left)
    assert b.core.history() == []


def test_quota_error_is_explained_plainly(b):
    msg = b.llm.friendly_failure(["gemini: rate limit or free quota reached (You exceeded your current quota) [gemma]",
                                  "ollama: cannot connect (ConnectError)"])
    main, details = msg.split("\n\nDetails: ")
    assert "used up their free limit" in main and "Groq" in main and "quota" in details
