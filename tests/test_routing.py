"""Picking the right free model: message sorting, live-speed ordering, racing two models for the first
word, the self-check on hard answers, and Deep mode."""
import importlib
import threading
import time

import httpx
import pytest


@pytest.fixture()
def b(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_NO_SCHEDULER", "1")
    from brain import config
    importlib.reload(config)
    from brain import settings, llm, db, memory, knowledge, core, netpool, routing, refine
    for m in (settings, llm, db, memory, knowledge, routing, refine, core):
        importlib.reload(m)
    db.init(); knowledge.init()
    llm._cooldown.clear(); llm._embed_cache.clear(); routing.reset()
    monkeypatch.setattr(netpool, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("off")))
    return type("B", (), dict(settings=settings, llm=llm, core=core, routing=routing, refine=refine))


# ---------- Sorting messages ----------

@pytest.mark.parametrize("text,level,kind", [
    ("hi", "trivial", "chat"),
    ("thanks!", "trivial", "chat"),
    ("what's a good name for a cat?", "normal", "chat"),
    ("write a short poem about rain", "normal", "writing"),
    ("my python script throws a traceback when I open the file twice in a row, how do I fix it", "hard", "code"),
    ("solve for x: 3x + 7 = 22", "hard", "math"),
    ("compare the trade-offs of postgres and sqlite for a small app with many readers and few writers", "hard",
     "reasoning"),
])
def test_classify(b, text, level, kind):
    found = b.routing.classify(text)
    assert (found["level"], found["kind"]) == (level, kind)


def test_fresh_questions_are_spotted(b):
    assert b.routing.classify("what's the latest news on the election")["fresh"]
    assert not b.routing.classify("explain recursion")["fresh"]


def test_task_for_levels(b):
    assert b.routing.task_for("hard") == "smart"
    assert b.routing.task_for("trivial") == "fast"
    assert b.routing.task_for("normal") == "balanced"
    assert b.routing.task_for("normal", "fastest") == "fast"


# ---------- Ordering by quality and live speed ----------

def _cands():
    slow, quick = {"id": "openrouter"}, {"id": "groq"}
    return [(slow, "gpt-oss-120b"), (quick, "llama-3.1-8b-instant")]


def test_fast_order_follows_measured_speed(b):
    never = lambda pid, m: False
    for _ in range(3):
        b.routing.observe("openrouter", "gpt-oss-120b", True, 4.0)
        b.routing.observe("groq", "llama-3.1-8b-instant", True, 0.3)
    order = b.routing.order(_cands(), "fast", never)
    assert order[0][0]["id"] == "groq"


def test_smart_order_prefers_quality_and_resting_goes_last(b):
    never = lambda pid, m: False
    assert b.routing.order(_cands(), "smart", never)[0][1] == "gpt-oss-120b"
    resting = lambda pid, m: pid == "openrouter"
    assert b.routing.order(_cands(), "smart", resting)[-1][0]["id"] == "openrouter"


def test_balanced_keeps_your_order_until_one_is_much_slower(b):
    never = lambda pid, m: False
    assert b.routing.order(_cands(), "balanced", never)[0][0]["id"] == "openrouter"
    for _ in range(3):
        b.routing.observe("openrouter", "gpt-oss-120b", True, 9.0)
        b.routing.observe("groq", "llama-3.1-8b-instant", True, 0.4)
    assert b.routing.order(_cands(), "balanced", never)[0][0]["id"] == "groq"


def test_failing_model_is_moved_back(b):
    never = lambda pid, m: False
    for _ in range(5):
        b.routing.observe("openrouter", "gpt-oss-120b", False)
    assert b.routing.order(_cands(), "balanced", never)[0][0]["id"] == "groq"


# ---------- Racing for the first word ----------

def test_race_starts_the_backup_when_the_first_is_silent_and_never_mixes(b):
    stopped = threading.Event()

    def run(cand):
        if cand == "slow":
            def slow():
                time.sleep(1.5)
                if not stopped.is_set():
                    yield "SLOW"
            return slow()
        return iter(["fast ", "answer"])

    errors = []
    out = list(b.routing.race(["slow", "fast"], run, 0.1, lambda c, e: errors.append(c)))
    stopped.set()
    assert out[0] == ("win", "fast")
    assert "".join(v for k, v in out if k == "text") == "fast answer"
    assert out[-1] == ("end", None)
    assert not errors


