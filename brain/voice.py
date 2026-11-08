"""Voice: listening (speech to text) and speaking (text to speech).

Listening engines
  browser  - Chrome/Edge built-in recognition. Free; Chrome sends audio to Google. Runs in the page.
  local    - Whisper on your laptop (faster-whisper). Free and private.
  groq     - Whisper on Groq. Free tier, very fast. Uses your Groq key from Settings.
Speaking engines
  edge     - Microsoft neural voices (the ones in Edge's Read Aloud). Free, no key, very natural, many
             Indian English and Hindi voices. Needs internet; text is sent to Microsoft to be spoken.
  browser  - The voices built into your browser/OS. Free, offline, instant, but robotic.
  piper    - Neural voices on your laptop. Free and private, less expressive than edge.
"""
import io
import logging
import re
import subprocess
import sys
import threading
import wave

import httpx

from . import config, settings

log = logging.getLogger("brain.voice")

DEFAULTS = {
    "stt": "browser",             # browser | local | groq | freellmapi
    "tts": "edge",                # edge | browser | piper | freellmapi | off
    "freellmapi_voice": "alloy",
    "edge_voice": "en-IN-PrabhatNeural",
    "language": "en",             # en | hi | auto  (Hinglish works with en or auto)
    "whisper_model": "base",      # tiny | base | small | medium  (bigger = better and slower)
    "piper_voice": "en_US-lessac-medium",
    "speak_replies": True,        # read replies aloud after you speak
    "wake_word": "hey brain",
    "browser_voice": "",          # a voice name from the browser list, or "" for automatic
    "rate": 1.0,
    "barge_in": True,             # in a conversation, stop talking when the owner starts
    "patience": "normal",         # quick | normal | relaxed: how long to wait after the owner goes quiet
}


class VoiceError(RuntimeError):
    pass


def cfg() -> dict:
    saved = settings.load().get("voice") or {}
    if saved.get("tts") == "browser" and "edge_voice" not in saved:
        saved = saved | {"tts": "edge"}   # saved before the natural voice existed: upgrade to it
    out = DEFAULTS | saved
    if out.get("patience") not in ("quick", "normal", "relaxed"):
        out["patience"] = "normal"
    from . import master
    words = master.get()["wake_words"]
    if words:
        out["wake_word"] = words[0]           # the wake word is set in Master Control
    return out


def voices_dir():
    d = config.DATA_DIR / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- What's installed ----------

def has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def capabilities() -> dict:
    c = cfg()
    piper_model = voices_dir() / f"{c['piper_voice']}.onnx"
    groq_key = bool(settings.load().get("providers", {}).get("groq", {}).get("api_key"))
    return {
        "local_whisper_installed": has_module("faster_whisper"),
        "piper_installed": has_module("piper"),
        "piper_voice_ready": piper_model.exists(),
        "piper_voices": sorted(p.stem for p in voices_dir().glob("*.onnx")),
        "groq_key": groq_key,
        "edge_installed": has_module("edge_tts"),
        "edge_voices": EDGE_VOICES,
    }


def install(component: str) -> dict:
    """Install an optional local voice package into this app's Python. Started only by the owner."""
    if config.MULTIUSER:
        raise VoiceError("Installing voice packages isn't possible from the web version.")
    pkg = {"whisper": "faster-whisper", "piper": "piper-tts", "edge": "edge-tts"}.get(component)
    if not pkg:
        raise VoiceError("Unknown component")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise VoiceError(f"Install failed: {(r.stderr or r.stdout)[-400:]}")
    return {"ok": True, "installed": pkg}


