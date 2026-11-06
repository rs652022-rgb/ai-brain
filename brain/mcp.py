"""MCP: connect outside tool servers to your Brain (like OpenClaw's MCP support).

An MCP server offers tools (GitHub, Notion, a database, your files...). Add it in Skills → MCP servers:
- HTTP servers: their URL (and a token header if they need one).
- Local command servers (e.g. "npx -y @modelcontextprotocol/server-filesystem C:/Docs"): only on your
  own computer, or for the admin of a server, because they run programs there.

The Brain then sees each server's tools and can call them while answering. Results are information,
never instructions.
"""
import json
import os
import queue
import shlex
import subprocess
import threading

import httpx

from . import config, db, settings

PROTOCOL = "2025-06-18"
_procs: dict = {}
_lock = threading.Lock()


class MCPError(RuntimeError):
    pass


def servers() -> list[dict]:
    return list((settings.load().get("mcp") or {}).get("servers") or [])


def _save(items: list[dict]) -> None:
    data = settings.load()
    data["mcp"] = {"servers": items[:12]}
    settings.save(data)


def public() -> list[dict]:
    out = []
    for s in servers():
        x = dict(s)
        x["headers"] = {k: "••••" for k in (s.get("headers") or {})}
        x["env"] = {k: "••••" for k in (s.get("env") or {})}
        out.append(x)
    return out


def upsert(server: dict) -> dict:
    name = "".join(c for c in str(server.get("name", "")).lower() if c.isalnum() or c in "-_")[:30]
    if not name:
        raise MCPError("Give the server a short name.")
    kind = server.get("kind") if server.get("kind") in ("http", "stdio") else "http"
    items = servers()
    old = next((s for s in items if s["name"] == name), {})
    s = {"name": name, "kind": kind, "enabled": bool(server.get("enabled", True)),
         "url": str(server.get("url", "")).strip(), "command": str(server.get("command", "")).strip(),
         "headers": {k: v for k, v in (server.get("headers") or {}).items() if v and v != "••••"} or old.get("headers", {}),
         "env": {k: v for k, v in (server.get("env") or {}).items() if v and v != "••••"} or old.get("env", {}),
         "tools": old.get("tools", [])}
    if kind == "http":
        if not s["url"].startswith(("http://", "https://")):
            raise MCPError("Enter the server's URL.")
        if not settings.url_allowed(s["url"]):
            raise MCPError("On a shared server, MCP servers must be at public addresses.")
    elif not config.allow_private():
        raise MCPError("Local command servers only work on your own computer (or for the server's admin).")
    elif not s["command"]:
        raise MCPError("Enter the command that starts the server.")
    items = [x for x in items if x["name"] != name] + [s]
    _save(items)
    db.audit("owner", "mcp.save", server=name, kind=kind)
    return s


def remove(name: str) -> None:
    _stop(name)
    _save([s for s in servers() if s["name"] != name])
    db.audit("owner", "mcp.remove", server=name)


# ---------- Talking to a server ----------

class _Stdio:
    def __init__(self, s: dict):
        args = shlex.split(s["command"], posix=os.name != "nt")
        env = os.environ | (s.get("env") or {})
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", env=env, bufsize=1,
                                  creationflags=0x08000000 if os.name == "nt" else 0)
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.n = 0
        self.lock = threading.Lock()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "ai-brain", "version": "1.0"}})
        self.notify("notifications/initialized")

    def _read(self):
        for line in self.p.stdout:
            line = line.strip()
            if line.startswith("{"):
                try:
                    self.q.put(json.loads(line))
                except ValueError:
                    pass

    def notify(self, method):
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.p.stdin.flush()

    def request(self, method, params, timeout=60):
        with self.lock:
            self.n += 1
            mid = self.n
            self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n")
            self.p.stdin.flush()
            while True:
                try:
                    msg = self.q.get(timeout=timeout)
                except queue.Empty:
                    raise MCPError(f"{method} timed out")
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise MCPError(str(msg["error"].get("message", msg["error"]))[:300])
                    return msg.get("result") or {}

    def close(self):
        try:
            self.p.terminate()
        except Exception:
            pass