def test_race_moves_on_after_an_error_and_reports_it(b):
    def run(cand):
        if cand == "bad":
            raise RuntimeError("boom")
        return iter(["ok"])

    errors = []
    out = list(b.routing.race(["bad", "good"], run, None, lambda c, e: errors.append((c, str(e)))))
    assert errors == [("bad", "boom")]
    assert ("win", "good") in out


def test_race_ends_quietly_when_everything_fails(b):
    errors = []
    out = list(b.routing.race(["a", "b"], lambda c: iter([]), None, lambda c, e: errors.append(c)))
    assert out == [] and errors == ["a", "b"]


def test_race_keeps_the_user_folder_in_worker_threads(b):
    from brain import config
    token = config._user_dir.set("someone")
    try:
        out = list(b.routing.race(["x"], lambda c: iter([str(config._user_dir.get())]), None, lambda c, e: None))
    finally:
        config._user_dir.reset(token)
    assert ("text", "someone") in out


# ---------- Self-check on hard answers ----------

def _ctx(level="hard", kind="code"):
    return {"system": "s", "history": [{"role": "user", "content": "fix my code"}], "max_tokens": 2400,
            "route": {"level": level, "kind": kind, "task": "smart"}}


def test_self_check_only_for_hard_code_maths_reasoning(b):
    assert b.refine.wants_check({"level": "hard", "kind": "code"}, private=False)
    assert not b.refine.wants_check({"level": "normal", "kind": "code"}, private=False)
    assert not b.refine.wants_check({"level": "hard", "kind": "writing"}, private=False)
    assert not b.refine.wants_check({"level": "hard", "kind": "code"}, private=True)


def test_self_check_can_be_switched_off(b):
    b.settings.save(b.settings.load() | {"self_check": False})
    assert not b.refine.wants_check({"level": "hard", "kind": "code"}, private=False)


def test_self_check_fixes_a_real_problem_with_a_second_model(b, monkeypatch):
    calls = []

    def fake_chat(system, messages, **kw):
        calls.append(kw)
        if kw.get("json_mode"):
            return '{"ok": false, "problems": ["off by one in the loop"]}', "groq:fast"
        return "fixed answer", "gemini:pro"

    monkeypatch.setattr(b.llm, "chat", fake_chat)
    reply, model, problems = b.refine.check_and_fix(_ctx(), "first answer", "cerebras:gpt-oss-120b")
    assert (reply, model, problems) == ("fixed answer", "gemini:pro", ["off by one in the loop"])
    assert calls[0]["avoid"] == "cerebras:gpt-oss-120b"      # the checker is a different model
    assert calls[1]["task"] == "smart"


def test_self_check_keeps_a_good_answer(b, monkeypatch):
    monkeypatch.setattr(b.llm, "chat", lambda s, m, **kw: ('{"ok": true}', "groq:fast"))
    assert b.refine.check_and_fix(_ctx(), "good", "x:y") == ("good", "x:y", [])


def test_self_check_failing_never_breaks_the_answer(b, monkeypatch):
    def down(s, m, **kw):
        raise b.llm.ModelUnavailable("all busy")
    monkeypatch.setattr(b.llm, "chat", down)
    assert b.refine.check_and_fix(_ctx(), "answer", "x:y") == ("answer", "x:y", [])


def test_think_runs_the_check_only_on_hard_questions(b, monkeypatch):
    b.settings.save({"order": ["groq"], "providers": {"groq": {"api_key": "k"}}, "embeddings": "none"})
    checks = []
    monkeypatch.setattr(b.llm, "chat", lambda s, m, **kw: ("an answer", "groq:m"))
    monkeypatch.setattr(b.refine, "critique", lambda q, a, used="": checks.append(q) or [])
    out = b.core.think("hi", background_extract=False)
    assert not checks and "checked" not in out
    out = b.core.think("my python function raises a KeyError when the dict is empty, how do I fix this bug",
                       background_extract=False)
    assert len(checks) == 1 and out["checked"] == {"problems": 0}


