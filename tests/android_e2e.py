"""End-to-end check of the Android app on an emulator (run in CI by .github/workflows/android.yml):
start a Brain, install the app, link it with an API token, switch on phone control, then let the Brain
read the phone screen, press Home and open an app, the same way a voice command would.
    python tests/android_e2e.py path/to/Brain.apk
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["BRAIN_DATA_DIR"] = tempfile.mkdtemp()
os.environ["BRAIN_NO_SCHEDULER"] = "1"
os.environ["BRAIN_OWNER_NAME"] = "Tester"
PKG = "app.aibrain.brain"


def adb(*args, check=True) -> str:
    r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        raise SystemExit(f"adb {' '.join(args)} failed: {r.stderr}")
    return r.stdout


def main(apk: str) -> int:
    import uvicorn
    from brain import accounts, db, devices, phonelink, config
    from brain.server import app
    accounts.init()
    db.init()
    token = accounts.make_token(0, "Emulator")[0]
    srv = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8765, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(3)

    adb("install", "-r", "-g", apk)                        # -g: grant the runtime permissions
    prefs = (f'<?xml version="1.0" encoding="utf-8"?><map><string name="url">http://10.0.2.2:8765</string>'
             f'<string name="token">{token}</string><boolean name="listen" value="false" /></map>')
    subprocess.run(["adb", "shell", f"run-as {PKG} sh -c 'mkdir -p shared_prefs && cat > shared_prefs/brain.xml'"],
                   input=prefs, text=True, check=True)
    adb("shell", "settings", "put", "secure", "enabled_accessibility_services", f"{PKG}/{PKG}.ControlService")
    adb("shell", "settings", "put", "secure", "accessibility_enabled", "1")
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")

    key = str(config.ROOT_DATA)
    for _ in range(60):
        if phonelink.connected(key):
            break
        time.sleep(1)
    else:
        print(adb("logcat", "-d", "-t", "200", check=False)[-4000:])
        raise SystemExit("FAIL: the app never linked to the Brain")
    print("linked:", phonelink.info(key))
    for _ in range(30):                                    # accessibility can take a moment to bind
        c = phonelink.connected(key)
        if c and c.info.get("accessibility"):
            break
        time.sleep(1)
    else:
        print(adb("logcat", "-d", "-t", "200", check=False)[-4000:])
        raise SystemExit("FAIL: phone control (Accessibility) never reported on")

    results = []
    print(devices.run("phone_key", {"key": "home"})["text"])
    time.sleep(2)
    home = devices.look("phone")["text"]
    print(home[:800])
    results.append("Phone screen elements" in home)
    print(devices.run("phone_open_app", {"name": "Settings"})["text"])
    time.sleep(3)
    settings_view = devices.look("phone")["text"]
    print(settings_view[:800])
    results.append("Settings" in settings_view)
    print(devices.run("phone_scroll", {"direction": "down"})["text"])
    print(devices.run("phone_key", {"key": "back"})["text"])
    crashes = adb("logcat", "-d", "-b", "crash", check=False)
    results.append(PKG not in crashes)
    if PKG in crashes:
        print(crashes[-3000:])
    print("PASS" if all(results) else "FAIL", results)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
