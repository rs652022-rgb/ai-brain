"""The Brain's hands: drivers for this computer and an Android phone, and the list of actions.

Computer: mouse, keyboard and screenshots through pyautogui; apps, links, files, commands and power
through the operating system.
Android phone: Android Debug Bridge (ADB) over USB or Wi-Fi. Screenshots, the on-screen elements
(so even models that can't see images can tap the right button), taps, swipes, typing, apps, links,
calls and messages.
iPhone: Apple doesn't allow other apps to control an iPhone, so it isn't supported.

Every action is checked by agent.py against the owner's permissions before it reaches these drivers.
"""
import base64
import importlib.util
import io
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import config, control

SYSTEM = platform.system()          # Windows | Darwin | Linux
NO_WINDOW = 0x08000000 if SYSTEM == "Windows" else 0


class DeviceError(RuntimeError):
    pass


def _jpeg(img, max_w: int = 1280) -> dict:
    """Shrink a screenshot (a PIL image or PNG bytes) to send to the AI model."""
    from PIL import Image
    if isinstance(img, (bytes, bytearray)):
        img = Image.open(io.BytesIO(img))
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, max_w / w)
    if scale < 1:
        img = img.resize((round(w * scale), round(h * scale)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=68)
    return {"data": base64.b64encode(buf.getvalue()).decode(), "mime": "image/jpeg",
            "width": img.size[0], "height": img.size[1], "scale": scale, "real": [w, h]}


def _luhn_card(text: str) -> bool:
    """True if the text contains something that looks like a payment card number."""
    for m in re.finditer(r"(?:\d[ -]?){13,19}", text):
        digits = [int(d) for d in re.sub(r"\D", "", m.group())]
        if not 13 <= len(digits) <= 19:
            continue
        total = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2:
                d *= 2
                d -= 9 if d > 9 else 0
            total += d
        if total % 10 == 0:
            return True
    return False


def check_text(text: str) -> None:
    if _luhn_card(text):
        raise PermissionError("That looks like a card number. I never type payment details; please enter them yourself.")


# ---------- Commands that are never run, whatever the permissions ----------

BLOCKED_COMMANDS = [
    r"\bformat(\.com)?\s+[a-z]:", r"\bdiskpart\b", r"\bbcdedit\b", r"\bcipher\s+/w", r"\bvssadmin\b.*\bdelete\b",
    r"\breg(\.exe)?\s+delete\b", r"\bRemove-Item\b.*\b[A-Z]:\\(Windows|Program Files|Users)?\\?\s*(-|$)",
    r"\b(del|erase|rd|rmdir)\b.*\s/s\b.*\b[a-z]:\\(\s|$|windows|program)", r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\*|/\*)(\s|$)",
    r"\bmkfs\b", r"\bdd\s+if=", r":\(\)\s*\{.*\};", r"\bSet-MpPreference\b", r"\bDisable-(WindowsDefender|Defender)",
    r"\bnetsh\s+advfirewall\s+set\b.*\boff\b", r"\b(iex|Invoke-Expression)\b", r"\|\s*(sh|bash|powershell|pwsh|iex)\b",
    r"\bnet\s+user\b.*\s/add\b", r"\bnet\s+localgroup\s+administrators\b", r"\bschtasks\b.*\s/create\b",
    r"\bcertutil\b.*-urlcache", r"\bbitsadmin\b", r"\bwevtutil\s+cl\b", r"Clear-EventLog", r"\bsdelete\b",
    r"\bshutdown\b", r"Stop-Computer", r"Restart-Computer",       # use the power actions instead (they always ask)
]
_BLOCKED = [re.compile(p, re.I) for p in BLOCKED_COMMANDS]


def check_command(cmd: str) -> None:
    for rx in _BLOCKED:
        if rx.search(cmd):
            raise PermissionError("That command is blocked for safety (it could wipe data, weaken security, "
                                  "or download and run code). Run it yourself if you really mean it.")


# ---------- This computer ----------

WIN_APPS = {
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge", "firefox": "firefox",
    "brave": "brave", "notepad": "notepad", "calculator": "calc", "calc": "calc", "paint": "mspaint",
    "file explorer": "explorer", "explorer": "explorer", "files": "explorer", "my computer": "explorer",
    "this pc": "explorer", "command prompt": "cmd", "cmd": "cmd", "powershell": "powershell", "terminal": "wt",
    "task manager": "taskmgr", "settings": "ms-settings:", "control panel": "control", "word": "winword",
    "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook", "vs code": "code", "vscode": "code",
    "visual studio code": "code", "whatsapp": "whatsapp:", "spotify": "spotify:", "camera": "microsoft.windows.camera:",
    "microsoft store": "ms-windows-store:", "store": "ms-windows-store:", "photos": "ms-photos:", "clock": "ms-clock:",
    "alarms": "ms-clock:", "snipping tool": "ms-screenclip:", "wifi settings": "ms-settings:network-wifi",
    "bluetooth settings": "ms-settings:bluetooth", "display settings": "ms-settings:display",
    "sound settings": "ms-settings:sound",
}
KEY_ALIASES = {"control": "ctrl", "windows": "win", "cmd": "command", "return": "enter", "escape": "esc",
               "del": "delete", "pgup": "pageup", "pgdn": "pagedown", "arrowup": "up", "arrowdown": "down",
               "arrowleft": "left", "arrowright": "right", "volume up": "volumeup", "volume down": "volumedown",
               "mute": "volumemute", "play": "playpause", "pause": "playpause", "next": "nexttrack",
               "previous": "prevtrack", "super": "win", "option": "alt", "spacebar": "space"}


