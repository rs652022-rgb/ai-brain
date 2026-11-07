"""Brain Companion as a menu-bar app (macOS) / tray app (Windows, Linux).

Same as the Brain Companion window, but it lives in the menu bar: an icon shows whether your Brain is
connected, and the menu lets you open your Brain, pause control of this computer, unlink, or quit.
Quitting stops all control at once.
"""
import platform
import sys
import threading
import webbrowser

import brain_companion as bc


def _icon(connected: bool, paused: bool):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    colour = (150, 150, 150, 255) if not connected else ((230, 160, 30, 255) if paused else (61, 90, 254, 255))
    d.ellipse((6, 6, 58, 58), fill=colour)
    d.ellipse((20, 18, 44, 42), outline=(255, 255, 255, 255), width=5)       # a simple "brain" ring
    d.line((32, 40, 32, 50), fill=(255, 255, 255, 255), width=5)
    return img


def pair_dialog() -> dict | None:
    """Windowed pairing (no terminal needed)."""
    import tkinter as tk
    from tkinter import messagebox
    import httpx
    result = {}
    root = tk.Tk()
    root.title("Link this computer to your Brain")
    root.resizable(False, False)
    frm = tk.Frame(root, padx=16, pady=14)
    frm.pack()
    tk.Label(frm, text="Open your Brain → Control → Your computers → Make a pairing code.", wraplength=360, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
    entries = {}
    for i, (key, label, default) in enumerate((("server", "Brain address", "https://"), ("code", "Pairing code", ""),
                                               ("name", "Name for this computer", platform.node() or "My computer")), 1):
        tk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=3)
        e = tk.Entry(frm, width=34)
        e.insert(0, default)
        e.grid(row=i, column=1, pady=3)
        entries[key] = e
    allow = tk.BooleanVar(value=False)
    tk.Checkbutton(frm, text="Allow this Brain to control this computer, within the permissions set in Control",
                   variable=allow, wraplength=340, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=8)

    def go():
        server = entries["server"].get().strip().rstrip("/")
        if not server.startswith(("http://", "https://")):
            server = "https://" + server
        if not allow.get():
            messagebox.showwarning("Brain Companion", "Tick the box to allow control first.")
            return
        try:
            r = httpx.post(f"{server}/api/companion/pair", json={"code": entries["code"].get().strip(),
                                                                 "name": entries["name"].get().strip() or "My computer"}, timeout=30)
            data = r.json()
            if r.status_code != 200:
                raise RuntimeError(data.get("detail") or r.text)
        except Exception as exc:
            messagebox.showerror("Brain Companion", f"Couldn't link: {exc}")
            return
        cfg = {"server": server, "token": data["token"], "name": entries["name"].get().strip(), "user": data["user"]}
        bc.save_cfg(cfg)
        result.update(cfg)
        root.destroy()

    tk.Button(frm, text="Link", command=go, width=12).grid(row=5, column=1, sticky="e")
    root.mainloop()
    return result or None


def main() -> None:
    import pystray
    cfg = bc.load_cfg()
    if not cfg.get("token"):
        cfg = pair_dialog()
        if not cfg:
            return
    threading.Thread(target=_serve, args=(cfg,), daemon=True).start()

    def label(_item=None):
        return bc.STATE["text"] + (" (paused)" if bc.STATE["paused"] else "")

    def toggle_pause(icon, _item):
        bc.STATE["paused"] = not bc.STATE["paused"]
        refresh(icon)

    def unlink(icon, _item):
        bc.CONFIG.unlink(missing_ok=True)
        icon.stop()

    def refresh(icon):
        icon.icon = _icon(bc.STATE["connected"], bc.STATE["paused"])
        icon.title = f"Brain Companion: {label()}"
        icon.update_menu()

    menu = pystray.Menu(
        pystray.MenuItem(label, None, enabled=False),
        pystray.MenuItem("Open my Brain", lambda *_: webbrowser.open(cfg["server"]), default=True),
        pystray.MenuItem("Pause control of this computer", toggle_pause, checked=lambda _i: bc.STATE["paused"]),
        pystray.MenuItem(lambda _i: "Activation word: " + {"off": "off", "waiting": "listening", "listening": "hearing you…",
                                                           "thinking": "working…", "speaking": "answering…", "loading": "starting…"}.get(bc.STATE.get("listening", "off"), "on"),
                         None, enabled=False),
        pystray.MenuItem("Pause listening", lambda icon, _i: (bc.set_paused(not bc.VOICE["paused"]), refresh(icon)),
                         checked=lambda _i: bc.VOICE["paused"]),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Unlink this computer", unlink),
        pystray.MenuItem("Quit (stops all control)", lambda icon, _i: icon.stop()),
    )
    icon = pystray.Icon("brain-companion", _icon(False, False), "Brain Companion", menu)

    def watch():
        last = None
        while True:
            now = (bc.STATE["connected"], bc.STATE["paused"], bc.STATE["text"], bc.STATE.get("listening"))
            if now != last:
                try:
                    refresh(icon)
                except Exception:
                    pass
                last = now
            threading.Event().wait(1.5)
    threading.Thread(target=watch, daemon=True).start()
    icon.run()


def _serve(cfg):
    try:
        bc.serve(cfg)
    except SystemExit as exc:
        bc.STATE.update(connected=False, text=str(exc) or "Unlinked: open the app again to pair")


if __name__ == "__main__":
    sys.exit(main())
