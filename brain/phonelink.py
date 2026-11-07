"""The Brain app on your Android phone, as a device the Brain can control (no cable, no ADB, no computer).

The app keeps a live link to your Brain (over Tailscale or your server's https address) and uses Android's
Accessibility service to read the screen and to tap, swipe, type, go Back/Home and open apps. You switch
that on yourself in Android Settings, and you can switch it off there any time. The same safety rules as
every device apply: calls, messages and deleting wait for your approval; passwords, OTPs and card numbers
are never typed.

When the app is connected, phone actions go to it; otherwise they use a phone plugged into your computer (ADB).
"""
import asyncio
import threading
import uuid

from . import devices
from .hub import Conn

_conns: dict[str, Conn] = {}
_lock = threading.Lock()


def register(key: str, conn: Conn) -> None:
    with _lock:
        old = _conns.get(key)
        _conns[key] = conn
    if old and old is not conn:
        for slot in old.pending.values():
            slot[1] = {"ok": False, "error": "The phone reconnected."}
            slot[0].set()


def unregister(key: str, conn: Conn) -> None:
    with _lock:
        if _conns.get(key) is conn:
            _conns.pop(key, None)
    for slot in conn.pending.values():
        slot[1] = {"ok": False, "error": "Your phone disconnected."}
        slot[0].set()


def deliver(conn: Conn, msg: dict) -> None:
    if msg.get("type") == "status":
        conn.info.update({k: msg[k] for k in ("accessibility", "listening", "battery", "model", "offline_model") if k in msg})
        return
    slot = conn.pending.get(str(msg.get("id")))
    if slot:
        slot[1] = msg
        slot[0].set()


def connected(key: str) -> Conn | None:
    return _conns.get(key)


def info(key: str) -> dict:
    c = _conns.get(key)
    return {"connected": bool(c), "name": c.device.get("name", "") if c else "",
            **({k: c.info.get(k) for k in ("accessibility", "listening", "battery", "model")} if c else {})}


def call(key: str, payload: dict, timeout: float = 40) -> dict:
    conn = _conns.get(key)
    if not conn:
        raise devices.DeviceError("Your phone app isn't connected. Open the Brain app on the phone.")
    if not conn.info.get("accessibility") and payload.get("op") not in ("status", "speak", "notify"):
        raise devices.DeviceError("Phone control is off. On the phone: Settings → Accessibility → Brain → On.")
    mid = uuid.uuid4().hex
    slot = [threading.Event(), None]
    conn.pending[mid] = slot
    try:
        asyncio.run_coroutine_threadsafe(conn.send({"id": mid, **payload}), conn.loop).result(10)
        if not slot[0].wait(timeout):
            raise devices.DeviceError("The phone didn't answer in time (is the screen on?).")
    finally:
        conn.pending.pop(mid, None)
    res = slot[1] or {}
    if not res.get("ok"):
        raise devices.DeviceError(res.get("error") or "The phone couldn't do that.")
    return res.get("result") or {}


class AppPhone:
    """Same actions as the ADB phone driver, carried out by the Android app's Accessibility service."""

    def __init__(self, key: str):
        self.owner = key
        self.elements: list[dict] = []
        self.scale = 1.0

    def _do(self, op: str, **args) -> dict:
        return call(self.owner, {"op": op, **args})

    def look(self) -> dict:
        out = self._do("screen")
        self.elements = out.get("elements") or []
        shot = None
        if out.get("shot"):
            shot = {"data": out["shot"], "mime": "image/jpeg", "scale": float(out.get("scale") or 1.0)}
            self.scale = shot["scale"]
        text = f"Phone app in front: {out.get('app', '?')}\nPhone screen elements:\n" + devices.format_elements(self.elements, self.scale)
        return {"text": text, "shot": shot} if shot else {"text": text}

    def _xy(self, x, y):
        return round(float(x) / (self.scale or 1)), round(float(y) / (self.scale or 1))

    def tap(self, x, y) -> str:
        rx, ry = self._xy(x, y)
        self._do("tap", x=rx, y=ry)
        return f"Tapped ({x}, {y})."

    def tap_element(self, number) -> str:
        try:
            e = self.elements[int(number) - 1]
        except (ValueError, IndexError, TypeError):
            raise devices.DeviceError("No element with that number. Look at the phone screen again first.")
        self._do("tap", x=e["x"], y=e["y"], node=e.get("id"))
        return f"Tapped [{number}] {e['label']}."

    def long_press(self, x, y) -> str:
        rx, ry = self._xy(x, y)
        self._do("long_press", x=rx, y=ry)
        return f"Long-pressed ({x}, {y})."

    def swipe(self, x1, y1, x2, y2, ms=350) -> str:
        a, b = self._xy(x1, y1)
        c, d = self._xy(x2, y2)
        self._do("swipe", x1=a, y1=b, x2=c, y2=d, ms=int(ms))
        return "Swiped."

    def scroll(self, direction: str) -> str:
        self._do("scroll", direction=direction)
        return f"Scrolled {direction}."

    def type_text(self, text: str, enter: bool = False) -> str:
        devices.check_text(text)
        self._do("type", text=text, enter=bool(enter))
        return f"Typed {len(text)} characters."

    def key(self, name: str) -> str:
        name = name.lower()
        if name not in ("back", "home", "recents", "notifications", "quick_settings", "enter", "lock", "screenshot"):
            raise devices.DeviceError("Keys: back, home, recents, notifications, quick_settings, enter, lock.")
        self._do("key", name=name)
        return f"Pressed {name}."

    def open_app(self, name: str) -> str:
        out = self._do("open_app", name=name)
        return f"Opened {out.get('label') or name}."

    def open_url(self, url: str) -> str:
        if not str(url).startswith(("http://", "https://")):
            raise devices.DeviceError("Only web addresses (http/https) can be opened.")
        self._do("open_url", url=url)
        return f"Opened {url}."

    def call(self, number: str) -> str:
        self._do("call", number=devices.Phone._number(number))
        return f"Calling {number}."

    def sms(self, number: str, text: str) -> str:
        devices.check_text(text)
        self._do("sms", number=devices.Phone._number(number), text=text)
        return f"Message ready for {number}."

    def whatsapp(self, number: str, text: str) -> str:
        devices.check_text(text)
        self._do("whatsapp", number=devices.Phone._number(number), text=text)
        return f"WhatsApp message ready for {number}."