class PC:
    name = "pc"
    label = "This computer"

    def __init__(self):
        self._gui = None
        self.scale = 1.0

    @staticmethod
    def installed() -> bool:
        return all(importlib.util.find_spec(m) is not None for m in ("pyautogui", "PIL"))

    def gui(self):
        if self._gui is None:
            if not self.installed():
                raise DeviceError("PC control isn't installed yet. Open Control → This computer → Install PC control.")
            if SYSTEM == "Windows":       # real pixels, so screenshot and click coordinates match on scaled displays
                import ctypes
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    try:
                        ctypes.windll.user32.SetProcessDPIAware()
                    except Exception:
                        pass
            if importlib.util.find_spec("tkinter") is None:
                # pyautogui's optional helper "mouseinfo" quits the whole program when Tkinter is missing
                # (Python installed without Tcl/Tk). Block it so pyautogui loads without it.
                sys.modules.setdefault("mouseinfo", None)
            try:
                import pyautogui
            except BaseException as exc:   # e.g. no display
                if isinstance(exc, KeyboardInterrupt):
                    raise
                raise DeviceError(f"Can't reach the screen: {exc}") from exc
            pyautogui.FAILSAFE = True       # slam the mouse into a screen corner to stop everything
            pyautogui.PAUSE = 0.12
            self._gui = pyautogui
        return self._gui

    def _xy(self, x, y) -> tuple[int, int]:
        """Coordinates the model gave (on the shrunk screenshot) -> real screen pixels."""
        try:
            return round(float(x) / self.scale), round(float(y) / self.scale)
        except (TypeError, ValueError):
            raise DeviceError("x and y must be numbers")

    # Seeing
    def screenshot(self) -> dict:
        g = self.gui()
        try:
            from PIL import ImageGrab           # fast, and works on Windows, macOS and Linux (X11)
            img = ImageGrab.grab()
        except Exception:
            img = g.screenshot()
        shot = _jpeg(img)
        self.scale = shot["scale"]
        return shot

    def windows(self) -> dict:
        if SYSTEM != "Windows":
            return {"windows": [], "active": ""}
        import pygetwindow as gw
        titles = [t for t in gw.getAllTitles() if t.strip()]
        act = gw.getActiveWindow()
        return {"windows": list(dict.fromkeys(titles))[:40], "active": act.title if act else ""}

    def _find_window(self, title: str):
        import pygetwindow as gw
        t = title.lower().strip()
        for w in gw.getAllWindows():
            if w.title and t in w.title.lower():
                return w
        raise DeviceError(f"No open window matches \"{title}\".")

    def focus_window(self, title: str) -> str:
        if SYSTEM != "Windows":
            raise DeviceError("Switching windows by name works on Windows only. Use alt+tab instead.")
        w = self._find_window(title)
        try:
            if w.isMinimized:
                w.restore()
            w.activate()
        except Exception:
            self.gui().press("alt")          # Windows sometimes refuses focus without a key press first
            w.activate()
        return f"Switched to \"{w.title}\"."

    def window_state(self, title: str, state: str) -> str:
        if SYSTEM != "Windows":
            raise DeviceError("Works on Windows only.")
        w = self._find_window(title)
        {"minimize": w.minimize, "maximize": w.maximize, "restore": w.restore, "close": w.close}[state]()
        return f"{state.title()}d \"{w.title}\"."

    def clipboard(self) -> str:
        import pyperclip
        return (pyperclip.paste() or "")[:4000]

    def set_clipboard(self, text: str) -> str:
        import pyperclip
        check_text(text)
        pyperclip.copy(text)
        return "Copied to the clipboard."

    # Apps and links
    def open_app(self, name: str) -> str:
        n = name.lower().strip().removesuffix(".exe")
        if SYSTEM == "Windows":
            target = WIN_APPS.get(n)
            if target:
                subprocess.Popen(["cmd", "/c", "start", "", target], creationflags=NO_WINDOW)
                return f"Opened {name}."
            g = self.gui()                  # anything else: search the Start menu, like you would
            g.press("win"); time.sleep(0.7)
            g.write(name, interval=0.02); time.sleep(1.0)
            g.press("enter")
            return f"Searched the Start menu for \"{name}\" and pressed Enter."
        if SYSTEM == "Darwin":
            r = subprocess.run(["open", "-a", name], capture_output=True, text=True)
            if r.returncode:
                raise DeviceError(r.stderr.strip() or f"Couldn't open {name}")
            return f"Opened {name}."
        exe = shutil.which(n) or shutil.which(n.replace(" ", "-"))
        if not exe:
            raise DeviceError(f"Couldn't find an app called {name}.")
        subprocess.Popen([exe], start_new_session=True)
        return f"Opened {name}."

    def open_url(self, url: str) -> str:
        if not re.match(r"^https?://", url or "", re.I):
            url = "https://" + url.strip()
        webbrowser.open(url)
        return f"Opened {url}."

    # Mouse and keyboard
    def click(self, x, y, button="left", clicks=1) -> str:
        if button not in ("left", "right", "middle"):
            raise DeviceError("button must be left, right or middle")
        rx, ry = self._xy(x, y)
        self.gui().click(rx, ry, clicks=max(1, min(3, int(clicks))), interval=0.08, button=button)
        return f"{'Double-c' if int(clicks) == 2 else 'C'}licked ({x}, {y})" + (f" with the {button} button." if button != "left" else ".")

    def move(self, x, y) -> str:
        self.gui().moveTo(*self._xy(x, y), duration=0.15)
        return f"Moved the mouse to ({x}, {y})."

    def drag(self, x1, y1, x2, y2) -> str:
        g = self.gui()
        g.moveTo(*self._xy(x1, y1))
        g.dragTo(*self._xy(x2, y2), duration=0.4, button="left")
        return f"Dragged from ({x1}, {y1}) to ({x2}, {y2})."

    def scroll(self, amount, x=None, y=None) -> str:
        g = self.gui()
        if x is not None and y is not None:
            g.moveTo(*self._xy(x, y))
        clicks = int(amount)
        g.scroll(clicks * 120 if SYSTEM == "Windows" else clicks)
        return f"Scrolled {'up' if clicks > 0 else 'down'} {abs(clicks)}."

    def type_text(self, text: str, enter: bool = False) -> str:
        check_text(text)
        g = self.gui()
        if text.isascii() and len(text) <= 300:
            g.write(text, interval=0.01)
        else:                                # Hindi, emoji, long text: paste it
            import pyperclip
            old = ""
            try:
                old = pyperclip.paste()
            except Exception:
                pass
            pyperclip.copy(text)
            g.hotkey("command" if SYSTEM == "Darwin" else "ctrl", "v")
            time.sleep(0.2)
            try:
                pyperclip.copy(old)
            except Exception:
                pass
        if enter:
            g.press("enter")
        return f"Typed {len(text)} characters" + (" and pressed Enter." if enter else ".")

    def press(self, keys: str) -> str:
        g = self.gui()
        names = [KEY_ALIASES.get(k.strip().lower(), k.strip().lower()) for k in re.split(r"\s*\+\s*", keys) if k.strip()]
        bad = [k for k in names if k not in g.KEYBOARD_KEYS]
        if not names or bad:
            raise DeviceError(f"Unknown key: {', '.join(bad) or keys}")
        g.hotkey(*names) if len(names) > 1 else g.press(names[0])
        return f"Pressed {'+'.join(names)}."

    # Files (only inside the folders the owner allowed)
    def list_folder(self, path: str) -> str:
        p = control.folder_allowed(path)
        if not p.is_dir():
            raise DeviceError(f"{p} is not a folder.")
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:150]
        lines = [f"{'[folder] ' if i.is_dir() else ''}{i.name}" + ("" if i.is_dir() else f"  ({i.stat().st_size:,} bytes)")
                 for i in items]
        return f"{p}:\n" + ("\n".join(lines) or "(empty)")

    def read_file(self, path: str) -> str:
        p = control.folder_allowed(path)
        if not p.is_file():
            raise DeviceError(f"{p} is not a file.")
        if p.suffix.lower() in (".pdf", ".docx"):
            from . import web
            return web.read_file(p.name, p.read_bytes())["text"][:12000]
        return p.read_text(encoding="utf-8", errors="replace")[:12000]

    def write_file(self, path: str, content: str, append: bool = False) -> str:
        p = control.folder_allowed(path)
        check_text(content)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a" if append else "w", encoding="utf-8") as f:
            f.write(content)
        return f"{'Added to' if append else 'Saved'} {p}."

    def make_folder(self, path: str) -> str:
        p = control.folder_allowed(path)
        p.mkdir(parents=True, exist_ok=True)
        return f"Created {p}."

    def delete(self, path: str) -> str:
        """Never permanent: moves the item into a _deleted_by_brain folder next to it, so you can undo."""
        p = control.folder_allowed(path)
        if not p.exists():
            raise DeviceError(f"{p} doesn't exist.")
        if any(p == Path(f).expanduser().resolve() for f in control.folders()):
            raise PermissionError("I won't remove a whole allowed folder.")
        bin_ = p.parent / "_deleted_by_brain"
        bin_.mkdir(exist_ok=True)
        dest = bin_ / f"{time.strftime('%Y%m%d-%H%M%S')}_{p.name}"
        shutil.move(str(p), str(dest))
        return f"Moved {p.name} to {bin_} (not permanently deleted; move it back to undo)."

    # Commands
    def run_command(self, command: str) -> str:
        check_command(command)
        if SYSTEM == "Windows":
            args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            args = ["bash", "-lc", command]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=90, cwd=str(Path.home()),
                               creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return "The command was still running after 90 seconds, so I stopped waiting."
        out = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()
        return f"Exit code {r.returncode}.\n{out[-3500:] or '(no output)'}"

    # Power
    def power(self, what: str) -> str:
        cmds = {
            "Windows": {"lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
                        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                        "shutdown": ["shutdown", "/s", "/t", "60"], "restart": ["shutdown", "/r", "/t", "60"],
                        "cancel": ["shutdown", "/a"]},
            "Darwin": {"lock": ["pmset", "displaysleepnow"], "sleep": ["pmset", "sleepnow"],
                       "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
                       "restart": ["osascript", "-e", 'tell app "System Events" to restart']},
            "Linux": {"lock": ["loginctl", "lock-session"], "sleep": ["systemctl", "suspend"],
                      "shutdown": ["shutdown", "-h", "+1"], "restart": ["shutdown", "-r", "+1"],
                      "cancel": ["shutdown", "-c"]},
        }.get(SYSTEM, {})
        if what not in cmds:
            raise DeviceError(f"Can't {what} on this system.")
        subprocess.Popen(cmds[what], creationflags=NO_WINDOW)
        extra = " in 60 seconds (say \"cancel shutdown\" to stop it)" if what in ("shutdown", "restart") and SYSTEM == "Windows" else ""
        return f"{what.title()} started{extra}."


