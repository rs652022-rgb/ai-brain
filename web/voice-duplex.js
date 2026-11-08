// Natural turn-taking for voice: the Brain lets you finish, and stops the moment you talk over it.
// Loaded before voice.js, which uses these helpers.

const DUPLEX = {
  stream: null,          // the microphone, kept open during a conversation so each turn starts at once
  carry: "",             // words you said while interrupting, carried into your next turn
  // Words people say when they haven't finished the thought: wait longer before answering.
  CARRY_ON: /\b(and|but|so|or|because|like|um+|uh+|hmm+|the|a|an|to|of|with|if|then|that|which|when|aur|ki|ke|ka|ko|se|toh|to|matlab|lekin|par|ya|jaise|kyunki|phir|ek|woh|wo|yeh|ye|jo|agar|मतलब|और|कि|तो|लेकिन|या|क्योंकि|फिर|अगर)\s*$/i,
  END_PAUSE: 1300,       // ms of quiet after a finished sentence
  SHORT_PAUSE: 1800,     // after one or two words ("yes", "wait"), more often follows
  MID_PAUSE: 2600,       // after "and", "matlab", "toh"...
  BARGE_MS: 280,         // how long your voice must last before the Brain stops (a cough or click doesn't)
  TICK_MS: 30,           // timers, not animation frames, so it also works when the window is behind others
};

// How much longer or shorter than normal to wait (Settings → Voice → "Wait before answering").
function patience() {
  return { quick: 0.7, normal: 1, relaxed: 1.5 }[(typeof V !== "undefined" && V.cfg.patience) || "normal"] || 1;
}

// How long to wait after you go quiet: short after a complete thought, longer mid-sentence.
function turnEndDelay(text) {
  const t = String(text || "").trim();
  let ms = DUPLEX.END_PAUSE;
  if (!t || DUPLEX.CARRY_ON.test(t) || /[,–-]$/.test(t)) ms = DUPLEX.MID_PAUSE;
  else if (t.split(/\s+/).length <= 2) ms = DUPLEX.SHORT_PAUSE;
  return Math.round(ms * patience());
}

async function getMic() {
  if (DUPLEX.stream && DUPLEX.stream.getAudioTracks().some((t) => t.readyState === "live")) return DUPLEX.stream;
  try {
    DUPLEX.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch {
    throw new Error("Microphone access was blocked. Allow it in the browser's address bar.");
  }
  return DUPLEX.stream;
}

function releaseMic() {
  DUPLEX.stream?.getTracks().forEach((t) => t.stop());
  DUPLEX.stream = null;
}

// Loudness of the microphone, for voice detection. close() frees the audio graph.
function micMeter(stream) {
  const ctx = new AudioContext();
  ctx.resume().catch(() => {});                    // a context made outside a click can start paused
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  return {
    level() {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      return Math.sqrt(sum / buf.length);
    },
    close() { ctx.close().catch(() => {}); },
  };
}

// Tells a real voice from the room's background. The background level is learned all the time from the
// quiet moments, so it also covers the Brain's own voice leaking into the microphone.
function voiceDetector(meter, { factor = 3, min = 0.012 } = {}) {
  let floor = 0, samples = 0;
  return {
    loud() {
      const lvl = meter.level();
      if (samples < 10) { floor = (floor * samples + lvl) / (samples + 1); samples++; return false; }
      const threshold = Math.max(min, floor * factor);
      if (lvl < threshold) floor = floor * 0.97 + lvl * 0.03;   // follow the room while it's quiet
      return lvl >= threshold;
    },
  };
}

const _words = (t) => String(t || "").toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);

// Words in what the microphone heard that the Brain is not saying itself (its own voice can leak in).
function newWords(heard, saying) {
  const own = new Set(_words(saying));
  return _words(heard).filter((w) => !own.has(w));
}

