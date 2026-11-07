"""Brain Companion: lets your Brain on the web use THIS computer (and an Android phone plugged into it).

Run it, enter your Brain's web address and the pairing code from Control → Your computers, and
leave the window open. Close the window (or press Ctrl+C) to stop all control at once.
Push the mouse hard into a screen corner to stop a running action.

It connects out to your Brain (no ports to open), carries out only the steps your Brain sends after
your permissions allowed them, and shows every step here. Safety guards also run on this computer:
dangerous commands are blocked, card numbers are never typed, files stay inside your allowed folders.
"""
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
for cand in (HERE, HERE.parent):
    if (cand / "brain" / "devices.py").exists():
        sys.path.insert(0, str(cand))
        break

APP_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "BrainCompanion"
APP_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("BRAIN_DATA_DIR", str(APP_DIR / "data"))   # ADB download and phone settings live here
CONFIG = APP_DIR / "companion.json"
VERSION = "1.0"

from brain import control, devices  # noqa: E402  (after the path setup above)

# Shared with the menu-bar / tray app (brain_tray.py)
STATE = {"connected": False, "paused": False, "text": "Starting…", "user": "", "last": ""}


def load_cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cfg(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass


def say(msg: str) -> None:
    STATE["last"] = msg
    try:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    except (OSError, ValueError, AttributeError):      # windowed app: no console
        pass


def pair(cfg: dict) -> dict:
    import httpx
    print("\n  Link this computer to your Brain")
    print("  1. Open your Brain in the browser → Control → Your computers → Make a pairing code")
    server = input("  2. Your Brain's web address (e.g. https://mybrain.example.com): ").strip().rstrip("/")
    if not server.startswith(("http://", "https://")):
        server = "https://" + server
    code = input("  3. Pairing code: ").strip()
    name = input(f"  4. Name for this computer [{platform.node() or 'My computer'}]: ").strip() or platform.node() or "My computer"
    r = httpx.post(f"{server}/api/companion/pair", json={"code": code, "name": name}, timeout=30)
    if r.status_code != 200:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise SystemExit(f"\n  Couldn't link: {detail}\n")
    data = r.json()
    print(f"\n  Linked to {data['user']['name']} ({data['user']['email']}).")
    ok = input("  Allow this Brain to control this computer, within the permissions set in Control? [y/N]: ").strip().lower()
    if ok not in ("y", "yes", "haan", "ha"):
        raise SystemExit("\n  Not allowed. Nothing was changed.\n")
    cfg = {"server": server, "token": data["token"], "name": name, "user": data["user"]}
    save_cfg(cfg)
    return cfg


def status() -> dict:
    s = devices.status_local()
    return {"system": s["system"], "pc_installed": s["pc_installed"], "adb_installed": s["adb_installed"],
            "phones": s["phones"], "phone_error": s["phone_error"], "version": VERSION}


def handle(msg: dict) -> dict:
    op = msg.get("op")
    folders = msg.get("folders")
    control.FOLDERS_OVERRIDE = [str(f) for f in folders] if isinstance(folders, list) else None
    try:
        if op == "run":
            action, args = msg.get("action"), msg.get("args") or {}
            shown = {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v) for k, v in args.items()}
            say(f"▶ {action} {shown if shown else ''}")
            result = devices.run_local(action, args)
            say(f"  {result.get('text', 'done')[:160]}")
        elif op == "look":
            say(f"👁 looking at the {'phone' if msg.get('device') == 'phone' else 'screen'}")
            result = devices.look_local(msg.get("device", "pc"))
        elif op == "status":
            result = status()
        elif op == "phone_connect":
            ph = devices.driver("phone")
            if not ph.installed():
                say("Installing ADB (Google's Android tools)…")
                ph.install_adb()
            out = ph.pair(msg["address"], msg["code"]) if msg.get("code") else ph.connect(msg["address"])
            result = {"message": out, "phones": devices.status_local()["phones"]}
        elif op == "install_adb":
            result = devices.driver("phone").install_adb()
        else:
            raise devices.DeviceError(f"Unknown request {op}")
        return {"id": msg.get("id"), "ok": True, "result": result}
    except PermissionError as exc:
        say(f"  ✋ blocked: {exc}")
        return {"id": msg.get("id"), "ok": False, "kind": "permission", "error": str(exc)}
    except Exception as exc:
        say(f"  ✗ {exc}")
        return {"id": msg.get("id"), "ok": False, "error": str(exc)[:500]}


def serve(cfg: dict) -> None:
    from websockets.sync.client import connect
    ws_url = cfg["server"].replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/companion/ws"
    delay = 2
    while True:
        try:
            with connect(ws_url, open_timeout=20, max_size=40 * 1024 * 1024, ping_interval=25) as ws:
                ws.send(json.dumps({"type": "hello", "token": cfg["token"], **status()}))
                lock = threading.Lock()
                for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "welcome":
                        delay = 2
                        STATE.update(connected=True, user=msg["user"], text=f"Connected to {msg['user']}'s Brain")
                        say(f"✓ Connected to {msg['user']}'s Brain as \"{msg['device']}\". Leave this window open.")
                        say("  Close this window or press Ctrl+C to stop all control.")
                        continue
                    if msg.get("type") == "error":
                        say(f"✗ {msg.get('error')}")
                        if "Pair it again" in str(msg.get("error")):
                            CONFIG.unlink(missing_ok=True)
                            raise SystemExit("  Run the companion again to pair.")
                        continue
                    if "id" in msg:
                        def work(m=msg):
                            reply = ({"id": m["id"], "ok": False, "error": "Control is paused on this computer."}
                                     if STATE["paused"] else handle(m))
                            with lock:
                                ws.send(json.dumps(reply))
                        threading.Thread(target=work, daemon=True).start()
        except SystemExit:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            STATE.update(connected=False, text="Not connected, retrying…")
            say(f"Not connected ({exc.__class__.__name__}: {str(exc)[:120]}). Retrying in {delay}s…")
            time.sleep(delay)
            delay = min(60, delay * 2)


def main() -> None:
    print(f"\n  Brain Companion {VERSION}  ·  this computer: {platform.node()} ({platform.system()})")
    if not devices.PC.installed():
        print("  Note: screen and mouse control packages are missing; run start-companion again to install them.")
    if "--unlink" in sys.argv:
        CONFIG.unlink(missing_ok=True)
        print("  Unlinked. Run again to pair.")
        return
    cfg = load_cfg()
    if not cfg.get("token"):
        cfg = pair(cfg)
    else:
        print(f"  Linked to {cfg.get('user', {}).get('name', 'your Brain')} at {cfg['server']}  (run with --unlink to change)")
    try:
        serve(cfg)
    except KeyboardInterrupt:
        print("\n  Stopped. Your Brain can't control this computer until you start the companion again.")


if __name__ == "__main__":
    main()