# ---------- Android phone (ADB) ----------

ANDROID_KEYS = {"home": 3, "back": 4, "recents": 187, "app switch": 187, "enter": 66, "delete": 67, "backspace": 67,
                "power": 26, "volume up": 24, "volume down": 25, "mute": 164, "play": 85, "pause": 85,
                "playpause": 85, "next": 87, "previous": 88, "search": 84, "menu": 82, "camera": 27, "tab": 61,
                "wake": 224, "sleep": 223, "notifications": -1}
ANDROID_APPS = {
    "whatsapp": "com.whatsapp", "whatsapp business": "com.whatsapp.w4b", "youtube": "com.google.android.youtube",
    "chrome": "com.android.chrome", "instagram": "com.instagram.android", "facebook": "com.facebook.katana",
    "gmail": "com.google.android.gm", "maps": "com.google.android.apps.maps", "google maps": "com.google.android.apps.maps",
    "spotify": "com.spotify.music", "settings": "com.android.settings", "play store": "com.android.vending",
    "telegram": "org.telegram.messenger", "linkedin": "com.linkedin.android", "twitter": "com.twitter.android",
    "x": "com.twitter.android", "photos": "com.google.android.apps.photos", "google photos": "com.google.android.apps.photos",
    "calendar": "com.google.android.calendar", "drive": "com.google.android.apps.docs", "gpay": "com.google.android.apps.nbu.paisa.user",
    "phonepe": "com.phonepe.app", "paytm": "net.one97.paytm", "zomato": "com.application.zomato", "swiggy": "in.swiggy.android",
    "amazon": "in.amazon.mShop.android.shopping", "flipkart": "com.flipkart.android", "uber": "com.ubercab", "ola": "com.olacabs.customer",
    "netflix": "com.netflix.mediaclient", "hotstar": "in.startv.hotstar", "jiohotstar": "in.startv.hotstar",
    "messages": "com.google.android.apps.messaging", "phone": "com.google.android.dialer", "dialer": "com.google.android.dialer",
    "contacts": "com.google.android.contacts", "clock": "com.google.android.deskclock", "calculator": "com.google.android.calculator",
    "keep": "com.google.android.keep", "google": "com.google.android.googlequicksearchbox",
}
PLATFORM_TOOLS = {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}


