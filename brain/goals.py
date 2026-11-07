"""Goals with budgets, and steering work while it runs (like OpenClaw's session goals, /steer and budgets).

A goal is a longer job the Brain keeps working on by itself, round after round, using its tools,
until it's done or its budget runs out: e.g. "Find 20 restaurants in Jaipur without a website, with phone
numbers" with a budget of 40 model requests. You can pause it, stop it, or steer it ("only Malviya Nagar")
at any time; the next round follows your note.

Steering also works on a normal chat answer that is still using tools, and on device tasks.
"""
import threading
import time
import uuid

from . import config, db, usage

GOAL_DONE = "GOAL_DONE:"
_goals: dict[str, dict] = {}
_lock = threading.Lock()

# Notes typed while a chat answer is still working: (user, conversation) -> [notes]
_steer: dict[tuple, list[str]] = {}
_abort: set = set()


def steer_chat(conversation: str, text: str) -> None:
    _steer.setdefault((config.user_key(), conversation), []).append(text.strip()[:1000])
    db.audit("owner", "chat.steer", conversation=conversation)


def abort_chat(conversation: str) -> None:
    _abort.add((config.user_key(), conversation))


def take_steering(conversation: str) -> list[str]:
    return _steer.pop((config.user_key(), conversation), [])


def aborted(conversation: str) -> bool:
    key = (config.user_key(), conversation)
    if key in _abort:
        _abort.discard(key)
        return True
    return False


# ---------- Goals ----------

def _public(g: dict) -> dict:
    return {k: v for k, v in g.items() if not k.startswith("_")}


def mine() -> list[dict]:
    me = config.user_key()
    return [_public(g) for g in sorted(_goals.values(), key=lambda x: -x["created"]) if g["_owner"] == me]


def get(gid: str) -> dict | None:
    g = _goals.get(gid)
    return g if g and g["_owner"] == config.user_key() else None


def start(text: str, max_requests: int = 30, max_tokens: int = 200_000, deliver: str = "web") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Say what the goal is.")
    g = {"id": uuid.uuid4().hex[:8], "text": text[:2000], "status": "running", "max_requests": max(1, min(500, int(max_requests))),
         "max_tokens": max(1000, min(5_000_000, int(max_tokens))), "requests": 0, "tokens": 0, "rounds": 0,
         "log": [], "notes": [], "result": "", "deliver": deliver, "created": time.time(),
         "_owner": config.user_key(), "_pause": threading.Event(), "_stop": threading.Event()}
    g["_pause"].set()                          # set = allowed to run
    with _lock:
        _goals[g["id"]] = g
    db.audit("owner", "goal.start", goal=g["id"], max_requests=g["max_requests"])
    config.spawn(_run, g)
    return _public(g)


def control(gid: str, action: str, note: str = "") -> dict:
    g = get(gid)
    if not g:
        raise ValueError("No such goal.")
    if action == "pause":
        g["_pause"].clear()
        g["status"] = "paused"
    elif action == "resume":
        g["status"] = "running"
        g["_pause"].set()
    elif action == "stop":
        g["_stop"].set()
        g["_pause"].set()
    elif action == "steer" and note.strip():
        g["notes"].append(note.strip()[:1000])
        g["log"].append({"at": time.time(), "text": f"You: {note.strip()[:300]}"})
    elif action == "budget":
        g["max_requests"] += 20
        if g["status"] == "over budget":
            g["status"] = "running"
            g["_stop"].clear()
            config.spawn(_run, g)
    db.audit("owner", f"goal.{action}", goal=gid)
    return _public(g)


ROUND_PROMPT = """You are working on a goal for the owner, step by step, on your own.
GOAL: {text}

Progress so far:
{progress}
{notes}
Do the next useful part of the work now, using your tools. Then reply with a short progress note.
When the goal is fully done, reply with "GOAL_DONE:" followed by the final result for the owner.
Budget left: about {left} model requests. Finish before it runs out; if it can't be done, say so with GOAL_DONE:."""


def _run(g: dict) -> None:
    from . import core
    meter = {"requests": g["requests"], "tokens": g["tokens"], "max_requests": g["max_requests"], "max_tokens": g["max_tokens"]}
    tok = usage.meter.set(meter)
    try:
        while not g["_stop"].is_set():
            g["_pause"].wait()
            if g["_stop"].is_set():
                break
            notes = g["notes"]
            g["notes"] = []
            progress = "\n".join(f"- {x['text'][:400]}" for x in g["log"][-12:]) or "- (just started)"
            prompt = ROUND_PROMPT.format(text=g["text"], progress=progress,
                                         notes=("\nThe owner's latest instructions (follow them):\n" + "\n".join(f"- {n}" for n in notes) + "\n") if notes else "",
                                         left=max(0, g["max_requests"] - meter["requests"]))
            try:
                out = core.think(prompt, mode="assistant", conversation=f"goal:{g['id']}", background_extract=False)
                reply = out["reply"]
            except Exception as exc:
                reply = str(exc)
            g["requests"], g["tokens"], g["rounds"] = meter["requests"], meter["tokens"], g["rounds"] + 1
            if "budget is used up" in reply or "limit of" in reply:
                g["status"] = "over budget"
                g["result"] = "I ran out of budget before finishing. Give it more budget to continue."
                break
            if GOAL_DONE in reply:
                g["status"], g["result"] = "done", reply.split(GOAL_DONE, 1)[1].strip()
                g["log"].append({"at": time.time(), "text": "Done."})
                break
            g["log"].append({"at": time.time(), "text": reply[:1500]})
            if meter["requests"] >= g["max_requests"] or meter["tokens"] >= g["max_tokens"]:
                g["status"] = "over budget"
                g["result"] = "I used the whole budget. Progress is in the log; add budget to continue."
                break
        else:
            g["status"] = "stopped"
    finally:
        usage.meter.reset(tok)
    if g["_stop"].is_set() and g["status"] not in ("done",):
        g["status"] = "stopped"
    db.audit("brain", "goal.end", goal=g["id"], status=g["status"], requests=g["requests"])
    if g["status"] in ("done", "over budget"):
        text = f"🎯 Goal: {g['text'][:80]}\n\n{g['result'] or 'Finished.'}"
        try:
            if g["deliver"] == "web":
                core.deliver_to_chat(text)
            else:
                from . import channels
                channels.send_owner(g["deliver"], text)
        except Exception:
            core.deliver_to_chat(text)
