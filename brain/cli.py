"""Brain from the terminal (like OpenClaw's CLI and TUI).

    python -m brain.cli                    chat in the terminal (streams replies; /help works too)
    python -m brain.cli ask "question"     one answer, then exit (good for scripts)
    python -m brain.cli status             models, memory, channels, jobs
    python -m brain.cli jobs               scheduled jobs
    python -m brain.cli run <job id>       run a job now
    python -m brain.cli approve <code>     approve a channel pairing code
    python -m brain.cli pending            pairing requests waiting for you

Settings: BRAIN_URL (default http://127.0.0.1:8765) and, for the web version, BRAIN_TOKEN (an API token
from Master → API tokens). Options --url and --token do the same.
"""
import argparse
import json
import os
import sys

import httpx

BOLD, DIM, BLUE, YELLOW, RESET = "\033[1m", "\033[2m", "\033[34m", "\033[33m", "\033[0m"
if os.name == "nt":
    os.system("")                       # turn on colours in the Windows terminal


class Client:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.h = {"Authorization": f"Bearer {token}"} if token else {}

    def get(self, path):
        r = httpx.get(self.url + path, headers=self.h, timeout=60)
        self._check(r)
        return r.json()

    def post(self, path, body=None, timeout=300):
        r = httpx.post(self.url + path, headers=self.h, json=body or {}, timeout=timeout)
        self._check(r)
        return r.json()

    @staticmethod
    def _check(r):
        if r.status_code == 401:
            sys.exit("Not allowed. On the web version, set BRAIN_TOKEN to an API token (Master → API tokens).")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            sys.exit(f"Error: {detail}")

    def stream(self, message):
        with httpx.stream("POST", self.url + "/api/chat/stream", headers=self.h, timeout=300,
                          json={"message": message, "mode": "mirror"}) as r:
            if r.status_code >= 400:
                r.read()
                self._check(r)
            for line in r.iter_lines():
                if line.strip():
                    yield json.loads(line)


def chat(c: Client) -> None:
    try:
        st = c.get("/api/master/public")
        name = st.get("brain_name", "Brain")
    except SystemExit:
        raise
    except Exception:
        name = "Brain"
    print(f"{BOLD}{name}{RESET} {DIM}· type /help for commands, Ctrl+C to quit{RESET}\n")
    while True:
        try:
            msg = input(f"{YELLOW}you ›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not msg:
            continue
        if msg in ("exit", "quit", "/quit"):
            return
        print(f"{BLUE}{name.lower()} ›{RESET} ", end="", flush=True)
        status_shown = False
        for ev in c.stream(msg):
            if ev["type"] == "text":
                print(ev["text"], end="", flush=True)
            elif ev["type"] == "status" and not status_shown:
                print(f"{DIM}({ev['text']}){RESET} ", end="", flush=True)
                status_shown = True
            elif ev["type"] == "error":
                print(f"\n{ev['error'].split(chr(10) * 2 + 'Details:')[0]}", end="")
            elif ev["type"] == "final":
                r = ev["result"]
                for m in r.get("media") or []:
                    print(f"\n{DIM}[{m['type']}: {c.url}{m['url']}]{RESET}", end="")
                for s in (r.get("web_sources") or [])[:4]:
                    print(f"\n{DIM}  ↳ {s['title']} {s['url']}{RESET}", end="")
        print("\n")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="brain", description="Your Brain from the terminal")
    ap.add_argument("--url", default=os.environ.get("BRAIN_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--token", default=os.environ.get("BRAIN_TOKEN", ""))
    ap.add_argument("command", nargs="?", default="chat")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    c = Client(a.url, a.token)
    cmd = a.command
    if cmd == "chat":
        chat(c)
    elif cmd == "ask":
        text = "".join(ev.get("text", "") for ev in c.stream(" ".join(a.args)) if ev["type"] == "text")
        print(text)
    elif cmd == "status":
        print(c.post("/api/chat", {"message": "/status"})["reply"])
    elif cmd == "jobs":
        for j in c.get("/api/automations")["jobs"]:
            print(f"{j['id']:>3}  {'on ' if j['enabled'] else 'off'}  {j['kind']:<9} {j['schedule'] or '-':<18} {j['name']}")
    elif cmd == "run" and a.args:
        print(c.post(f"/api/automations/{a.args[0]}/run").get("output", ""))
    elif cmd == "pending":
        for s in c.get("/api/channels")["senders"]:
            if s["status"] == "pending":
                print(f"{s['code']}  {s['channel']:<9} {s['name'] or s['sender']}")
    elif cmd == "approve" and a.args:
        c.post("/api/channels/approve", {"code": a.args[0], "allow": True})
        print("Approved.")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
