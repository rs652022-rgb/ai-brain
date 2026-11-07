"""Always listening for your activation word, like Siri, on your laptop. Works without internet.

The microphone is processed on this computer by Vosk (free, open source, offline speech recognition).
Nothing is recorded or sent anywhere until it hears your activation word. Then it listens to your command,
sends only the TEXT of it to your Brain, and speaks the answer aloud (offline voice). Say "stop" to
interrupt. Switch it off any time (Settings, or the tray menu).

Used by the desktop app / laptop Brain (in-process) and by the Brain Companion tray app (sends the command
to your Brain over its link).

Needs: pip install vosk sounddevice pyttsx3 (bundled in the desktop and tray apps).
Speech models download once (about 40 MB each): English and Hindi.
"""
import io
import json
import logging
import os
import queue
import re
import threading
import time
import zipfile
from pathlib import Path

log = logging.getLogger("brain.listener")

MODELS = {
    "en": ("vosk-model-small-en-us-0.15", "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"),
    "hi": ("vosk-model-small-hi-0.22", "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip"),
}
RATE = 16000
STOP_WORDS = ("stop", "ruko", "bas", "cancel")


def installed() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) for m in ("vosk", "sounddevice"))


def models_dir(base: Path) -> Path:
    d = base / "speech-models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_path(base: Path, lang: str) -> Path | None:
    name = MODELS.get(lang, MODELS["en"])[0]
    p = models_dir(base) / name
    return p if (p / "am").exists() or (p / "conf").exists() else None


def download_model(base: Path, lang: str, progress=None) -> Path:
    import httpx
    name, url = MODELS.get(lang, MODELS["en"])
    dest = models_dir(base)
    buf = io.BytesIO()
    with httpx.stream("GET", url, timeout=httpx.Timeout(60, connect=15), follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        for chunk in r.iter_bytes(1 << 16):
            buf.write(chunk)
            if progress and total:
                progress(buf.tell() / total)
    with zipfile.ZipFile(buf) as z:
        for n in z.namelist():
            target = (dest / n).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise RuntimeError("Unsafe file in the speech model download.")
        z.extractall(dest)
    return dest / name


def _norm(text: str) -> str:
    text = (text or "").lower().replace("[unk]", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text)).strip()


