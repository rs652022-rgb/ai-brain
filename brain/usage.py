"""Usage tracking: how many model requests each provider served (or failed) per day, and Prometheus metrics."""
import time
from datetime import date

from . import db


def record(provider: str, model: str, ok: bool, seconds: float = 0.0) -> None:
    try:
        with db.session() as conn:
            conn.execute("INSERT INTO usage(day, provider, model, ok, failed, seconds) VALUES(?,?,?,?,?,?) "
                         "ON CONFLICT(day, provider, model) DO UPDATE SET ok=ok+excluded.ok, failed=failed+excluded.failed, "
                         "seconds=seconds+excluded.seconds",
                         (date.today().isoformat(), provider, model[:80], int(ok), int(not ok), round(seconds, 3)))
    except Exception:
        pass                                   # never let bookkeeping break an answer


def summary(days: int = 14) -> list[dict]:
    with db.session() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT day, provider, model, ok, failed, ROUND(seconds / MAX(ok, 1), 2) AS avg_seconds FROM usage "
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
