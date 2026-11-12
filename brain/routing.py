"""Picking the right free model for each message, fast.

- classify(): how hard a message is (trivial / normal / hard) and what kind (code, math, ...), from cheap
  text signals only, so sorting a message costs no model call.
- observe() / speed(): live timings for every provider:model, so slow ones move back on their own.
- order(): sorts (provider, model) candidates for a task: "fast", "balanced" or "smart".
- race(): starts the best candidate and, if it hasn't said a word within a short wait, also starts the
  next one. The first to answer wins; the others are stopped. Words are never mixed from two models.
"""
import contextvars
import json
import queue
import re
import threading
from collections import deque
from pathlib import Path

_lock = threading.Lock()
_stats: dict[str, deque] = {}          # "provider:model" -> recent (ok, first_word_seconds)
WINDOW = 20
DEFAULT_SPEED = 2.5


# ---------- Quality table ----------

def _load_table() -> tuple[list, dict]:
    try:
        data = json.loads((Path(__file__).with_name("model_quality.json")).read_text(encoding="utf-8"))
        models = [(re.compile(p, re.I), float(q), set(good)) for p, q, good in data.get("models", [])]
        speeds = {k: float(v) for k, v in data.get("provider_speed", {}).items() if not k.startswith("_")}
        return models, speeds
    except (OSError, ValueError, TypeError, re.error):
        return [], {}


_TABLE, _PROVIDER_SPEED = _load_table()


def table_entry(model: str) -> tuple[float, set] | None:
    for pat, q, good in _TABLE:
        if pat.search(model or ""):
            return q, good
    return None


def quality(model: str, kind: str = "") -> float:
    """0-100. Known families come from model_quality.json, others from the name-based guess."""
    entry = table_entry(model)
    if entry is None:
        from .llm import _name_rank
        return 40 + _name_rank(model)
    q, good = entry
    return q + (6 if kind and kind in good else 0)


# ---------- Live timings ----------

def _key(pid: str, model: str) -> str:
    return f"{pid}:{model}"


def observe(pid: str, model: str, ok: bool, first_word: float | None = None) -> None:
    with _lock:
        d = _stats.setdefault(_key(pid, model), deque(maxlen=WINDOW))
        d.append((bool(ok), first_word if ok and first_word else None))


