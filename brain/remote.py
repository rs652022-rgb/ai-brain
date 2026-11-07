"""Use from my phone: reach the Brain on your own computer from your phone or laptop, privately, over Tailscale.

Tailscale (free, open-source client) links your own devices in a private encrypted network. When you switch
this on, the Brain listens ONLY on this computer's Tailscale address (100.x.y.z), never on your Wi-Fi or the
internet, so only your own Tailscale devices can even reach it. On top of that it asks for a password you
choose. Sessions last 30 days; "Sign out everywhere" ends them all. Login attempts are rate-limited.

For HTTPS (needed for the microphone and installing the app on the phone), run once on this computer:
    tailscale serve --bg 8765
and open https://<this-computer>.<your-tailnet>.ts.net on the phone (Tailscale makes the certificate).
"""
import hashlib
import hmac
import ipaddress
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time

from . import config, db, settings

COOKIE = "brain_remote"
PORT = int(os.environ.get("BRAIN_REMOTE_PORT", "8766"))
SESSION_DAYS = 30
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_attempts: dict[str, list[float]] = {}
_server = {"srv": None, "thread": None, "ip": ""}
_lock = threading.Lock()


class RemoteError(RuntimeError):
    pass


def cfg() -> dict:
    return {"enabled": False, "pw_hash": "", "salt": "", "sessions": []} | (settings.load().get("remote") or {})


def _save(c: dict) -> None:
    data = settings.load()
    data["remote"] = c
    settings.save(data)


def _hash(text: str, salt: str) -> str:
    return hashlib.scrypt(text.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32).hex()


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_tailnet(ip: str) -> bool:
    try:
        a = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return False
    return a in TAILNET_V4 or a in TAILNET_V6


def tailscale_ip() -> str:
    """This computer's Tailscale address, or '' if Tailscale isn't running."""
    try:                                     # asks the OS which address would reach Tailscale's DNS (sends nothing)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("100.100.100.100", 53))
            ip = s.getsockname()[0]
            if is_tailnet(ip):
                return ip
    except OSError:
        pass
    exe = shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if os.name == "nt" else "")
    if exe and os.path.exists(exe):
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
            if out and is_tailnet(out[0]):
                return out[0]
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def tailscale_name() -> str:
    exe = shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if os.name == "nt" else "")
    if not exe or not os.path.exists(exe):
        return ""
    try:
        import json
        st = json.loads(subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5).stdout)
        return str(st.get("Self", {}).get("DNSName", "")).rstrip(".")
    except Exception:
        return ""


def status() -> dict:
    c = cfg()
    ip = tailscale_ip()
    name = tailscale_name()
    return {"available": not config.MULTIUSER, "enabled": c["enabled"], "has_password": bool(c["pw_hash"]),
            "listening": bool(_server["srv"]), "tailscale_ip": ip, "tailscale_name": name,
            "address": f"http://{ip}:{PORT}" if ip else "", "https_address": f"https://{name}" if name else "",
            "sessions": len(c["sessions"])}


def set_password(password: str) -> None:
    if len(password or "") < 10:
        raise RemoteError("Use a password of at least 10 characters.")
    c = cfg()
    c["salt"] = secrets.token_hex(16)
    c["pw_hash"] = _hash(password, c["salt"])
    c["sessions"] = []                         # a new password signs everyone out
    _save(c)
    db.audit("owner", "remote.password")


def set_enabled(on: bool) -> dict:
    if config.MULTIUSER:
        raise RemoteError("On a server, people already sign in from anywhere.")
    c = cfg()
    if on and not c["pw_hash"]:
        raise RemoteError("Set a password first.")
    c["enabled"] = bool(on)
    if not on:
        c["sessions"] = []
    _save(c)
    db.audit("owner", "remote.enable" if on else "remote.disable")
    start() if on else stop()
    return status()


def login(password: str, ip: str) -> str:
    now = time.time()
    recent = [t for t in _attempts.get(ip, []) if now - t < 600]
    if len(recent) >= 8:
        raise RemoteError("Too many tries. Wait 10 minutes.")
    _attempts[ip] = recent + [now]
    c = cfg()
    if not (c["enabled"] and c["pw_hash"]) or not hmac.compare_digest(_hash(password or "", c["salt"]), c["pw_hash"]):
        db.audit("system", "remote.login_failed", ip=ip)
        time.sleep(0.5)
        raise RemoteError("Wrong password.")
    _attempts.pop(ip, None)
    token = secrets.token_urlsafe(32)
    c["sessions"] = ([s for s in c["sessions"] if s["exp"] > now] + [{"h": _tok_hash(token), "exp": now + SESSION_DAYS * 86400}])[-10:]
    _save(c)
    db.audit("owner", "remote.login", ip=ip)
    return token


def session_ok(token: str | None) -> bool:
    if not token:
        return False
    c = cfg()
    if not c["enabled"]:
        return False
    h, now = _tok_hash(token), time.time()
    return any(hmac.compare_digest(s["h"], h) and s["exp"] > now for s in c["sessions"])


def logout(token: str | None) -> None:
    c = cfg()
    h = _tok_hash(token or "")
    c["sessions"] = [s for s in c["sessions"] if s["h"] != h]
    _save(c)


def logout_all() -> None:
    c = cfg()
    c["sessions"] = []
    _save(c)
    db.audit("owner", "remote.logout_all")


# ---------- The extra listener (only on the Tailscale address) ----------

def start() -> str:
    """Listen on this computer's Tailscale address. Returns the address, or '' if Tailscale isn't running."""
    if config.MULTIUSER or os.environ.get("BRAIN_NO_REMOTE") == "1":
        return ""
    with _lock:
        if _server["srv"]:
            return _server["ip"]
        ip = tailscale_ip()
        if not ip:
            return ""
        import uvicorn
        from .server import app
        srv = uvicorn.Server(uvicorn.Config(app, host=ip, port=PORT, log_level="warning", lifespan="off"))
        t = threading.Thread(target=srv.run, daemon=True, name="brain-remote")
        t.start()
        _server.update(srv=srv, thread=t, ip=ip)
    db.audit("system", "remote.listening", ip=ip, port=PORT)
    return ip


def stop() -> None:
    with _lock:
        srv = _server["srv"]
        _server.update(srv=None, thread=None, ip="")
    if srv:
        srv.should_exit = True


def watch() -> None:
    """Started with the Brain: keeps the listener up when Tailscale connects later (e.g. after sleep)."""
    def loop():
        while True:
            try:
                if cfg()["enabled"] and not _server["srv"]:
                    start()
                elif _server["srv"] and _server["ip"] != tailscale_ip():
                    stop()                        # the Tailscale address changed or went away
            except Exception:
                pass
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name="brain-remote-watch").start()