// While the Brain thinks or speaks, watch for you talking over it. Two signals, whichever comes first:
// - your voice is clearly louder than the background for a moment, or
// - the speech recogniser hears words the Brain isn't saying (also kept, so your next turn starts with them).
// Returns a function that stops watching; it resolves once the microphone is free again.
async function watchBargeIn(onBarge, { saying = () => "", lang = "en-IN", words = true } = {}) {
  let stopped = false, meter = null, timer = null, rec = null, loudMs = 0, last = performance.now();
  try { meter = micMeter(await getMic()); } catch { return async () => {}; }
  const detect = voiceDetector(meter);
  let recEnded = Promise.resolve();
  const fire = () => { if (stopped) return; stop(); onBarge(); };

  timer = setInterval(() => {
    const now = performance.now(), dt = now - last; last = now;
    if (detect.loud()) loudMs += dt;
    else loudMs = Math.max(0, loudMs - dt * 0.6);   // short gaps between syllables don't reset it
    if (loudMs >= DUPLEX.BARGE_MS) fire();
  }, DUPLEX.TICK_MS);

  if (words && typeof SR !== "undefined" && SR) {
    try {
      rec = new SR();
      rec.lang = lang; rec.interimResults = true; rec.continuous = true; rec.maxAlternatives = 1;
      let resolveEnd; recEnded = new Promise((r) => (resolveEnd = r));
      rec.onresult = (ev) => {
        let text = "";
        for (let i = 0; i < ev.results.length; i++) text += ev.results[i][0].transcript + " ";
        const fresh = newWords(text, saying());
        // Hindi spoken by the Brain comes back in Roman letters, so ask for more words then.
        const need = /[ऀ-ॿ]/.test(saying()) ? 3 : 2;
        if (fresh.length >= need || (fresh.length && fresh.join("").length >= 6)) {
          DUPLEX.carry = text.trim();
          fire();
        }
      };
      rec.onerror = () => {};
      rec.onend = () => { resolveEnd(); if (!stopped) { try { rec.start(); } catch { /* the level check still runs */ } } };
      rec.start();
    } catch { rec = null; recEnded = Promise.resolve(); }
  }

  function stop() {
    if (stopped) return recEnded;
    stopped = true;
    clearInterval(timer);
    meter.close();
    if (rec) { try { rec.abort(); } catch { /* already stopped */ } }
    // Only one recogniser can listen at a time: wait (briefly) until this one has let go.
    return Promise.race([recEnded, new Promise((r) => setTimeout(r, 400))]);
  }
  return stop;
}

// Browser listening for one turn. It keeps listening through pauses and ends your turn only when you've
// really finished: it waits longer after "and", "matlab", "toh", and never while the microphone still
// hears your voice. If the browser stops listening by itself, it starts again.
function listenBrowserTurn(lang, onText, prefix = "") {
  return new Promise((resolve, reject) => {
    let finalText = prefix ? prefix + " " : "", interim = "", heardAny = !!prefix, done = false;
    let lastVoice = performance.now(), rec = null, meter = null, detect = null;
    const started = performance.now();
    const text = () => (finalText + interim).trim();

    getMic().then((s) => { if (!done) { meter = micMeter(s); detect = voiceDetector(meter, { factor: 2.5 }); } }).catch(() => {});

    const check = setInterval(() => {
      const now = performance.now();
      if (detect?.loud()) lastVoice = now;
      if (V.stopFlag) return finish();
      if (!heardAny) { if (now - started > 8000) finish(); return; }
      if (now - started > 90000) return finish();          // one turn is at most a minute and a half
      if (now - lastVoice >= turnEndDelay(text()) + (interim ? 300 : 0)) finish();
    }, 100);

    function finish() {
      if (done) return;
      done = true;
      clearInterval(check); meter?.close();
      try { rec?.stop(); } catch { /* already stopped */ }
      V.rec = null;
      resolve(text());
    }

    function start() {
      rec = new SR();
      V.rec = rec;
      rec.lang = lang; rec.interimResults = true; rec.continuous = true; rec.maxAlternatives = 1;
      rec.onresult = (ev) => {
        interim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          const t = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) finalText += t + " "; else interim += t;
        }
        heardAny = true; lastVoice = performance.now();
        onText?.(text());
      };
      rec.onerror = (ev) => {
        if (ev.error === "no-speech" || ev.error === "aborted" || ev.error === "network") return;
        done = true; clearInterval(check); meter?.close(); V.rec = null;
        reject(new Error(ev.error === "not-allowed" ? "Microphone access was blocked. Allow it in the browser's address bar." : `Listening error: ${ev.error}`));
      };
      rec.onend = () => {
        if (done) return;
        if (V.stopFlag) return finish();
        // The browser stopped on its own (it does after a pause): keep what we have and listen on.
        if (interim) { finalText += interim + " "; interim = ""; }
        try { start(); } catch { finish(); }
      };
      rec.start();
    }
    if (prefix) onText?.(text());
    start();
  });
}
