"""AI Brain desktop app: the whole Brain in its own window, no Python or terminal needed.

It starts the Brain on this computer (only this computer can reach it), then opens it in a native window
(Edge WebView2 on Windows, WebKit on macOS). Without a window engine it opens your browser instead.
Your data lives in your user folder, so updating the app keeps all memories and settings:
  Windows: %APPDATA%\\AI Brain     macOS: ~/Library/Application Support/AI Brain     Linux: ~/.local/share/ai-brain
"""
import os
import platform
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "AI Brain"


def data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "ai-brain"
    base.mkdir(parents=True, exist_ok=True)
    return base


# Set up before the Brain is imported: data outside the app, one person, no extra browser tab.
os.environ.setdefault("BRAIN_DATA_DIR", str(data_dir()))
os.environ.setdefault("BRAIN_NO_BROWSER", "1")
os.environ.pop("BRAIN_MULTIUSER", None)
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(HERE))


def brain_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start_brain(port: int) -> None:
    import uvicorn
    from brain.server import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(150):                       # up to 30 s on a slow first start
        if brain_running(port):
            return
        time.sleep(0.2)
    raise SystemExit("The Brain didn't start. Is something else using the port?")


def main() -> None:
    port = int(os.environ.get("BRAIN_PORT", "8765"))
    if not brain_running(port):               # already open (second launch)? just show another window
        if not port_free(port):
            port = next(p for p in range(8766, 8800) if port_free(p))
        start_brain(port)
    url = f"http://127.0.0.1:{port}"
    try:
        import webview
    except Exception:
        webview = None
    if webview is None or "--browser" in sys.argv:
        webbrowser.open(url)
        print(f"{APP_NAME} is running at {url}. Close this window to stop it.")
        while True:
            time.sleep(3600)
    win = webview.create_window(APP_NAME, url, width=1280, height=860, min_size=(380, 560), text_select=True)

    def open_in_browser():
        webbrowser.open(win.get_current_url() or url)
    try:
        from webview.menu import Menu, MenuAction
        menu = [Menu("Brain", [MenuAction("Open in my browser (best for voice)", open_in_browser),
                               MenuAction("Reload", lambda: win.load_url(url))])]
    except Exception:
        menu = []
    webview.start(menu=menu, private_mode=False, storage_path=str(data_dir() / "webview"))


if __name__ == "__main__":
    main()
