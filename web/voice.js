// Voice: listen, speak, hands-free conversation and wake word.
// Uses helpers from app.js: $, $$, el, api, note, sendChat, loaders, S.

const V = {
  cfg: { stt: "browser", tts: "edge", language: "en", speak_replies: true, wake_word: "hey brain", browser_voice: "", rate: 1 },
  caps: {},
  talk: false,        // hands-free conversation on
  wake: false,        // wake-word listening on
  busy: false,        // listening or thinking right now
  rec: null,          // active browser SpeechRecognition
  recorder: null,     // active MediaRecorder
  audio: null,        // active Piper playback
  stopFlag: false,
  speaker: null,      // the Speaker reading the current reply
};
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const STOP_DEFAULT = ["stop", "stop talking", "be quiet", "ruko", "ruk", "bas", "chup"];
const norm = (t) => t.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, "").replace(/\s+/g, " ").trim();
const isStop = (t) => [...STOP_DEFAULT, ...(window.BRAND?.stop_words || [])].some((w) => norm(t) === norm(w));
const wakeWords = () => (window.BRAND?.wake_words?.length ? window.BRAND.wake_words : ["hey brain"]).map(norm);

async function loadVoice() {
  try { const r = await api("/api/voice"); V.cfg = r.config; V.caps = r; } catch { /* keep defaults */ }
  $("#speak-toggle").classList.toggle("on", V.cfg.speak_replies !== false && V.cfg.tts !== "off");
}

function langCode() { return V.cfg.language === "hi" ? "hi-IN" : "en-IN"; }

function showBar(text) { $("#voice-bar").hidden = !text; if (text) $("#voice-state").textContent = text; }

// ---------- Speaking ----------
// A Speaker takes the reply as it streams in, cuts it into sentences, and speaks them one after another.
// The next sentence's audio is fetched while the current one plays, so there are no gaps.