def tools_dir() -> Path:
    d = config.DATA_DIR / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Phone:
    name = "phone"
    label = "Android phone"

    def __init__(self):
        self.scale = 1.0
        self.elements: list[dict] = []

    # ADB itself
    def adb_path(self) -> str:
        c = control.get()
        exe = "adb.exe" if SYSTEM == "Windows" else "adb"
        for cand in (c.get("adb_path"), str(tools_dir() / "platform-tools" / exe), shutil.which("adb")):
            if cand and Path(cand).is_file():
                return cand
        raise DeviceError("ADB isn't installed. Open Control → Android phone → Install ADB.")

    def installed(self) -> bool:
        try:
            self.adb_path()
            return True
        except DeviceError:
            return False

    def install_adb(self) -> dict:
        """Download Google's official Android platform-tools into data/tools. Started only by the owner."""
        if config.MULTIUSER:
            raise DeviceError("On the web version, the Brain Companion app on your computer installs ADB.")
        import httpx
        os_name = PLATFORM_TOOLS.get(SYSTEM)
        if not os_name:
            raise DeviceError("Unsupported system")
        url = f"https://dl.google.com/android/repository/platform-tools-latest-{os_name}.zip"
        try:
            r = httpx.get(url, timeout=180, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise DeviceError(f"Download failed: {exc}") from exc
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for member in z.namelist():           # stay inside data/tools
                if member.startswith("/") or ".." in Path(member).parts:
                    raise DeviceError("Unexpected file in the download")
            z.extractall(tools_dir())
        path = self.adb_path()
        if SYSTEM != "Windows":
            os.chmod(path, 0o755)
        return {"ok": True, "adb": path}

    def _adb(self, *args, timeout=25, binary=False, serial=True):
        cmd = [self.adb_path()]
        if serial:
            cmd += ["-s", self.serial()]
        try:
            r = subprocess.run(cmd + list(args), capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise DeviceError("The phone didn't answer in time. Is it unlocked and connected?")
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).decode(errors="replace").strip()
            if "unauthorized" in msg:
                msg = "The phone hasn't allowed this computer yet. Unlock it and tap Allow on the USB debugging prompt."
            raise DeviceError(msg[:300] or "ADB command failed")
        return r.stdout if binary else r.stdout.decode(errors="replace")

    def devices(self) -> list[dict]:
        out = self._adb("devices", "-l", serial=False, timeout=15)
        found = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "")
                found.append({"serial": parts[0], "state": parts[1], "model": model.replace("_", " ")})
        return found

    def serial(self) -> str:
        want = control.get().get("phone_serial")
        ready = [d for d in self.devices() if d["state"] == "device"]
        if want and any(d["serial"] == want for d in ready):
            return want
        if ready:
            return ready[0]["serial"]
        raise DeviceError("No Android phone is connected. Plug it in with USB debugging on, or connect over Wi-Fi in Control.")

    def pair(self, address: str, code: str) -> str:
        if not re.fullmatch(r"[\w.\-]+:\d{2,5}", address) or not re.fullmatch(r"\d{6}", code):
            raise DeviceError("Use the IP:port and 6-digit code shown under Wireless debugging → Pair device with pairing code.")
        return self._adb("pair", address, code, serial=False, timeout=30).strip()

    def connect(self, address: str) -> str:
        if not re.fullmatch(r"[\w.\-]+:\d{2,5}", address):
            raise DeviceError("Use the IP address and port shown under Wireless debugging, like 192.168.1.5:41234.")
        out = self._adb("connect", address, serial=False, timeout=20).strip()
        if "connected" not in out:
            raise DeviceError(out or "Couldn't connect")
        return out

    def shell(self, command: str, **kw) -> str:
        return self._adb("shell", command, **kw)

    def _xy(self, x, y) -> tuple[int, int]:
        try:
            return round(float(x) / self.scale), round(float(y) / self.scale)
        except (TypeError, ValueError):
            raise DeviceError("x and y must be numbers")

    # Seeing
    def screenshot(self) -> dict:
        png = self._adb("exec-out", "screencap", "-p", binary=True, timeout=20)
        if not png.startswith(b"\x89PNG"):
            raise DeviceError("The phone didn't send a screenshot. Unlock the screen and try again.")
        shot = _jpeg(png, max_w=720)
        self.scale = shot["scale"]
        return shot

    def ui(self) -> str:
        """The tappable and readable things on the phone screen, numbered, with their centre points."""
        raw = self._adb("exec-out", "uiautomator", "dump", "/dev/tty", timeout=20)
        xml = raw[raw.find("<?xml"):raw.rfind(">") + 1] if "<?xml" in raw else raw
        self.elements = parse_ui(xml)
        return format_elements(self.elements, self.scale)

    # Touch and keys
    def tap(self, x, y) -> str:
        rx, ry = self._xy(x, y)
        self.shell(f"input tap {rx} {ry}")
        return f"Tapped ({x}, {y})."

    def tap_element(self, number) -> str:
        try:
            e = self.elements[int(number) - 1]
        except (ValueError, IndexError, TypeError):
            raise DeviceError("No element with that number. Look at the phone screen again first.")
        self.shell(f"input tap {e['x']} {e['y']}")
        return f"Tapped [{number}] {e['label']}."

    def long_press(self, x, y) -> str:
        rx, ry = self._xy(x, y)
        self.shell(f"input swipe {rx} {ry} {rx} {ry} 700")
        return f"Long-pressed ({x}, {y})."

    def swipe(self, x1, y1, x2, y2, ms=350) -> str:
        a, b = self._xy(x1, y1)
        c, d = self._xy(x2, y2)
        self.shell(f"input swipe {a} {b} {c} {d} {max(50, min(3000, int(ms)))}")
        return f"Swiped from ({x1}, {y1}) to ({x2}, {y2})."

    def scroll(self, direction: str) -> str:
        size = re.search(r"(\d+)x(\d+)", self.shell("wm size"))
        w, h = (int(size.group(1)), int(size.group(2))) if size else (1080, 2340)
        cx, top, bottom = w // 2, int(h * 0.3), int(h * 0.75)
        a, b = (bottom, top) if direction == "down" else (top, bottom)
        self.shell(f"input swipe {cx} {a} {cx} {b} 350")
        return f"Scrolled {direction}."

    def type_text(self, text: str, enter: bool = False) -> str:
        check_text(text)
        if not text.isascii():
            raise DeviceError("Typing on the phone over ADB supports English letters only. Use voice typing or "
                              "send the text as a message instead.")
        safe = text.replace("%", "\\%").replace(" ", "%s")
        self.shell("input text " + shlex.quote(safe))
        if enter:
            self.shell("input keyevent 66")
        return f"Typed {len(text)} characters" + (" and pressed Enter." if enter else ".")

    def key(self, name: str) -> str:
        n = name.lower().strip()
        if n == "notifications":
            self.shell("cmd statusbar expand-notifications")
            return "Opened the notifications."
        code = ANDROID_KEYS.get(n)
        if code is None:
            raise DeviceError(f"Unknown phone key: {name}. Use one of: {', '.join(ANDROID_KEYS)}")
        self.shell(f"input keyevent {code}")
        return f"Pressed {n}."

    # Apps, links, calls, messages
    def _package(self, name: str) -> str:
        n = name.lower().strip()
        if re.fullmatch(r"[a-z][\w]*(\.[\w]+)+", n):
            return n
        if n in ANDROID_APPS:
            return ANDROID_APPS[n]
        pkgs = [l.split(":", 1)[1].strip() for l in self.shell("pm list packages -3").splitlines() if ":" in l]
        key = re.sub(r"\W", "", n)
        hits = [p for p in pkgs if key and key in p.lower().replace(".", "")]
        if hits:
            return sorted(hits, key=len)[0]
        raise DeviceError(f"Couldn't find an app called {name} on the phone.")

    def open_app(self, name: str) -> str:
        pkg = self._package(name)
        out = self.shell(f"monkey -p {shlex.quote(pkg)} -c android.intent.category.LAUNCHER 1")
        if "No activities found" in out:
            raise DeviceError(f"{name} can't be opened from the launcher.")
        return f"Opened {name} on the phone."

    def open_url(self, url: str) -> str:
        if not re.match(r"^https?://", url or "", re.I):
            url = "https://" + url.strip()
        self.shell(f"am start -a android.intent.action.VIEW -d {shlex.quote(url)}")
        return f"Opened {url} on the phone."

    @staticmethod
    def _number(number: str) -> str:
        n = re.sub(r"[\s()-]", "", str(number or ""))
        if not re.fullmatch(r"\+?\d{3,15}", n):
            raise DeviceError("That isn't a phone number. Use digits, with the country code if needed (+91…).")
        return n

    def call(self, number: str) -> str:
        n = self._number(number)
        self.shell(f"am start -a android.intent.action.CALL -d tel:{shlex.quote(n)}")
        return f"Calling {n}."

    def sms(self, number: str, text: str) -> str:
        n = self._number(number)
        check_text(text)
        self.shell(f"am start -a android.intent.action.SENDTO -d sms:{shlex.quote(n)} --es sms_body {shlex.quote(text)}")
        return f"Opened a text to {n} with the message filled in. Tap Send to send it."

    def whatsapp(self, number: str, text: str) -> str:
        from urllib.parse import quote
        n = self._number(number).lstrip("+")
        check_text(text)
        self.shell(f"am start -a android.intent.action.VIEW -d {shlex.quote(f'https://wa.me/{n}?text={quote(text)}')}")
        return f"Opened WhatsApp to {n} with the message filled in. Tap Send to send it."