class Listener:
    """state: off | loading | waiting (for the wake word) | listening (to a command) | thinking | speaking"""

    def __init__(self, base: Path, handler, wake_words: list[str], lang: str = "en", on_state=None):
        self.base, self.handler, self.lang = base, handler, lang
        self.wake_words = [w for w in (_norm(x) for x in wake_words) if w] or ["hey brain"]
        self.on_state = on_state or (lambda s, t="": None)
        self.state, self.last_heard, self.last_reply, self.error = "off", "", "", ""
        self._stop = threading.Event()
        self._thread = None
        self._tts_lock = threading.Lock()
        self._speaking = threading.Event()

    def _set(self, state: str, text: str = "") -> None:
        self.state = state
        try:
            self.on_state(state, text)
        except Exception:
            pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="brain-listener")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._set("off")

    # ---- speaking (offline voice) ----
    def speak(self, text: str) -> None:
        text = re.sub(r"[*_#`>\[\]]", "", text or "")[:1200]
        if not text.strip():
            return
        with self._tts_lock:
            self._speaking.set()
            self._set("speaking", text)
            try:
                import pyttsx3
                eng = pyttsx3.init()
                eng.say(text)
                eng.runAndWait()
            except Exception as exc:
                log.info("Speaking failed: %s", exc)
            finally:
                self._speaking.clear()

    def _beep(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            t = np.linspace(0, 0.12, int(RATE * 0.12), False)
            sd.play((0.25 * np.sin(2 * np.pi * 880 * t)).astype("float32"), RATE)
            sd.wait()
        except Exception:
            pass

    # ---- the listening loop ----
    def _model(self):
        import vosk
        vosk.SetLogLevel(-1)
        path = model_path(self.base, self.lang)
        if not path:
            path = download_model(self.base, self.lang)
        model = vosk.Model(str(path))
        # One recogniser hears everything, so the words right after the activation word are never lost
        # ("hey brain what time is it" in one breath works, and so does a pause after "hey brain").
        return model, vosk.KaldiRecognizer(model, RATE)

    def _run(self) -> None:
        try:
            import sounddevice as sd
            import vosk  # noqa: F401
        except Exception as exc:
            self.error = f"Voice packages missing: {exc}"
            self._set("off", self.error)
            return
        self._set("loading")
        try:
            model, wake_rec = self._model()
        except Exception as exc:
            self.error = f"Couldn't load the speech model (the first time needs internet): {exc}"
            self._set("off", self.error)
            return
        audio: queue.Queue = queue.Queue(maxsize=200)

        def cb(indata, frames, t, status):
            try:
                audio.put_nowait(bytes(indata))
            except queue.Full:
                pass

        try:
            stream = sd.RawInputStream(samplerate=RATE, blocksize=4000, dtype="int16", channels=1, callback=cb)
        except Exception as exc:
            self.error = f"No microphone: {exc}"
            self._set("off", self.error)
            return
        self.error = ""
        with stream:
            self._loop(model, wake_rec, audio)

    def run_on_audio(self, pcm: bytes) -> None:
        """Feed recorded 16 kHz mono 16-bit audio instead of the microphone (used to test the whole flow)."""
        model, wake_rec = self._model()
        audio: queue.Queue = queue.Queue()
        for i in range(0, len(pcm), 8000):
            audio.put(pcm[i:i + 8000])
        for _ in range(8):                              # 2 s of silence at the end
            audio.put(b"\0" * 8000)
        self._file_mode = True
        self._loop(model, wake_rec, audio)

    def _wake_split(self, text: str) -> tuple[bool, str]:
        """(heard the activation word?, what was said after it). Exact words only, so everyday talk
        ("the brain is…") never wakes it."""
        words = text.split()
        for w in self.wake_words:
            ws = w.split()
            for i in range(0, len(words) - len(ws) + 1):
                if words[i:i + len(ws)] == ws:
                    return True, " ".join(words[i + len(ws):])
        return False, ""

    def _loop(self, model, rec, audio: queue.Queue) -> None:
        self._set("waiting")
        file_mode = getattr(self, "_file_mode", False)
        woke = False
        while not self._stop.is_set():
            try:
                data = audio.get(timeout=0.5)
            except queue.Empty:
                if file_mode:
                    data = None
                else:
                    continue
            final = data is None or rec.AcceptWaveform(data)
            heard = _norm(json.loads(rec.FinalResult() if data is None else rec.Result()).get("text", "") if final
                          else json.loads(rec.PartialResult()).get("partial", ""))
            if self._speaking.is_set():
                if heard and any(w in heard.split() for w in STOP_WORDS):
                    self._interrupt()
                    rec.Reset()
                continue
            hit, after = self._wake_split(heard)
            if hit and not final and not woke:
                woke = True                               # the user hears the beep while still speaking
                self._set("listening")
                if not file_mode:
                    self._beep()
            if final:
                if hit:
                    woke = False
                    rec.Reset()
                    if after:
                        self._handle(after)
                    else:                                 # "hey brain" ... (pause) ... "open chrome"
                        self._command(model, audio)
                    if not file_mode:
                        while not audio.empty():          # don't react to our own voice
                            audio.get_nowait()
                    self._set("waiting")
                elif woke:
                    woke = False
                    self._set("waiting")
            if data is None:
                return

    def _handle(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.last_heard = text
        self._set("thinking", text)
        try:
            reply = self.handler(text)
        except Exception as exc:
            reply = f"Sorry, {exc}"
        self.last_reply = reply or ""
        if reply and not getattr(self, "_file_mode", False):
            # speak in the background so "stop" can interrupt it
            threading.Thread(target=self.speak, args=(reply,), daemon=True).start()
            time.sleep(0.3)

    def _interrupt(self) -> None:
        try:
            import pyttsx3
            pyttsx3.init().stop()
        except Exception:
            pass

    def _command(self, model, audio: queue.Queue) -> None:
        """After a bare activation word: listen to the next thing said (up to 15 s)."""
        import vosk
        self._set("listening")
        file_mode = getattr(self, "_file_mode", False)
        rec = vosk.KaldiRecognizer(model, RATE)
        text, started, last_voice = "", time.time(), time.time()
        while time.time() - started < 15 and not self._stop.is_set():
            try:
                data = audio.get(timeout=0.5)
            except queue.Empty:
                if file_mode:
                    break
                continue
            if rec.AcceptWaveform(data):
                part = json.loads(rec.Result()).get("text", "")
                if part:
                    text = (text + " " + part).strip()
                    last_voice = time.time()
                elif text and time.time() - last_voice > 1.2:
                    break
            elif json.loads(rec.PartialResult()).get("partial"):
                last_voice = time.time()
            elif not text and time.time() - started > 6:
                break                                   # nothing said after the wake word
        text = (text + " " + json.loads(rec.FinalResult()).get("text", "")).strip()
        hit, after = self._wake_split(text)
        self._handle(after if hit else text)


# ---------- The laptop Brain's own listener (desktop app, start.bat/start.sh) ----------

_local: dict = {"listener": None}


def _local_handler(text: str) -> str:
    from . import core
    out = core.think(text, mode="assistant", conversation="main", voice=True)
    return out["reply"]


def local_status() -> dict:
    from . import config, settings
    c = settings.load().get("listener") or {}
    lis = _local["listener"]
    if config.MULTIUSER:
        from . import hub
        conn = hub.connected(config.user_key())
        return {"available": True, "companion": True, "installed": True, "enabled": bool(c.get("enabled")),
                "lang": c.get("lang", "en"), "state": (conn.info.get("listener_state") if conn else "") or ("off" if not conn else "waiting"),
                "heard": (conn.info.get("listener_heard") if conn else "") or "", "reply": "",
                "error": "" if conn else "Open the Brain Companion on your computer: it does the listening.", "model_ready": True}
    return {"available": True, "installed": installed(), "enabled": bool(c.get("enabled")),
            "lang": c.get("lang", "en"), "state": lis.state if lis else "off", "heard": lis.last_heard if lis else "",
            "reply": lis.last_reply if lis else "", "error": lis.error if lis else "",
            "model_ready": bool(model_path(config.ROOT_DATA, c.get("lang", "en")))}


def local_set(enabled: bool, lang: str = "en") -> dict:
    from . import config, db, master, settings
    if lang not in MODELS:
        raise ValueError("Language is en or hi.")
    data = settings.load()
    data["listener"] = {"enabled": bool(enabled), "lang": lang}
    settings.save(data)
    db.audit("owner", "listener.on" if enabled else "listener.off", lang=lang)
    if config.MULTIUSER:                      # on a server, the Brain Companion on your computer does the listening
        tell_companion()
        return local_status()
    old = _local["listener"]
    if old:
        old.stop()
        _local["listener"] = None
    if enabled:
        if not installed():
            raise RuntimeError("Voice packages aren't installed: pip install vosk sounddevice pyttsx3 (the desktop app has them).")
        lis = Listener(config.ROOT_DATA, _local_handler, master.get().get("wake_words") or ["hey brain"], lang)
        _local["listener"] = lis
        lis.start()
    return local_status()


def autostart() -> None:
    from . import config, settings
    if config.MULTIUSER or os.environ.get("BRAIN_NO_SCHEDULER") == "1":
        return
    c = settings.load().get("listener") or {}
    if c.get("enabled") and installed():
        try:
            local_set(True, c.get("lang", "en"))
        except Exception as exc:
            log.warning("Listener didn't start: %s", exc)


def companion_settings() -> dict:
    """What the Brain Companion on the user's computer needs to listen (sent when it connects or this changes)."""
    from . import master, settings
    c = settings.load().get("listener") or {}
    return {"type": "listen", "enabled": bool(c.get("enabled")), "lang": c.get("lang", "en"),
            "wake_words": master.get().get("wake_words") or ["hey brain"]}


def tell_companion() -> None:
    import asyncio
    from . import config, hub
    conn = hub.connected(config.user_key())
    if conn:
        try:
            asyncio.run_coroutine_threadsafe(conn.send(companion_settings()), conn.loop).result(5)
        except Exception:
            pass


def restart_if_running() -> None:
    """The activation word changed in Master: listen for the new one."""
    from . import config, settings
    if config.MULTIUSER:
        tell_companion()
        return
    c = settings.load().get("listener") or {}
    if c.get("enabled") and _local["listener"]:
        local_set(True, c.get("lang", "en"))