function speechText(t) {
  return t.replace(/```[\s\S]*?```/g, " (code shown on screen) ")
    .replace(/https?:\/\/\S+/g, "").replace(/\[(?:K|W)?\d+\]|\(#\d+\)|#\d+\b/g, "")
    .replace(/^\s*(Sources?|Sources learned):[\s\S]*$/m, "")
    .replace(/[*_`#>|]+/g, "").replace(/^\s*[-•]\s*/gm, "").replace(/\s+/g, " ").replace(/\s+([,.!?;:।])/g, "$1").trim();
}

function pickVoice() {
  const voices = speechSynthesis.getVoices();
  if (V.cfg.browser_voice) return voices.find((v) => v.name === V.cfg.browser_voice);
  const want = langCode();
  const natural = (v) => /Natural|Online|Neural/i.test(v.name);   // Edge/Windows natural voices sound far better
  return voices.find((v) => v.lang === want && natural(v)) || voices.find((v) => v.lang === want)
    || voices.find((v) => v.lang.startsWith(want.slice(0, 2)) && natural(v)) || voices.find((v) => v.lang.startsWith(want.slice(0, 2)));
}

function browserSay(text) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window) || !text) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    const voice = pickVoice();
    if (voice) u.voice = voice;
    u.lang = voice?.lang || langCode();
    u.rate = Number(V.cfg.rate) || 1;
    u.onend = u.onerror = resolve;
    speechSynthesis.speak(u);
  });
}

async function fetchVoice(text) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 12000);  // never keep you waiting on a slow voice service
  try {
    const res = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }), signal: ctl.signal });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Voice failed");
    return URL.createObjectURL(await res.blob());
  } catch (e) {
    throw new Error(e.name === "AbortError" ? "The natural voice is slow right now." : e.message);
  } finally { clearTimeout(timer); }
}

function playUrl(url) {
  return new Promise((resolve) => {
    const a = new Audio(url);
    V.audio = a;
    a.onended = a.onerror = () => { URL.revokeObjectURL(url); if (V.audio === a) V.audio = null; resolve(); };
    a.play().catch(() => resolve());
  });
}

let voiceWarned = false;
class Speaker {
  constructor() {
    this.buf = ""; this.queue = []; this.cancelled = false; this.ended = false;
    this.heard = [];      // sentences you have heard (or started hearing), for when you interrupt
    this.pushed = 0;
    // After a failure, use the browser voice for 30 seconds, then try the natural voice again.
    this.server = (V.cfg.tts === "edge" || V.cfg.tts === "piper" || V.cfg.tts === "freellmapi") && Date.now() - (V.serverVoiceDown || 0) > 30000;
    this.done = new Promise((r) => (this._resolve = r));
    this.running = false;
  }
  feed(piece) {
    if (this.cancelled) return;
    this.buf += piece;
    // A sentence is ready at . ! ? । or a line break. Very short first bits wait for a little more text.
    const re = /^([\s\S]*?[.!?।\n]+)(\s+|$)/;
    let m;
    while ((m = this.buf.match(re)) && (m[1].trim().length >= 3 || this.buf.length > 60)) {
      this.push(m[1]); this.buf = this.buf.slice(m[0].length);
    }
    // Start talking sooner: the first bit can end at a comma once it's long enough to sound natural.
    const c = !this.pushed && this.buf.match(/^([\s\S]{24,}?[,;:–])\s+/);
    if (c) { this.push(c[1]); this.buf = this.buf.slice(c[0].length); }
  }
  end() { if (this.buf.trim()) this.push(this.buf); this.buf = ""; this.ended = true; this.kick(); }
  push(sentence) {
    const text = speechText(sentence);
    if (!text) return;
    this.pushed++;
    // Start fetching the audio now, so it's ready when its turn comes.
    const item = { text, url: this.server ? fetchVoice(text).catch((e) => { this.fallback(e); return null; }) : null };
    this.queue.push(item);
    this.kick();
  }
  fallback(e) {
    V.serverVoiceDown = Date.now();
    if (!voiceWarned) { voiceWarned = true; addMsg("assistant", `⚠ ${e.message} Using the browser voice for now.`); }
  }
  async kick() {
    if (this.running) return;
    this.running = true;
    showBar("Speaking… (Stop or Esc to interrupt)");
    while (!this.cancelled && (this.queue.length || !this.ended)) {
      if (!this.queue.length) { await new Promise((r) => setTimeout(r, 40)); continue; }
      const item = this.queue.shift();
      const url = item.url ? await item.url : null;
      if (this.cancelled) break;
      this.heard.push(item.text);
      if (url) await playUrl(url); else await browserSay(item.text);
    }
    this.running = false;
    if (this.ended || this.cancelled) this._resolve();
  }
  cancel() {
    this.cancelled = true; this.queue = [];
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (V.audio) { V.audio.pause(); V.audio = null; }
    this._resolve();
  }
}

async function speak(text) {          // speak a whole text (used by Test voice and short messages)
  if (!text || V.cfg.tts === "off") return;
  const s = new Speaker(); V.speaker = s;
  s.feed(text); s.end();
  await s.done;
  if (!V.talk && !V.wake) showBar("");
}

function stopSpeaking() {
  V.speaker?.cancel(); V.speaker = null;
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  if (V.audio) { V.audio.pause(); V.audio = null; }
}

// ---------- Listening ----------

function listenBrowser({ continuous = false, onPartial } = {}) {
  return new Promise((resolve, reject) => {
    if (!SR) return reject(new Error("This browser can't listen. Use Chrome or Edge, or choose Local/Groq Whisper in Settings → Voice."));
    const rec = new SR();
    V.rec = rec;
    rec.lang = langCode(); rec.interimResults = true; rec.continuous = continuous; rec.maxAlternatives = 1;
    let finalText = "";
    rec.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t + " "; else interim += t;
        if (continuous && ev.results[i].isFinal && onPartial?.(t) === "done") rec.stop();
      }
      if (!continuous) $("#chat-input").value = (finalText + interim).trim();
    };
    rec.onerror = (ev) => {
      if (ev.error === "no-speech" || ev.error === "aborted") return;
      reject(new Error(ev.error === "not-allowed" ? "Microphone access was blocked. Allow it in the browser's address bar." : `Listening error: ${ev.error}`));
    };
    rec.onend = () => { V.rec = null; resolve(finalText.trim()); };
    rec.start();
  });
}

// Records until you pause (about 1.3 s of silence), then sends the audio to Whisper.
async function listenServer() {
  const stream = await getMic();
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  V.recorder = recorder;
  const chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);

  const meter = micMeter(stream);
  let heard = false, quietSince = performance.now(), speechMs = 0, last = performance.now(), floor = 0.01;
  const started = performance.now();

  await new Promise((resolve) => {
    recorder.onstop = resolve;
    recorder.start(250);
    const tick = () => {
      if (recorder.state !== "recording") return;
      const rms = meter.level(), now = performance.now();
      if (!heard && now - started < 300) floor = Math.max(0.004, (floor + rms) / 2);   // the room's own level
      if (rms > Math.max(0.015, floor * 2.5)) { heard = true; quietSince = now; speechMs += now - last; }
      last = now;
      // A short "yes" may be followed by more, so wait a little longer after very short speech.
      const pause = speechMs < 900 ? 1100 : 750;
      const silentFor = now - quietSince, total = now - started;
      if ((heard && silentFor > pause) || (!heard && total > 8000) || total > 60000 || V.stopFlag) recorder.stop();
      else requestAnimationFrame(tick);
    };
    tick();
  });
  meter.close(); V.recorder = null;
  if (!V.talk) releaseMic();            // in a conversation the mic stays open for the next turn
  if (!heard || V.stopFlag) return "";

  showBar("Transcribing…");
  const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let bin = ""; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  const r = await api("/api/voice/transcribe", { method: "POST", body: { data_base64: btoa(bin), filename: "speech.webm" } });
  return r.text || "";
}

async function listenOnce() {
  showBar("Listening… speak now, then pause");
  $("#mic").classList.add("rec");
  try {
    if (V.cfg.stt !== "browser") return await listenServer();
    if (!SR) return await listenBrowser();          // shows the "use Chrome or Edge" message
    return await listenBrowserTurn(langCode(), (t) => { $("#chat-input").value = t; });
  } finally { $("#mic").classList.remove("rec"); }
}

function stopListening() {
  V.stopFlag = true;
  try { V.rec?.abort(); } catch {}
  if (V.recorder?.state === "recording") V.recorder.stop();
}

// ---------- One spoken turn ----------

async function handleSpoken(text, { speakReply }) {
  text = text.trim();
  if (!text) return;
  if (isStop(text)) { stopSpeaking(); return null; }
  showBar("Thinking…");
  const speaker = speakReply && V.cfg.tts !== "off" ? new Speaker() : null;
  if (speaker) V.speaker = speaker;
  const ctl = new AbortController();
  // In a conversation you can talk over the Brain: it stops at once and listens.
  let unwatch = null, interrupted = false;
  if (speaker && V.talk && V.cfg.barge_in !== false) {
    watchBargeIn(() => { interrupted = true; interrupt(speaker, ctl); }).then((stop) => {
      if (interrupted || speaker.cancelled) stop(); else unwatch = stop;
    });
  }
  try {
    const r = await sendChat(null, { text, spoken: true, signal: ctl.signal, onText: speaker ? (t) => speaker.feed(t) : null });
    if (speaker && !interrupted) {
      if (r?.error && !speaker.queue.length && !speaker.running) speaker.feed(r.reply);
      speaker.end();
      await speaker.done;
    }
    return interrupted ? { ...r, interrupted: true } : r;
  } finally { unwatch?.(); }
}

// You talked over the Brain: stop the voice and the answer, and keep only what you heard in the chat.
function interrupt(speaker, ctl) {
  const heard = speaker.heard.join(" ");
  speaker.cancel();
  ctl.abort();
  showBar("Listening…");
  api("/api/chat/interrupted", { method: "POST", body: { text: heard, agent: $("#agent-pick")?.value || "" } }).catch(() => {});
}

const wantsSpeech = () => $("#speak-toggle").classList.contains("on") && V.cfg.tts !== "off";

async function micOnce() {
  if (V.busy) { stopListening(); stopSpeaking(); return; }
  V.busy = true; V.stopFlag = false;
  try {
    const text = await listenOnce();
    await handleSpoken(text, { speakReply: wantsSpeech() });
  } catch (e) { note("#v-note", e.message, true); addMsg("assistant", `⚠ ${e.message}`); }
  finally { V.busy = false; if (!V.talk && !V.wake) showBar(""); }
}

async function talkLoop() {
  let empty = 0;
  while (V.talk) {
    V.stopFlag = false; V.busy = true;
    try {
      while (V.saying) await new Promise((r) => setTimeout(r, 200));   // don't listen to our own voice
      const text = await listenOnce();
      if (!V.talk) break;
      if (V.saying) continue;
      if (!text) { if (++empty >= 3) { setTalk(false); addMsg("assistant", "I stopped listening because it was quiet. Click 🎙 Talk to continue."); } continue; }
      empty = 0;
      if (/^(goodbye|bye|that's all|stop listening|band karo)[.!]?$/i.test(text)) { setTalk(false); await speak("Okay, talk to you later."); break; }
      const r = await handleSpoken(text, { speakReply: true });
      if (r?.action === "rest" || r?.action === "resting") { setTalk(false); if (!V.wake && window.BRAND?.features?.voice !== false) setWake(true); }
    } catch (e) { addMsg("assistant", `⚠ ${e.message}`); setTalk(false); }
    finally { V.busy = false; }
  }
  if (!V.wake) showBar("");
}

function setTalk(on) {
  V.talk = on;
  $("#talk-toggle").classList.toggle("on", on);
  $("#band-talk")?.classList.toggle("on", on);
  if (on) {
    setWake(false);
    if (V.cfg.tts !== "browser" && V.cfg.tts !== "off") api("/api/voice/warm", { method: "POST" }).catch(() => {});
    talkLoop();
  } else { stopListening(); stopSpeaking(); releaseMic(); showBar(""); }
}

// Wake word: browser recognition runs continuously and reacts only to the wake phrase.
async function wakeLoop() {
  if (!SR) { addMsg("assistant", "⚠ The wake word needs Chrome or Edge."); setWake(false); return; }
  while (V.wake) {
    const words = wakeWords();
    showBar(`Waiting for "${words[0]}"…`);
    let heard = null;
    try {
      await listenBrowser({ continuous: true, onPartial: (t) => {
        const low = norm(t);
        const w = words.find((x) => low.includes(x));
        if (w) { heard = t.slice(Math.max(0, t.toLowerCase().indexOf(w.split(" ")[0]))); return "done"; }
      } });
    } catch (e) { addMsg("assistant", `⚠ ${e.message}`); setWake(false); break; }
    if (!V.wake || heard === null) continue;
    V.busy = true;
    try {
      // Send the wake phrase itself: the server wakes the Brain if it was resting, and answers anything after it.
      let r = await handleSpoken(heard, { speakReply: true });
      if (r?.action === "wake" && !norm(heard).split(" ").slice(words[0].split(" ").length).length) {
        const more = await listenOnce();
        if (more) r = await handleSpoken(more, { speakReply: true });
      }
    } catch (e) { addMsg("assistant", `⚠ ${e.message}`); }
    finally { V.busy = false; }
  }
}

function setWake(on) {
  V.wake = on;
  $("#wake-toggle").classList.toggle("on", on);
  if (on) { if (V.talk) setTalk(false); wakeLoop(); } else { try { V.rec?.abort(); } catch {} if (!V.talk) showBar(""); }
}

// ---------- Controls ----------

$("#mic").addEventListener("click", micOnce);
document.addEventListener("click", (e) => { if (e.target.closest("#hero-talk")) setTalk(true); });
$("#talk-toggle").addEventListener("click", () => setTalk(!V.talk));
$("#band-talk").addEventListener("click", () => { if (typeof openTab === "function") openTab("chat"); setTalk(!V.talk); });
$("#wake-toggle").addEventListener("click", () => setWake(!V.wake));
$("#speak-toggle").addEventListener("click", () => {
  const on = !$("#speak-toggle").classList.contains("on");
  $("#speak-toggle").classList.toggle("on", on);
  if (!on) stopSpeaking();
});
function stopAll() { stopSpeaking(); stopListening(); setTalk(false); setWake(false); showBar(""); }
$("#voice-stop").addEventListener("click", stopAll);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") stopAll(); });

// ---------- Voice settings ----------

function fillBrowserVoices() {
  const sel = $("#v-bvoice"), current = V.cfg.browser_voice || "";
  const voices = "speechSynthesis" in window ? speechSynthesis.getVoices() : [];
  const natural = (v) => /Natural|Online|Neural|Google/i.test(v.name);
  const nice = voices.filter((v) => /^(en|hi)/.test(v.lang)).sort((a, b) => (natural(b) - natural(a))
    || (b.lang.endsWith("IN") - a.lang.endsWith("IN")) || a.name.localeCompare(b.name));
  sel.replaceChildren(el("option", { value: "" }, "Automatic (most natural voice available)"),
    ...nice.map((v) => el("option", { value: v.name }, `${v.name} (${v.lang})${natural(v) ? "" : " · robotic"}`)));
  // The browser's voice list can arrive late: keep the saved choice so saving now doesn't clear it.
  if (current && !nice.some((v) => v.name === current)) sel.append(el("option", { value: current }, current));
  sel.value = current;
}
if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = fillBrowserVoices;

function renderVoiceSettings() {
  const c = V.cfg;
  $("#v-stt").value = c.stt; $("#v-tts").value = c.tts; $("#v-lang").value = c.language;
  $("#v-rate").value = c.rate; $("#v-wmodel").value = c.whisper_model;
  $("#v-barge").checked = c.barge_in !== false;
  $("#v-pvoice").value = c.piper_voice;
  const ev = V.caps.edge_voices || {};
  $("#v-evoice").replaceChildren(...Object.entries(ev).map(([id, label]) => el("option", { value: id }, label)));
  $("#v-evoice").value = c.edge_voice || "en-IN-PrabhatNeural";
  $("#v-pvoices").replaceChildren(...(V.caps.piper_voices || []).map((v) => el("option", { value: v })));
  fillBrowserVoices();
  const bits = [
    V.caps.local_whisper_installed ? "Local Whisper installed" : "Local Whisper not installed",
    V.caps.piper_installed ? (V.caps.piper_voice_ready ? `Piper ready (${c.piper_voice})` : "Piper installed, voice not downloaded") : "Piper not installed",
    SR ? "Browser listening available" : "Browser listening unavailable (use Chrome/Edge)",
  ];
  $("#v-install-whisper").hidden = !!V.caps.local_whisper_installed;
  $("#v-install-piper").hidden = !!V.caps.piper_installed;
  $("#v-download").hidden = !V.caps.piper_installed;
  note("#v-note", bits.join(" · "));
  V.formReady = true;     // only now do the form fields hold the saved values
}

function pick(sel, saved) {          // a list with no options (not loaded yet) keeps the saved value
  const box = $(sel);
  return box && box.options && box.options.length && box.value ? box.value : saved;
}

function readVoiceSettings() {
  if (!V.formReady) return { ...V.cfg };   // form not filled yet: never save blank or default values
  return { ...V.cfg, stt: $("#v-stt").value, tts: $("#v-tts").value, language: $("#v-lang").value,
    rate: Number($("#v-rate").value),
    whisper_model: $("#v-wmodel").value, edge_voice: pick("#v-evoice", V.cfg.edge_voice), piper_voice: $("#v-pvoice").value.trim() || "en_US-lessac-medium",
    browser_voice: $("#v-bvoice").value, barge_in: $("#v-barge").checked, speak_replies: V.cfg.speak_replies !== false };
}

const _origSave = saveSettings;
saveSettings = async function (quiet = false) {   // eslint-disable-line no-global-assign
  if (S) { S.voice = readVoiceSettings(); V.cfg = S.voice; }
  return _origSave(quiet);
};
const _origLoad = loaders.settings;
loaders.settings = async () => { await _origLoad(); await loadVoice(); renderVoiceSettings(); };

async function voiceAction(btn, msg, fn) {
  btn.disabled = true; note("#v-note", msg);
  try { await fn(); await loadVoice(); renderVoiceSettings(); } catch (e) { note("#v-note", e.message, true); }
  finally { btn.disabled = false; }
}
$("#v-install-whisper").addEventListener("click", (e) => voiceAction(e.target, "Installing local Whisper… this can take a few minutes.",
  () => api("/api/voice/install", { method: "POST", body: { component: "whisper" } })));
$("#v-install-piper").addEventListener("click", (e) => voiceAction(e.target, "Installing Piper… this can take a few minutes.",
  () => api("/api/voice/install", { method: "POST", body: { component: "piper" } })));
$("#v-download").addEventListener("click", (e) => voiceAction(e.target, "Downloading the voice (about 60 MB)…", async () => {
  await saveSettings(true);
  await api("/api/voice/download", { method: "POST", body: { name: $("#v-pvoice").value.trim() } });
}));
$("#v-test").addEventListener("click", async () => {
  await saveSettings(true);
  await speak(`Hey ${window.OWNER_FIRST && window.OWNER_FIRST !== "there" ? window.OWNER_FIRST : "there"}! This is how I sound now. Natural enough?`);
});

window.voiceReload = loadVoice;
// Device tasks speak their questions and results (only if speaking is on).
window.brainSay = async (text) => {
  if (!text || !wantsSpeech()) return;
  V.saying = true;
  stopListening();
  try { await speak(text); } finally { V.saying = false; }
};
loadVoice();