def test_hard_questions_get_the_smart_task_and_more_room(b, monkeypatch):
    b.settings.save({"order": ["groq"], "providers": {"groq": {"api_key": "k"}}, "embeddings": "none",
                     "self_check": False})
    seen = []
    monkeypatch.setattr(b.llm, "chat", lambda s, m, **kw: seen.append((s, kw)) or ("ok", "groq:m"))
    b.core.think("prove that the square root of two is irrational", background_extract=False)
    system, kw = seen[-1]
    assert kw["task"] == "smart" and kw["kind"] == "math" and kw["max_tokens"] == 2400
    assert b.refine.HARD_PROMPT in system
    b.core.think("hello", background_extract=False)
    assert seen[-1][1]["task"] == "fast" and b.refine.HARD_PROMPT not in seen[-1][0]


# ---------- Deep mode ----------

def test_deep_asks_several_models_and_merges_once(b, monkeypatch):
    b.settings.save({"order": ["groq", "cerebras", "gemini"], "embeddings": "none",
                     "providers": {p: {"api_key": "k"} for p in ("groq", "cerebras", "gemini")}})
    drafted, merged = [], []
    monkeypatch.setattr(b.llm, "call", lambda p, s, m, **kw: drafted.append(p["id"]) or f"draft from {p['id']}")
    monkeypatch.setattr(b.llm, "chat", lambda s, m, **kw: merged.append(s) or ("merged", "gemini:pro"))
    reply, model = b.refine.deep(_ctx(kind="reasoning"))
    assert sorted(drafted) == ["cerebras", "gemini", "groq"]
    assert len(merged) == 1 and "draft from groq" in merged[0] and "<expert_drafts>" in merged[0]
    assert reply == "merged" and model.startswith("deep(3)")


def test_deep_uses_fusion_when_freellmapi_is_set_up(b, monkeypatch):
    b.settings.save({"order": ["freellmapi", "groq"], "embeddings": "none",
                     "providers": {"freellmapi": {"api_key": "k"}, "groq": {"api_key": "k"}}})
    asked = []
    monkeypatch.setattr(b.llm, "chat", lambda s, m, **kw: asked.append(kw.get("prefer")) or ("fused", "freellmapi:fusion"))
    monkeypatch.setattr(b.llm, "call", lambda *a, **kw: pytest.fail("should not draft when fusion works"))
    assert b.refine.deep(_ctx()) == ("fused", "freellmapi:fusion")
    assert asked == [("freellmapi", "fusion")]


def test_deep_is_off_by_default_and_never_for_sealed_memories(b):
    assert not b.refine.wants_deep(None, private=False)
    assert b.refine.wants_deep(True, private=False)
    assert not b.refine.wants_deep(True, private=True)


def test_new_settings_are_cleaned(b):
    b.settings.save(b.settings.load() | {"speed_mode": "warp", "hedge_ms": "99999", "deep_mode": 1})
    data = b.settings.load()
    assert data["speed_mode"] == "balanced" and data["hedge_ms"] == 10000 and data["deep_mode"] is True


def test_background_runners_skip_the_self_check_and_deep(b, monkeypatch):
    b.settings.save({"order": ["groq"], "providers": {"groq": {"api_key": "k"}}, "embeddings": "none",
                     "deep_mode": True})
    monkeypatch.setattr(b.llm, "chat", lambda s, m, **kw: ("an answer", "groq:m"))
    monkeypatch.setattr(b.refine, "critique", lambda *a, **kw: pytest.fail("no self-check for background work"))
    monkeypatch.setattr(b.refine, "deep", lambda ctx: pytest.fail("no Deep mode for background work"))
    hard = "my python function raises a KeyError when the dict is empty, how do I fix this bug"
    for conv in ("goal:ab12", "job:1", "workflow:x", "mailwatch"):
        out = b.core.think(hard, conversation=conv, background_extract=False)
        assert out["reply"] == "an answer" and "checked" not in out
    assert b.refine.for_person("main") and b.refine.for_person("telegram:42")
