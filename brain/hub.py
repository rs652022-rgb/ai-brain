"""Links each user's Brain on the server to the Brain Companion app on their own computer.

The companion opens a WebSocket to the server (outgoing, so it works behind home routers), proves
who it is with its device token, and then carries out device actions the Brain asks for, on that
user's computer only. The server never runs device actions itself.
"""
import asyncio
import threading
import uuid

from . import devices


class Conn:
    def __init__(self, ws, loop, user: dict, device: dict, info: dict):
        self.ws, self.loop, self.user, self.device, self.info = ws, loop, user, device, info
        self.pending: dict[str, list] = {}

    async def send(self, msg: dict) -> None:
        await self.ws.send_json(msg)


_conns: dict[str, Conn] = {}          # user key -> the user's connected companion (latest wins)
_lock = threading.Lock()


def register(key: str, conn: Conn) -> None:
    with _lock:
        old = _conns.get(key)
        _conns[key] = conn
    if old and old is not conn:
        for ev in old.pending.values():
            ev[1] = {"ok": False, "error": "The companion reconnected."}
            ev[0].set()


def unregister(key: str, conn: Conn) -> None:
    with _lock:
        if _conns.get(key) is conn:
            _conns.pop(key, None)
    for ev in conn.pending.values():
        ev[1] = {"ok": False, "error": "Your computer disconnected."}
        ev[0].set()


def deliver(conn: Conn, msg: dict) -> None:
    """A reply from the companion."""
    if msg.get("type") == "status":
        conn.info.update({k: v for k, v in msg.items() if k != "type"})
        return
    slot = conn.pending.get(str(msg.get("id")))
    if slot:
        slot[1] = msg
        slot[0].set()


def connected(key: str) -> Conn | None:
    return _conns.get(key)


def drop_device(key: str, device_id: int) -> None:
    conn = _conns.get(key)
    if conn and conn.device["id"] == device_id:
        asyncio.run_coroutine_threadsafe(conn.ws.close(code=4001), conn.loop)


def call(key: str, payload: dict, timeout: float = 90) -> dict:
    """Ask the user's companion to do something and wait for the answer (called from worker threads)."""
    conn = _conns.get(key)
    if not conn:
        raise devices.DeviceError("Your computer isn't connected. Open the Brain Companion app on it "
                                  "(Control tab → Your computers).")
    mid = uuid.uuid4().hex
    slot = [threading.Event(), None]
    conn.pending[mid] = slot
    try:
        asyncio.run_coroutine_threadsafe(conn.send({"id": mid, **payload}), conn.loop).result(15)
        if not slot[0].wait(timeout):
            raise devices.DeviceError("Your computer didn't answer in time.")
    finally:
        conn.pending.pop(mid, None)
    res = slot[1] or {}
    if not res.get("ok"):
        err = res.get("error") or "The companion couldn't do that."
        if res.get("kind") == "permission":
            raise PermissionError(err)
        raise devices.DeviceError(err)
    return res.get("result") or {}
