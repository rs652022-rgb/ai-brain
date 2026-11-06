"""Push notifications to your phone and computer (free Web Push; works with the installed app on Android,
iPhone/iPad with iOS 16.4+, Windows, Mac and Linux), and the device "node" link.

Notifications: scheduled jobs, heartbeat alerts, workflow approvals and messages the Brain sends on its
own arrive even when the app is closed.

Device node (like OpenClaw's mobile nodes): while the Brain app is open on a phone or laptop, your Brain
can ask that device for its location, a camera photo or to show a notification. The device always asks
you first, every time.
"""
import asyncio
import base64
import json
import threading
import uuid

from . import accounts, config, db

_vapid: dict = {}


def _keys() -> dict:
    """The server's VAPID keys (made once, kept in data/vapid.json)."""
    if _vapid:
        return _vapid
    path = config.ROOT_DATA / "vapid.json"
    if path.exists():
        _vapid.update(json.loads(path.read_text()))
        return _vapid
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    _vapid.update(private_pem=priv, public=base64.urlsafe_b64encode(pub).rstrip(b"=").decode())
    config.ROOT_DATA.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_vapid))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return _vapid


def public_key() -> str:
    return _keys()["public"]


def _uid() -> int:
    """Account id of the current user (0 = the laptop owner)."""
    if not config.MULTIUSER:
        return 0
    d = config.data_dir()
    return int(d.name) if d.parent.name == "users" and d.name.isdigit() else -1


def subscribe(sub: dict) -> None:
    endpoint = str(sub.get("endpoint", ""))
    if not endpoint.startswith("https://") or not (sub.get("keys") or {}).get("p256dh"):
        raise ValueError("Not a push subscription.")
    with accounts._conn() as c:
        c.execute("INSERT INTO push_subs(user_id, endpoint, sub, created) VALUES(?,?,?,?) "
                  "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, sub=excluded.sub",
                  (_uid(), endpoint, json.dumps(sub), db.now()))


def unsubscribe(endpoint: str) -> None:
    with accounts._conn() as c:
        c.execute("DELETE FROM push_subs WHERE endpoint=? AND user_id=?", (endpoint, _uid()))


def count() -> int:
    with accounts._conn() as c:
        return c.execute("SELECT COUNT(*) FROM push_subs WHERE user_id=?", (_uid(),)).fetchone()[0]


def notify(title: str, body: str, url: str = "/") -> int:
    """Send a notification to every device this user turned notifications on for. Returns how many got it."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return 0
    with accounts._conn() as c:
        rows = c.execute("SELECT id, endpoint, sub FROM push_subs WHERE user_id=?", (_uid(),)).fetchall()
    if not rows:
        return 0
    keys = _keys()
    payload = json.dumps({"title": title[:80], "body": body[:240], "url": url})
    sent = 0
    for r in rows:
        try:
            webpush(json.loads(r["sub"]), payload, vapid_private_key=keys["private_pem"],
                    vapid_claims={"sub": "mailto:brain@localhost"}, ttl=3600, timeout=10)
            sent += 1
        except WebPushException as exc:
            if getattr(exc, "response", None) is not None and exc.response.status_code in (404, 410):
                with accounts._conn() as c:      # the device unsubscribed: forget it
                    c.execute("DELETE FROM push_subs WHERE id=?", (r["id"],))
        except Exception:
            pass
    return sent


def notify_async(title: str, body: str, url: str = "/") -> None:
    config.spawn(notify, title, body, url)


# ---------- Device node: the open app on a phone or laptop ----------

class Node:
    def __init__(self, ws, loop, name: str, caps: list[str]):
        self.id, self.ws, self.loop, self.name, self.caps = uuid.uuid4().hex[:8], ws, loop, name, caps
        self.pending: dict[str, list] = {}


_nodes: dict[str, list[Node]] = {}
_lock = threading.Lock()


def add_node(key: str, node: Node) -> None:
    with _lock:
        _nodes.setdefault(key, []).append(node)


def remove_node(key: str, node: Node) -> None:
    with _lock:
        _nodes[key] = [n for n in _nodes.get(key, []) if n is not node]
    for slot in node.pending.values():
        slot[1] = {"ok": False, "error": "The device closed the app."}
        slot[0].set()


def nodes() -> list[dict]:
    return [{"id": n.id, "name": n.name, "caps": n.caps} for n in _nodes.get(config.user_key(), [])]


def deliver(node: Node, msg: dict) -> None:
    slot = node.pending.get(str(msg.get("id")))
    if slot:
        slot[1] = msg
        slot[0].set()


def ask(action: str, device: str = "", text: str = "", timeout: float = 90) -> dict:
    """Ask an open Brain app on one of your devices to do something (it asks you first)."""
    items = _nodes.get(config.user_key(), [])
    items = [n for n in items if action in n.caps and (not device or device.lower() in n.name.lower())]
    if not items:
        raise RuntimeError("No device with the Brain app open can do that right now. Open the app on your phone "
                           "and switch on 'Let my Brain use this device' in Master → This device.")
    node = items[-1]
    mid = uuid.uuid4().hex
    slot = [threading.Event(), None]
    node.pending[mid] = slot
    try:
        asyncio.run_coroutine_threadsafe(node.ws.send_json({"id": mid, "action": action, "text": text[:300]}), node.loop).result(10)
        if not slot[0].wait(timeout):
            raise RuntimeError("The device didn't answer (you may not have tapped Allow).")
    finally:
        node.pending.pop(mid, None)
    res = slot[1] or {}
    if not res.get("ok"):
        raise PermissionError(res.get("error") or "Not allowed on the device.")
    db.audit("brain", "node.ask", what=action, device=node.name)
    return res.get("result") or {}