def parse_ui(xml: str) -> list[dict]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for node in root.iter("node"):
        label = (node.get("text") or node.get("content-desc") or "").strip()
        clickable = node.get("clickable") == "true" or node.get("long-clickable") == "true"
        rid = (node.get("resource-id") or "").split("/")[-1]
        if not label and not (clickable and rid):
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        kind = (node.get("class") or "").split(".")[-1]
        out.append({"label": (label or rid)[:80], "kind": kind, "tap": clickable, "x": (x1 + x2) // 2, "y": (y1 + y2) // 2,
                    "edit": kind == "EditText"})
    return out[:120]


def format_elements(elements: list[dict], scale: float = 1.0) -> str:
    if not elements:
        return "(no readable elements on the phone screen)"
    return "\n".join(f"[{i}] \"{e['label']}\" {e['kind']}{' (tap)' if e['tap'] else ''}{' (text box)' if e['edit'] else ''}"
                     f" at ({round(e['x'] * scale)}, {round(e['y'] * scale)})" for i, e in enumerate(elements, 1))


# ---------- The action list (what the AI model can ask for) ----------
# name: (device, category, arguments, description, always_ask)

ACTIONS = {
    # this computer
    "screenshot":      ("pc", "observe", "", "Look at the computer screen again", False),
    "list_windows":    ("pc", "observe", "", "List open windows and which one is active", False),
    "read_clipboard":  ("pc", "observe", "", "Read the clipboard", False),
    "open_app":        ("pc", "apps", '{"name": "chrome"}', "Open an app by name (Chrome, Notepad, Word, Settings, WhatsApp…)", False),
    "open_url":        ("pc", "apps", '{"url": "https://…"}', "Open a website in the default browser", False),
    "focus_window":    ("pc", "apps", '{"title": "part of the window title"}', "Switch to an open window", False),
    "window":          ("pc", "apps", '{"title": "…", "state": "minimize|maximize|restore|close"}', "Minimize, maximize, restore or close a window", False),
    "click":           ("pc", "input", '{"x": 0, "y": 0, "button": "left", "clicks": 1}', "Click at a point on the latest screenshot (clicks 2 = double-click, button right = right-click)", False),
    "move_mouse":      ("pc", "input", '{"x": 0, "y": 0}', "Move the mouse (hover)", False),
    "drag":            ("pc", "input", '{"x1": 0, "y1": 0, "x2": 0, "y2": 0}', "Drag with the left button", False),
    "scroll":          ("pc", "input", '{"amount": -5, "x": 0, "y": 0}', "Scroll (negative = down) optionally over a point", False),
    "type":            ("pc", "input", '{"text": "…", "enter": false}', "Type text where the cursor is (any language)", False),
    "press":           ("pc", "input", '{"keys": "ctrl+s"}', "Press a key or shortcut: enter, esc, tab, win, alt+tab, ctrl+c, volumeup, playpause…", False),
    "set_clipboard":   ("pc", "input", '{"text": "…"}', "Put text on the clipboard", False),
    "list_folder":     ("pc", "files_read", '{"path": "C:/Users/…/Desktop"}', "List a folder", False),
    "read_file":       ("pc", "files_read", '{"path": "…"}', "Read a text, PDF or Word file", False),
    "write_file":      ("pc", "files_write", '{"path": "…", "content": "…", "append": false}', "Create or overwrite a text file", False),
    "make_folder":     ("pc", "files_write", '{"path": "…"}', "Create a folder", False),
    "delete":          ("pc", "files_write", '{"path": "…"}', "Remove a file or folder (moved to _deleted_by_brain, can be undone)", True),
    "run_command":     ("pc", "commands", '{"command": "Get-Date"}', f"Run a {'PowerShell' if SYSTEM == 'Windows' else 'shell'} command and read its output", False),
    "lock_screen":     ("pc", "power", "", "Lock the computer", False),
    "sleep_pc":        ("pc", "power", "", "Put the computer to sleep", True),
    "shutdown_pc":     ("pc", "power", "", "Shut the computer down (after 60 seconds)", True),
    "restart_pc":      ("pc", "power", "", "Restart the computer (after 60 seconds)", True),
    "cancel_shutdown": ("pc", "power", "", "Cancel a shutdown or restart that is counting down", False),
    # Android phone
    "phone_screen":    ("phone", "observe", "", "Look at the phone screen again (screenshot and numbered elements)", False),
    "phone_tap":       ("phone", "input", '{"x": 0, "y": 0}', "Tap a point on the latest phone screenshot", False),
    "phone_tap_element": ("phone", "input", '{"number": 3}', "Tap a numbered element from the phone's element list (best choice)", False),
    "phone_long_press": ("phone", "input", '{"x": 0, "y": 0}', "Long-press a point", False),
    "phone_swipe":     ("phone", "input", '{"x1": 0, "y1": 0, "x2": 0, "y2": 0}', "Swipe", False),
    "phone_scroll":    ("phone", "input", '{"direction": "down"}', "Scroll up or down", False),
    "phone_type":      ("phone", "input", '{"text": "…", "enter": false}', "Type into the focused text box (English only)", False),
    "phone_key":       ("phone", "input", '{"key": "home"}', "Press home, back, recents, enter, power, volume up/down, notifications…", False),
    "phone_open_app":  ("phone", "apps", '{"name": "whatsapp"}', "Open an app on the phone", False),
    "phone_open_url":  ("phone", "apps", '{"url": "https://…"}', "Open a link on the phone", False),
    "phone_call":      ("phone", "messages", '{"number": "+91…"}', "Call a phone number", True),
    "phone_sms":       ("phone", "messages", '{"number": "+91…", "text": "…"}', "Open an SMS with the text filled in", True),
    "phone_whatsapp":  ("phone", "messages", '{"number": "+91…", "text": "…"}', "Open a WhatsApp chat with the message filled in", True),
}
OBSERVE_ACTIONS = {"screenshot", "phone_screen", "list_windows", "read_clipboard", "list_folder", "read_file"}

