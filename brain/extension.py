"""The Brain's Chrome extension: it lets the Brain use the tabs you're already signed in to.

You add the extension to Chrome (or Edge/Brave), paste your Brain's address and an API token, and pick
the sites it may use (each site is a separate Chrome permission you approve). The extension keeps a live
link to your Brain and does only what's asked on those sites: list tabs, open a page, read it, click,
type, scroll and take a screenshot. Passwords come only from the secrets vault, on the vault's sites,
and the extension checks the page again right before typing one.
"""
import asyncio
import threading
import uuid

from . import devices, vault
from .hub import Conn

_conns: dict[str, Conn] = {}
_lock = threading.Lock()


class ExtensionError(RuntimeError):
    pass


def register(key: str, conn: Conn) -> None:
    with _lock:
        _conns[key] = conn


def unregister(key: str, conn: Conn) -> None:
    with _lock:
        if _conns.get(key) is conn:
            _conns.pop(key, None)
    for slot in conn.pending.values():
        slot[1] = {"ok": False, "error": "Your browser disconnected."}
        slot[0].set()


def deliver(conn: Conn, msg: dict) -> None:
    if msg.get("type") == "status":
        conn.info.update({k: msg[k] for k in ("sites", "browser", "tabs") if k in msg})
        return
    slot = conn.pending.get(str(msg.get("id")))
    if slot:
        slot[1] = msg
        slot[0].set()


def status(key: str) -> dict:
    c = _conns.get(key)
    return {"connected": bool(c), "browser": (c.info.get("browser") if c else "") or "",
            "sites": (c.info.get("sites") if c else []) or []}


def call(key: str, payload: dict, timeout: float = 45) -> dict:
    conn = _conns.get(key)
    if not conn:
        raise ExtensionError("Your browser isn't connected. Add the Brain extension to Chrome (Settings → Chrome extension).")
    mid = uuid.uuid4().hex
    slot = [threading.Event(), None]
    conn.pending[mid] = slot
    try:
        asyncio.run_coroutine_threadsafe(conn.send({"id": mid, **payload}), conn.loop).result(10)
        if not slot[0].wait(timeout):
            raise ExtensionError("The browser didn't answer in time.")
    finally:
        conn.pending.pop(mid, None)
    res = slot[1] or {}
    if not res.get("ok"):
        raise ExtensionError(res.get("error") or "The extension couldn't do that.")
    return res.get("result") or {}


OPS = ("tabs", "open", "read", "click", "type", "press", "scroll", "screenshot", "close")


def act(key: str, args: dict) -> dict:
    op = str(args.get("op") or "tabs")
    if op not in OPS:
        raise ExtensionError(f"op is one of: {', '.join(OPS)}")
    payload = {"op": op}
    for k in ("tab", "url", "target", "key", "direction"):
        if args.get(k) not in (None, ""):
            payload[k] = args[k]
    used: list[str] = []
    if op == "type":
        text = str(args.get("text", ""))
        devices.check_text(text)                  # never card, bank or ID numbers
        if vault.has_placeholder(text):
            tabs = call(key, {"op": "tabs"}).get("tabs", [])
            tab = next((t for t in tabs if str(t.get("id")) == str(args.get("tab"))), None)
            if not tab:
                raise ExtensionError("Say which tab (from op tabs) to type the secret into.")
            text, used = vault.resolve(text, tab.get("url", ""), where="extension")
            data = vault._load()
            sites = sorted({s for m in vault.PLACEHOLDER.finditer(str(args.get("text", ""))) for s in data[m.group(1)]["sites"]})
            payload["lock_sites"] = sites            # the extension refuses if the tab moved elsewhere
            payload["secret"] = True
        payload["text"] = text
    out = call(key, payload)
    if used:
        for k in ("text", "elements", "title"):
            if isinstance(out.get(k), str):
                out[k] = vault.scrub(out[k], used)
    return out