def download_piper_voice(name: str) -> dict:
    if config.MULTIUSER:
        raise VoiceError("Piper voices can't be downloaded on the web version.")
    if not re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)", name):
        raise VoiceError("Voice names look like en_US-lessac-medium or hi_IN-pratham-medium.")
    if not has_module("piper"):
        raise VoiceError("Install Piper first.")
    r = subprocess.run([sys.executable, "-m", "piper.download_voices", "--download-dir", str(voices_dir()), name],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not (voices_dir() / f"{name}.onnx").exists():
        raise VoiceError(f"Could not download '{name}'. Check the name in the Piper voice list. {(r.stderr or '')[-200:]}")
    return {"ok": True, "voice": name}


# ---------- Listening ----------

_whisper = {"model": None, "size": None}
_whisper_lock = threading.Lock()


def _local_whisper(audio: bytes, language: str | None) -> str:
    if not has_module("faster_whisper"):
        raise VoiceError("Local Whisper is not installed. Install it in Settings → Voice.")
    from faster_whisper import WhisperModel
    size = cfg()["whisper_model"]
    with _whisper_lock:
        if _whisper["model"] is None or _whisper["size"] != size:
            # First use downloads the model (base is about 150 MB) into data/whisper.
            _whisper["model"] = WhisperModel(size, device="auto", compute_type="int8",
                                             download_root=str(config.DATA_DIR / "whisper"))
            _whisper["size"] = size
        segments, _info = _whisper["model"].transcribe(io.BytesIO(audio), language=language, vad_filter=True, beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()


def _groq_whisper(audio: bytes, filename: str, language: str | None) -> str:
    key = settings.load().get("providers", {}).get("groq", {}).get("api_key")
    if not key:
        raise VoiceError("Add a Groq key in Settings → AI models to use Groq for listening.")
    data = {"model": "whisper-large-v3-turbo", "response_format": "json"}
    if language:
        data["language"] = language
    r = httpx.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {key}"},
                   data=data, files={"file": (filename, audio)}, timeout=60)
    if r.status_code != 200:
        raise VoiceError(f"Groq transcription failed (HTTP {r.status_code}): {r.text[:160]}")
    return r.json().get("text", "").strip()


def transcribe(audio: bytes, filename: str = "speech.webm", engine: str | None = None) -> str:
    c = cfg()
    engine = engine or c["stt"]
    language = None if c["language"] == "auto" else c["language"]
    if len(audio) < 1000:
        raise VoiceError("The recording was empty. Check that your microphone is allowed.")
    if engine == "local":
        if not config.allow_private():
            raise VoiceError("Local Whisper isn't available on the shared web version. Choose Browser or Groq Whisper in Settings → Voice.")
        return _local_whisper(audio, language)
    if engine == "groq":
        return _groq_whisper(audio, filename, language)
    if engine == "freellmapi":
        from . import freellm
        try:
            return freellm.transcribe(audio, filename, language)
        except freellm.FreeLLMError as exc:
            raise VoiceError(str(exc)) from exc
    raise VoiceError("Browser listening happens in the page, not on the server.")


# ---------- Speaking ----------

_piper = {"voice": None, "name": None}


def clean_for_speech(text: str) -> str:
    """Make a reply sound natural: drop links, citations, markdown and code."""
    text = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[(?:K|W)?\d+\]|\(#\d+\)|#\d+\b", "", text)          # [K1] [W2] [3] (#12)
    text = re.sub(r"^\s*(Sources?|Sources learned):.*$", "", text, flags=re.M | re.S)
    text = re.sub(r"[*_`#>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.!?;:।])", r"\1", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


EDGE_VOICES = {
    "en-IN-PrabhatNeural": "Prabhat: Indian English, male (says Hindi with Madhur)",
    "en-IN-NeerjaNeural": "Neerja: Indian English, female (says Hindi with Swara)",
    "en-IN-NeerjaExpressiveNeural": "Neerja Expressive: Indian English, female, warmer",
    "hi-IN-MadhurNeural": "Madhur: Hindi and Hinglish, male",
    "hi-IN-SwaraNeural": "Swara: Hindi and Hinglish, female",
    "en-US-AndrewMultilingualNeural": "Andrew: multilingual, male (very natural)",
    "en-US-BrianMultilingualNeural": "Brian: multilingual, male, relaxed",
    "en-US-EmmaMultilingualNeural": "Emma: multilingual, female (very natural)",
    "en-US-AvaMultilingualNeural": "Ava: multilingual, female, bright",
    "en-GB-RyanNeural": "Ryan: British English, male",
}

# English-only voices read Hindi with an English accent. For Hindi text they hand over to the Hindi
# voice of the same gender, which also says English words with an Indian accent.
HINDI_PARTNER = {
    "en-IN-PrabhatNeural": "hi-IN-MadhurNeural",
    "en-IN-NeerjaNeural": "hi-IN-SwaraNeural",
    "en-IN-NeerjaExpressiveNeural": "hi-IN-SwaraNeural",
    "en-GB-RyanNeural": "hi-IN-MadhurNeural",
}
# Each natural voice and its partner of the other gender, used when the Brain's gender is changed.
_PAIRS = (("en-IN-PrabhatNeural", "en-IN-NeerjaNeural"), ("hi-IN-MadhurNeural", "hi-IN-SwaraNeural"),
          ("en-US-AndrewMultilingualNeural", "en-US-EmmaMultilingualNeural"),
          ("en-US-BrianMultilingualNeural", "en-US-AvaMultilingualNeural"))
VOICE_GENDER = {m: "male" for m, _ in _PAIRS} | {f: "female" for _, f in _PAIRS} | {
    "en-IN-NeerjaExpressiveNeural": "female", "en-GB-RyanNeural": "male"}
_PARTNER = {m: f for m, f in _PAIRS} | {f: m for m, f in _PAIRS} | {
    "en-IN-NeerjaExpressiveNeural": "en-IN-PrabhatNeural", "en-GB-RyanNeural": "en-IN-NeerjaNeural"}


def gender_partner(voice: str) -> str:
    return _PARTNER.get(voice, voice)


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def has_hindi(text: str) -> bool:
    """True when the text has Hindi written in Devanagari (the voice prompt asks for that)."""
    return bool(_DEVANAGARI.search(text or ""))


def edge_voice_for(text: str, chosen: str, language: str = "en") -> str:
    """The voice for this sentence: the chosen one, or its Hindi partner for Hindi."""
    chosen = chosen or DEFAULTS["edge_voice"]
    if chosen in HINDI_PARTNER and (has_hindi(text) or language == "hi"):
        return HINDI_PARTNER[chosen]
    return chosen


# One event loop for the natural voice, kept running: starting a new loop and connection setup for
# every sentence added a noticeable pause before each one.
_loop = {"loop": None}
_loop_lock = threading.Lock()


def _edge_loop():
    import asyncio
    with _loop_lock:
        if _loop["loop"] is None or _loop["loop"].is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="edge-voice", daemon=True).start()
            _loop["loop"] = loop
        return _loop["loop"]


def _edge(text: str) -> bytes:
    if not has_module("edge_tts"):
        raise VoiceError("The natural voice isn't installed. Run start again, or choose another voice in Settings.")
    import asyncio
    import edge_tts
    c = cfg()
    pct = round((float(c.get("rate") or 1.0) - 1.0) * 100)
    voice_name = edge_voice_for(text, c.get("edge_voice"), c.get("language", "en"))

    async def collect() -> bytes:
        comm = edge_tts.Communicate(text, voice_name, rate=f"{pct:+d}%", connect_timeout=5, receive_timeout=10)
        out = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                out.extend(chunk["data"])
        return bytes(out)

    # A hard limit: the service can hang when it is unreachable.
    fut = asyncio.run_coroutine_threadsafe(asyncio.wait_for(collect(), 8), _edge_loop())
    try:
        audio = fut.result(timeout=9)      # backstop in case the loop itself is stuck
    except Exception as exc:
        fut.cancel()
        raise VoiceError(f"Natural voice unavailable ({exc.__class__.__name__}). Check your internet.") from exc
    if not audio:
        raise VoiceError("Natural voice returned no audio.")
    return audio


def warm() -> None:
    """Open the natural voice's connection early (when a conversation starts), so the first reply has no
    extra wait. Runs in the background; failures are ignored."""
    if cfg()["tts"] not in ("edge", "freellmapi") or not has_module("edge_tts"):
        return

    def run():
        try:
            _edge("Hmm.")
        except VoiceError:
            pass
    threading.Thread(target=run, name="voice-warm", daemon=True).start()


def speak(text: str) -> tuple[bytes, str]:
    """Return (audio, mime type) for the text with the chosen server voice (edge or piper)."""
    text = clean_for_speech(text)[:4000]
    if not text:
        raise VoiceError("Nothing to say.")
    if cfg()["tts"] == "freellmapi":
        from . import freellm
        try:
            return freellm.speech(text, cfg().get("freellmapi_voice", "alloy"))
        except freellm.FreeLLMError:
            pass                                                   # fall back to the free Microsoft voice
    if cfg()["tts"] in ("edge", "freellmapi") or not config.allow_private():   # Piper runs on your own computer only
        return _edge(text), "audio/mpeg"
    return _piper_wav(text), "audio/wav"


def _piper_wav(text: str) -> bytes:
    """WAV audio of the text with the local Piper voice."""
    if not has_module("piper"):
        raise VoiceError("Piper is not installed. Install it in Settings → Voice.")
    name = cfg()["piper_voice"]
    model = voices_dir() / f"{name}.onnx"
    if not model.exists():
        raise VoiceError(f"Voice '{name}' is not downloaded yet. Download it in Settings → Voice.")
    from piper import PiperVoice
    if _piper["name"] != name:
        _piper["voice"], _piper["name"] = PiperVoice.load(str(model)), name
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        _piper["voice"].synthesize_wav(text, wav)
    return buf.getvalue()