_pc = PC()
_phone = Phone()


def driver(device: str):
    return _pc if device == "pc" else _phone


def _remote(payload: dict) -> dict:
    """On a server with accounts, device actions run on the user's own computer via the companion."""
    from . import hub
    return hub.call(config.user_key(), payload | {"folders": control.get()["folders"]})


def run(action: str, args: dict) -> dict:
    """Carry out one action that has already been permitted. Returns {'text': ..., 'shot': optional}."""
    if config.MULTIUSER:
        return _remote({"op": "run", "action": action, "args": args or {}})
    return run_local(action, args)


def run_local(action: str, args: dict) -> dict:
    a = args or {}
    pc, ph = _pc, _phone
    table = {
        "screenshot": lambda: {"text": "Took a screenshot.", "shot": pc.screenshot()},
        "list_windows": lambda: {"text": _fmt_windows(pc.windows())},
        "read_clipboard": lambda: {"text": "Clipboard: " + (pc.clipboard() or "(empty)")},
        "open_app": lambda: pc.open_app(str(a["name"])),
        "open_url": lambda: pc.open_url(str(a["url"])),
        "focus_window": lambda: pc.focus_window(str(a["title"])),
        "window": lambda: pc.window_state(str(a["title"]), str(a.get("state", "maximize")).lower()),
        "click": lambda: pc.click(a["x"], a["y"], a.get("button", "left"), a.get("clicks", 1)),
        "move_mouse": lambda: pc.move(a["x"], a["y"]),
        "drag": lambda: pc.drag(a["x1"], a["y1"], a["x2"], a["y2"]),
        "scroll": lambda: pc.scroll(a.get("amount", -5), a.get("x"), a.get("y")),
        "type": lambda: pc.type_text(str(a["text"]), bool(a.get("enter"))),
        "press": lambda: pc.press(str(a["keys"])),
        "set_clipboard": lambda: pc.set_clipboard(str(a["text"])),
        "list_folder": lambda: pc.list_folder(str(a["path"])),
        "read_file": lambda: pc.read_file(str(a["path"])),
        "write_file": lambda: pc.write_file(str(a["path"]), str(a.get("content", "")), bool(a.get("append"))),
        "make_folder": lambda: pc.make_folder(str(a["path"])),
        "delete": lambda: pc.delete(str(a["path"])),
        "run_command": lambda: pc.run_command(str(a["command"])),
        "lock_screen": lambda: pc.power("lock"),
        "sleep_pc": lambda: pc.power("sleep"),
        "shutdown_pc": lambda: pc.power("shutdown"),
        "restart_pc": lambda: pc.power("restart"),
        "cancel_shutdown": lambda: pc.power("cancel"),
        "phone_screen": lambda: _phone_look(),
        "phone_tap": lambda: ph.tap(a["x"], a["y"]),
        "phone_tap_element": lambda: ph.tap_element(a["number"]),
        "phone_long_press": lambda: ph.long_press(a["x"], a["y"]),
        "phone_swipe": lambda: ph.swipe(a["x1"], a["y1"], a["x2"], a["y2"], a.get("ms", 350)),
        "phone_scroll": lambda: ph.scroll("up" if str(a.get("direction", "down")).lower() == "up" else "down"),
        "phone_type": lambda: ph.type_text(str(a["text"]), bool(a.get("enter"))),
        "phone_key": lambda: ph.key(str(a["key"])),
        "phone_open_app": lambda: ph.open_app(str(a["name"])),
        "phone_open_url": lambda: ph.open_url(str(a["url"])),
        "phone_call": lambda: ph.call(str(a["number"])),
        "phone_sms": lambda: ph.sms(str(a["number"]), str(a.get("text", ""))),
        "phone_whatsapp": lambda: ph.whatsapp(str(a["number"]), str(a.get("text", ""))),
    }
    if action not in table:
        raise DeviceError(f"Unknown action {action}")
    try:
        out = table[action]()
    except KeyError as exc:
        raise DeviceError(f"Missing argument {exc} for {action}") from exc
    except Exception as exc:
        if exc.__class__.__name__ == "FailSafeException":
            raise PermissionError("Stopped: the mouse was pushed into a screen corner (emergency stop).") from exc
        raise
    return out if isinstance(out, dict) else {"text": out}


