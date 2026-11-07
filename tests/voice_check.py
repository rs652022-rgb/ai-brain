"""End-to-end check of the activation-word listener with real speech (run in CI, needs internet once):
speak a sentence with espeak-ng, feed it to the listener, and check it woke up and heard the command.
    python tests/voice_check.py
"""
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brain.listener import Listener  # noqa: E402

work = Path(tempfile.mkdtemp())
results = []
for wake, sentence, expect in [("hey brain", "hey brain. what time is it", "time"),
                               ("hey jarvis", "hey jarvis, open the calculator", "calculator")]:
    wav = work / "say.wav"
    subprocess.run(["espeak-ng", "-s", "140", "-w", str(wav), sentence], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ar", "16000", "-ac", "1", str(work / "16k.wav")], check=True)
    with wave.open(str(work / "16k.wav")) as w:
        pcm = w.readframes(w.getnframes())
    heard = []
    lis = Listener(work, lambda t: heard.append(t) or "ok", [wake], "en")
    lis.run_on_audio(b"\0" * 32000 + pcm)
    print(f"{wake!r}: heard {heard}")
    results.append(bool(heard) and expect in heard[0])
    # without the wake word nothing happens
    heard.clear()
    subprocess.run(["espeak-ng", "-s", "140", "-w", str(wav), "what time is it"], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ar", "16000", "-ac", "1", str(work / "16k.wav")], check=True)
    with wave.open(str(work / "16k.wav")) as w:
        Listener(work, lambda t: heard.append(t) or "ok", [wake], "en").run_on_audio(w.readframes(w.getnframes()))
    print("  without wake word:", heard)
    results.append(not heard)
# what the offline model writes for the custom phrase, and its dictionary
import json, vosk  # noqa: E401
from brain.listener import model_path
mp = model_path(work, "en")
print("model files:", sorted(str(p.relative_to(mp)) for p in mp.rglob("*") if p.is_file())[:40])
subprocess.run(["espeak-ng", "-s", "140", "-w", str(wav), "hey jarvis, open the calculator"], check=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ar", "16000", "-ac", "1", str(work / "16k.wav")], check=True)
rec = vosk.KaldiRecognizer(vosk.Model(str(mp)), 16000)
with wave.open(str(work / "16k.wav")) as w:
    rec.AcceptWaveform(w.readframes(w.getnframes()))
print("raw transcript:", json.loads(rec.FinalResult()))
print("PASS" if all(results) else "FAIL", results)
sys.exit(0 if all(results) else 1)