class _Http:
    def __init__(self, s: dict):
        self.url, self.headers, self.session, self.n = s["url"], dict(s.get("headers") or {}), "", 0
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "ai-brain", "version": "1.0"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, body, timeout=60):
        if not settings.url_allowed(self.url):
            raise MCPError("That MCP address isn't allowed.")
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
             "MCP-Protocol-Version": PROTOCOL} | self.headers
        if self.session:
            h["Mcp-Session-Id"] = self.session
        r = httpx.post(self.url, json=body, headers=h, timeout=timeout)
        if r.headers.get("mcp-session-id"):
            self.session = r.headers["mcp-session-id"]
        if r.status_code >= 400:
            raise MCPError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r

    def request(self, method, params, timeout=60):
        self.n += 1
        r = self._post({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}, timeout)
        msgs = []
        if "text/event-stream" in r.headers.get("content-type", ""):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        msgs.append(json.loads(line[5:].strip()))
                    except ValueError:
                        pass
        elif r.content:
            msgs.append(r.json())
        msg = next((m for m in msgs if m.get("id") == self.n), None)
        if not msg:
            raise MCPError(f"No answer to {method}")
        if "error" in msg:
            raise MCPError(str(msg["error"].get("message", msg["error"]))[:300])
        return msg.get("result") or {}

    def close(self):
        pass


def _client(name: str):
    s = next((x for x in servers() if x["name"] == name and x.get("enabled", True)), None)
    if not s:
        raise MCPError(f"No MCP server called {name}.")
    key = (config.user_key(), name)
    with _lock:
        c = _procs.get(key)
        if c is None or (isinstance(c, _Stdio) and c.p.poll() is not None):
            if s["kind"] == "stdio" and not config.allow_private():
                raise MCPError("Local command servers aren't allowed here.")
            c = _Stdio(s) if s["kind"] == "stdio" else _Http(s)
            _procs[key] = c
    return c


def _stop(name: str) -> None:
    c = _procs.pop((config.user_key(), name), None)
    if c:
        c.close()


def refresh(name: str) -> list[dict]:
    """Connect and read the server's tool list (saved so the Brain knows what it can call)."""
    _stop(name)
    tools = _client(name).request("tools/list", {}).get("tools", [])
    slim = [{"name": t["name"], "description": (t.get("description") or "")[:300],
             "params": list(((t.get("inputSchema") or {}).get("properties") or {}).keys())[:12]} for t in tools][:60]
    items = servers()
    for s in items:
        if s["name"] == name:
            s["tools"] = slim
    _save(items)
    db.audit("owner", "mcp.refresh", server=name, tools=len(slim))
    return slim


def call(server: str, tool: str, arguments: dict) -> str:
    s = next((x for x in servers() if x["name"] == server), None)
    if not s or tool not in {t["name"] for t in s.get("tools", [])}:
        raise MCPError(f"{server} has no tool called {tool}. Use one of the listed MCP tools.")
    res = _client(server).request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout=120)
    parts = []
    for c in res.get("content", []):
        if c.get("type") == "text":
            parts.append(c["text"])
        elif c.get("type") == "resource":
            parts.append(json.dumps(c.get("resource", {}))[:2000])
        else:
            parts.append(f"[{c.get('type')} content]")
    if res.get("structuredContent") and not parts:
        parts.append(json.dumps(res["structuredContent"])[:6000])
    text = "\n".join(parts) or "(no output)"
    db.audit("brain", "mcp.call", server=server, tool=tool)
    return ("The tool reported an error: " if res.get("isError") else "") + text


def prompt_lines() -> str:
    lines = []
    for s in servers():
        if s.get("enabled", True):
            for t in s.get("tools", []):
                lines.append(f"  - {s['name']} / {t['name']}({', '.join(t['params'])}): {t['description'][:160]}")
    return "\n".join(lines[:80])
