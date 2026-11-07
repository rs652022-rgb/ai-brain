"""Usage tracking: how many model requests each provider served (or failed) per day, and Prometheus metrics."""
import time
from datetime import date

from . import db


import contextvars

# A goal (or anything else) can count the requests and tokens spent inside it, and set a ceiling.
meter: contextvars.ContextVar = contextvars.ContextVar("brain_meter", default=None)


class OverBudget(RuntimeError):
    pass


def estimate(*texts) -> int:
    """About 4 characters per token: good enough for budgets on free models."""
    return sum(len(t) if isinstance(t, str) else len(str(t)) for t in texts) // 4


def record(provider: str, model: str, ok: bool, seconds: float = 0.0, tokens: int = 0) -> None:
    m = meter.get()
    if m is not None:
        m["requests"] = m.get("requests", 0) + 1
        m["tokens"] = m.get("tokens", 0) + tokens
    try:
        with db.session() as conn:
            conn.execute("INSERT INTO usage(day, provider, model, ok, failed, seconds, tokens) VALUES(?,?,?,?,?,?,?) "
                         "ON CONFLICT(day, provider, model) DO UPDATE SET ok=ok+excluded.ok, failed=failed+excluded.failed, "
                         "seconds=seconds+excluded.seconds, tokens=tokens+excluded.tokens",
                         (date.today().isoformat(), provider, model[:80], int(ok), int(not ok), round(seconds, 3), tokens))
    except Exception:
        pass                                   # never let bookkeeping break an answer


def today() -> dict:
    with db.session() as conn:
        r = conn.execute("SELECT COALESCE(SUM(ok + failed), 0) AS requests, COALESCE(SUM(tokens), 0) AS tokens FROM usage "
                         "WHERE day=?", (date.today().isoformat(),)).fetchone()
    return {"requests": r["requests"], "tokens": r["tokens"]}


def check_limits() -> None:
    """Stop before a model call when a daily limit or the current goal's budget is used up."""
    from . import settings
    lim = settings.load().get("limits") or {}
    if lim.get("daily_requests") or lim.get("daily_tokens"):
        t = today()
        if lim.get("daily_requests") and t["requests"] >= int(lim["daily_requests"]):
            raise OverBudget(f"Today's limit of {lim['daily_requests']} model requests is used up (change it in Master → Limits).")
        if lim.get("daily_tokens") and t["tokens"] >= int(lim["daily_tokens"]):
            raise OverBudget(f"Today's limit of {lim['daily_tokens']} tokens is used up (change it in Master → Limits).")
    m = meter.get()
    if m is not None:
        if m.get("max_requests") and m.get("requests", 0) >= m["max_requests"]:
            raise OverBudget("This goal's request budget is used up.")
        if m.get("max_tokens") and m.get("tokens", 0) >= m["max_tokens"]:
            raise OverBudget("This goal's token budget is used up.")


def summary(days: int = 14) -> list[dict]:
    with db.session() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT day, provider, model, ok, failed, tokens, ROUND(seconds / MAX(ok, 1), 2) AS avg_seconds FROM usage "
            "WHERE day >= date('now', ?) ORDER BY day DESC, ok DESC", (f"-{days} days",)).fetchall()]


def prometheus(rows_by_user: dict[str, list[dict]], extra: dict) -> str:
    lines = ["# HELP brain_model_requests_total Model requests by provider, model and result (today).",
             "# TYPE brain_model_requests_total counter"]
    today = date.today().isoformat()
    for user, rows in rows_by_user.items():
        for r in rows:
            if r["day"] != today:
                continue
            labels = f'user="{user}",provider="{r["provider"]}",model="{r["model"]}"'
            lines.append(f'brain_model_requests_total{{{labels},result="ok"}} {r["ok"]}')
            lines.append(f'brain_model_requests_total{{{labels},result="failed"}} {r["failed"]}')
    for k, v in extra.items():
        lines += [f"# TYPE brain_{k} gauge", f"brain_{k} {v}"]
    lines.append(f"brain_scrape_timestamp_seconds {int(time.time())}")
    return "\n".join(lines) + "\n"