def _fmt_windows(w: dict) -> str:
    if not w["windows"]:
        return "Window list isn't available on this system."
    return f"Active: {w['active'] or '(none)'}\nOpen windows:\n" + "\n".join(f"- {t}" for t in w["windows"])


def _phone_look() -> dict:
    shot = _phone.screenshot()
    return {"text": "Phone screen elements:\n" + _phone.ui(), "shot": shot}


def look(device: str) -> dict:
    """A fresh view of a device: a screenshot plus any text description."""
    if config.MULTIUSER:
        return _remote({"op": "look", "device": device})
    return look_local(device)


def look_local(device: str) -> dict:
    if device == "phone":
        return _phone_look()
    out = {"text": "", "shot": _pc.screenshot()}
    try:
        out["text"] = _fmt_windows(_pc.windows())
    except Exception:
        pass
    return out


def status() -> dict:
    if config.MULTIUSER:
        from . import hub
        conn = hub.connected(config.user_key())
        base = {"system": "", "pc_installed": False, "adb_installed": False, "phones": [], "phone_error": ""}
        if not conn:
            return base | {"remote": True, "connected": False}
        return base | {k: v for k, v in conn.info.items() if k in base} | {
            "remote": True, "connected": True, "device_name": conn.device["name"]}
    return status_local()


def status_local() -> dict:
    pc_ok = PC.installed()
    out = {"system": SYSTEM, "pc_installed": pc_ok, "adb_installed": _phone.installed(), "phones": [],
           "phone_error": ""}
    if out["adb_installed"]:
        try:
            out["phones"] = _phone.devices()
        except DeviceError as exc:
            out["phone_error"] = str(exc)
    return out


def install_pc() -> dict:
    """Install the PC control packages into this app's Python. Started only by the owner."""
    if config.MULTIUSER:
        raise DeviceError("On the web version, install the Brain Companion app on your computer instead.")
    pkgs = ["pyautogui", "pyperclip", "pillow"] + (["pygetwindow"] if SYSTEM == "Windows" else [])
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise DeviceError(f"Install failed: {(r.stderr or r.stdout)[-400:]}")
    return {"ok": True, "installed": pkgs}