def speed(pid: str, model: str) -> float:
    """Typical seconds to the first word (median of recent answers, or a starting guess)."""
    with _lock:
        times = sorted(t for ok, t in _stats.get(_key(pid, model), ()) if ok and t)
    if times:
        return times[len(times) // 2]
    return _PROVIDER_SPEED.get(pid, DEFAULT_SPEED)


def success(pid: str, model: str) -> float:
    with _lock:
        d = list(_stats.get(_key(pid, model), ()))
    if not d:
        return 0.9
    return sum(ok for ok, _ in d) / len(d)


def measured(pid: str, model: str) -> bool:
    with _lock:
        return any(t for _, t in _stats.get(_key(pid, model), ()))


def reset() -> None:
    with _lock:
        _stats.clear()


# ---------- Ordering ----------

def order(cands: list[tuple[dict, str]], task: str, cooling, kind: str = "") -> list[tuple[dict, str]]:
    """cands are (provider, model) in your own order. Resting models always go last."""
    if task == "smart":
        def key(item):
            n, (p, m) = item
            return (cooling(p["id"], m), -round(quality(m, kind) * success(p["id"], m)), speed(p["id"], m), n)
    elif task == "fast":
        def key(item):
            n, (p, m) = item
            score = speed(p["id"], m) / max(success(p["id"], m), 0.2) - quality(m, kind) * 0.02
            return (cooling(p["id"], m), score, n)
    else:   # balanced: keep your order, but models measured as much slower than the best go back
        best = min((speed(p["id"], m) for p, m in cands if measured(p["id"], m)), default=None)

        def key(item):
            n, (p, m) = item
            slow = best is not None and measured(p["id"], m) and speed(p["id"], m) > max(3.0, best * 2.5)
            return (cooling(p["id"], m), success(p["id"], m) < 0.4, slow, n)
    return [c for _, c in sorted(enumerate(cands), key=key)]


# ---------- How hard is this message? ----------

_CODE = re.compile(r"```|\b(def|class|function|const|import|SELECT|traceback|stack trace|exception|error:|regex|"
                   r"bug|debug|refactor|compile|python|javascript|typescript|java|rust|golang|sql|api|endpoint|"
                   r"dockerfile|kubernetes|code)\b", re.I)
_MATH = re.compile(r"\b(prove|proof|integral|derivative|equation|solve for|probability|theorem|matrix|"
                   r"calculate|compute)\b|[=^√∑∫]\s*\w|\d+\s*[-+*/^]\s*\d+\s*[-+*/^]\s*\d+", re.I)
_REASON = re.compile(r"\b(step by step|explain why|compare|trade-?offs?|analy[sz]e|analysis|strategy|plan for|"
                     r"design|architecture|pros and cons|evaluate|in depth|detailed|thorough|research|"
                     r"why does|how does .+ work)\b", re.I)
_WRITE = re.compile(r"\b(write|draft|essay|story|poem|email|letter|blog|article|rewrite|summari[sz]e|translate)\b", re.I)
_TRIVIAL = re.compile(r"^\W*(hi|hello|hey|yo|thanks|thank you|thx|ok|okay|cool|nice|great|good (morning|night|"
                      r"evening|afternoon)|bye|lol|yes|no|sure|namaste)\W*$", re.I)
_FRESH = re.compile(r"\b(today|tonight|yesterday|this (week|month|year)|latest|current(ly)?|right now|news|"
                    r"price of|stock price|weather|score|who won|released?|20[2-3]\d)\b", re.I)


def classify(message: str, has_files: bool = False) -> dict:
    """{'level': trivial|normal|hard, 'kind': code|math|reasoning|writing|vision|chat, 'fresh': bool}"""
    text = (message or "").strip()
    words = len(text.split())
    kind = ("vision" if has_files else "code" if _CODE.search(text) else "math" if _MATH.search(text)
            else "reasoning" if _REASON.search(text) else "writing" if _WRITE.search(text) else "chat")
    if _TRIVIAL.match(text) and not has_files:
        level = "trivial"
    elif kind in ("code", "math", "reasoning") and (words >= 12 or "```" in text or kind == "math"):
        level = "hard"
    elif words >= 120:
        level = "hard"
    else:
        level = "normal"
    return {"level": level, "kind": kind, "fresh": bool(_FRESH.search(text))}


def task_for(level: str, speed_mode: str = "balanced") -> str:
    if level == "hard":
        return "smart"
    if level == "trivial" or speed_mode == "fastest":
        return "fast"
    return "balanced"


# ---------- Racing two models for the first word ----------

class EmptyReply(Exception):
    pass


def race(cands: list, run, hedge_s: float | None, on_error, skip=lambda c: False):
    """Yield ('win', cand), then ('text', piece)..., then ('end', None) or ('error', exc) for a failure mid-answer.

    run(cand) returns an iterator of text pieces (raises on failure). on_error(cand, exc) is told about
    every candidate that failed before saying anything. Ends silently if every candidate failed."""
    q: queue.Queue = queue.Queue()
    stops: dict[int, threading.Event] = {}
    pending = list(range(len(cands)))
    live: set[int] = set()
    winner = None

    def worker(i: int, ev: threading.Event):
        it = None
        try:
            it = iter(run(cands[i]))
            for piece in it:
                if ev.is_set():
                    break
                if piece:
                    q.put((i, "text", piece))
            else:
                q.put((i, "end", None))
        except BaseException as exc:      # noqa: BLE001  (reported to the caller, never swallowed)
            q.put((i, "error", exc))
        finally:
            close = getattr(it, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass

    def start_next() -> bool:
        while pending:
            i = pending.pop(0)
            if skip(cands[i]):
                continue
            stops[i] = threading.Event()
            live.add(i)
            ctx = contextvars.copy_context()      # the thread works for the same user (data folder, meters)
            threading.Thread(target=ctx.run, args=(worker, i, stops[i]), daemon=True, name="model-race").start()
            return True
        return False

    try:
        if not start_next():
            return
        while True:
            wait = hedge_s if winner is None and pending and hedge_s else None
            try:
                i, kind, val = q.get(timeout=wait)
            except queue.Empty:
                start_next()                 # the first choice is slow to start: let the next one try too
                continue
            if winner is None:
                if kind == "text":
                    winner = i
                    for j in live - {i}:
                        stops[j].set()
                    yield "win", cands[i]
                    yield "text", val
                    continue
                live.discard(i)
                on_error(cands[i], val if kind == "error" else EmptyReply("empty reply"))
                if not live and not start_next():
                    return
            elif i == winner:
                if kind == "text":
                    yield "text", val
                else:
                    yield kind, val
                    return
    finally:
        for ev in stops.values():
            ev.set()
