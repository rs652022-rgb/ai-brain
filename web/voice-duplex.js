// Natural turn-taking for voice: the Brain lets you finish, and stops the moment you talk over it.
// Loaded before voice.js, which uses these helpers.

const DUPLEX = {
  stream: null,          // the microphone, kept open during a conversation so each turn starts at once
  // Words people say when they haven't finished the thought: wait longer before answering.
  CARRY_ON: /\b(and|but|so|or|because|like|um+|uh+|hmm+|the|a|to|of|with|if|then|aur|ki|ke|ka|toh|to|matlab|lekin|par|ya|jaise|kyunki|phir|ek|woh|wo|yeh|ye|मतलब|और|कि|तो|लेकिन|या|क्योंकि|फिर)\s*$/i,
  END_PAUSE: 700,        // ms of quiet after a finished sentence
  MID_PAUSE: 1700,       // ms of quiet after "and", "matlab", "toh"...
  BARGE_MS: 260,         // how long you must keep talking over the voice before it stops
};

// How long to wait after you go quiet: short after a complete thought, longer mid-sentence.
function turnEndDelay(text) {
  const t = String(text || "").trim();
  if (!t) return DUPLEX.MID_PAUSE;
  if (DUPLEX.CARRY_ON.test(t) || /[,–-]$/.test(t)) return DUPLEX.MID_PAUSE;
  return t.split(/\s+/).length <= 2 ? 1000 : DUPLEX.END_PAUSE;   // "yes" / "wait" may be followed by more
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

// While the Brain speaks, listen for you talking over it. The first moments set the level of the room
// and of the Brain's own voice leaking into the mic, so only a real voice above that interrupts.
// Returns a function that stops watching.
async function watchBargeIn(onBarge) {
  let stopped = false, meter = null;
  try { meter = micMeter(await getMic()); } catch { return () => {}; }
  const started = performance.now();
  let floor = 0, samples = 0, loudSince = 0;
  const tick = () => {
    if (stopped) return;
    const lvl = meter.level(), now = performance.now();
    if (now - started < 400) {                       // learn the background level first
      floor = (floor * samples + lvl) / (samples + 1); samples++;
    } else {
      const threshold = Math.max(0.04, floor * 3.2);
      if (lvl > threshold) {
        loudSince = loudSince || now;
        if (now - loudSince >= DUPLEX.BARGE_MS) { stop(); onBarge(); return; }
      } else if (loudSince && now - loudSince > 120) {
        loudSince = 0;                               // a cough or a click: ignore it
      }
      floor = floor * 0.995 + Math.min(lvl, floor * 2 || lvl) * 0.005;   // follow slow changes in the room
    }
    requestAnimationFrame(tick);
  };
  const stop = () => { if (!stopped) { stopped = true; meter.close(); } };
  tick();
  return stop;
}

// Browser listening for one turn: keeps listening through short pauses and ends your turn only when
// you've really finished (a longer wait after "and", "matlab", "toh").
function listenBrowserTurn(lang, onText) {
  return new Promise((resolve, reject) => {
    const rec = new SR();
    V.rec = rec;
    rec.lang = lang; rec.interimResults = true; rec.continuous = true; rec.maxAlternatives = 1;
    let finalText = "", interim = "", timer = null, heardAny = false;
    const giveUp = setTimeout(() => { if (!heardAny) rec.stop(); }, 8000);
    const schedule = () => {
      clearTimeout(timer);
      const text = (finalText + interim).trim();
      timer = setTimeout(() => rec.stop(), interim ? turnEndDelay(text) + 400 : turnEndDelay(text));
    };
    rec.onresult = (ev) => {
      interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t + " "; else interim += t;
      }
      heardAny = true;
      onText?.((finalText + interim).trim());
      schedule();
    };
    rec.onerror = (ev) => {
      if (ev.error === "no-speech" || ev.error === "aborted") return;
      reject(new Error(ev.error === "not-allowed" ? "Microphone access was blocked. Allow it in the browser's address bar." : `Listening error: ${ev.error}`));
    };
    rec.onend = () => {
      clearTimeout(timer); clearTimeout(giveUp); V.rec = null;
      resolve((finalText + interim).trim());
    };
    rec.start();
  });
}
