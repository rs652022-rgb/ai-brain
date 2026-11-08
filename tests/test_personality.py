"""Personality and gender: saved in Master Control, used in every reply, and the voice follows the gender."""
import importlib
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def b(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_NO_SCHEDULER", "1")
    from brain import config
    importlib.reload(config)
    from brain import settings, master, voice, core
    for m in (settings, master, voice, core):
        importlib.reload(m)
    return type("B", (), dict(master=master, voice=voice, core=core, settings=settings))


def test_defaults_are_male_with_no_extra_traits(b):
    m = b.master.public()
    assert m["gender"] == "male" and m["personality"] == [] and m["behavior_note"] == ""


def test_traits_are_saved_and_unknown_ones_dropped(b):
    m = b.master.update({"personality": ["sharp", "calm", "evil", 3], "behavior_note": "  Be   honest.  "})
    assert m["personality"] == ["calm", "sharp"] and m["behavior_note"] == "Be honest."
    assert b.master.update({"personality": "calm"})["personality"] == []      # not a list: cleared


def test_unknown_gender_is_ignored(b):
    assert b.master.update({"gender": "robot"})["gender"] == "male"


def test_female_switches_the_voice_to_its_female_partner(b):
    b.settings.save(b.settings.load() | {"voice": {"edge_voice": "en-US-AndrewMultilingualNeural", "rate": 1.1}})
    b.master.update({"gender": "female"})
    v = b.voice.cfg()
    assert v["edge_voice"] == "en-US-EmmaMultilingualNeural" and v["rate"] == 1.1
    b.master.update({"gender": "male"})
    assert b.voice.cfg()["edge_voice"] == "en-US-AndrewMultilingualNeural"


def test_a_voice_that_already_matches_is_kept(b):
    b.settings.save(b.settings.load() | {"voice": {"edge_voice": "en-IN-NeerjaExpressiveNeural"}})
    b.master.update({"gender": "female"})
    assert b.voice.cfg()["edge_voice"] == "en-IN-NeerjaExpressiveNeural"


def test_default_voice_changes_with_gender(b):
    b.master.update({"gender": "female"})
    assert b.voice.cfg()["edge_voice"] == "en-IN-NeerjaNeural"


def _prompt(b):
    return b.core.build_system_prompt("assistant", [], voice=True)


def test_prompt_uses_masculine_forms_for_male(b):
    p = _prompt(b)
    assert "karunga, jaunga, bataunga" in p and "karungi" not in p


def test_prompt_uses_feminine_forms_for_female(b):
    b.master.update({"gender": "female", "personality": ["caring", "understanding"], "behavior_note": "Cheer me up."})
    p = _prompt(b)
    assert "karungi, jaungi, bataungi" in p and "करूँगी" in p and "karunga," not in p
    assert "Caring:" in p and "Understanding:" in p and "Calm:" not in p and "Cheer me up." in p


def test_patience_setting_is_checked(b):
    b.settings.save(b.settings.load() | {"voice": {"patience": "relaxed"}})
    assert b.voice.cfg()["patience"] == "relaxed"
    b.settings.save(b.settings.load() | {"voice": {"patience": "forever"}})
    assert b.voice.cfg()["patience"] == "normal"


JS_CHECK = r"""
const fs = require("fs"), vm = require("vm");
const ctx = { V: { cfg: { patience: "normal" } }, performance, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const r = vm.runInContext(`({
  done: turnEndDelay("Book a table for two tonight."),
  mid: turnEndDelay("I want to go there and"),
  hindi: turnEndDelay("mujhe kal jaana hai matlab"),
  short: turnEndDelay("wait"),
  echo: newWords("hello there how are you", "Hello there! How are you?").length,
  fresh: newWords("no stop wait", "Hello there, how are you?").length,
})`, ctx);
ctx.V.cfg.patience = "relaxed";
r.relaxed = vm.runInContext(`turnEndDelay("Book a table for two tonight.")`, ctx);
console.log(JSON.stringify(r));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")
def test_turn_taking_waits_longer_mid_thought(tmp_path):
    script = tmp_path / "check.js"
    script.write_text(JS_CHECK, encoding="utf-8")
    js = Path(__file__).resolve().parent.parent / "web" / "voice-duplex.js"
    out = subprocess.run(["node", str(script), str(js)], capture_output=True, text=True, timeout=30, check=True)
    import json
    r = json.loads(out.stdout)
    assert r["done"] >= 1200                          # lets you finish: no answer after a short breath
    assert r["mid"] > r["done"] and r["hindi"] > r["done"] and r["short"] > r["done"]
    assert r["relaxed"] > r["done"]
    assert r["echo"] == 0 and r["fresh"] == 3        # its own voice isn't an interruption; yours is
