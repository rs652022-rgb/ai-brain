const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
let mode = "mirror";

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && data.locked) showLock();
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  return data;
}

// ---------- Branding from Master Control ----------
window.BRAND = { brain_name: "Brain", owner_first: "there", greeting: "Hey there. What's on your mind?",
  subtitle: "", suggestions: [], wake_words: ["hey brain"], sleep_words: [], stop_words: [], features: {}, resting: false };

function applyBrand(b) {
  window.BRAND = b;
  window.OWNER_FIRST = b.owner_first;
  $("#brand-name").textContent = b.brain_name;
  document.title = b.brain_name;
  document.documentElement.dataset.theme = b.theme || "blueprint";
  document.documentElement.style.setProperty("--signal", b.accent || "#ffd84a");
  const voiceOn = b.features?.voice !== false;
  ["#band-talk", "#talk-toggle", "#wake-toggle", "#speak-toggle", "#mic"].forEach((id) => { const e = $(id); if (e) e.hidden = !voiceOn; });
  if ($("#messages .empty")) emptyChat();
  showSheet(currentTab);
}

async function loadBrand(first = false) {
  try {
    const b = await api("/api/master/public");
    if (b.lock_app && !b.unlocked) { applyBrand(b); showLock(); return false; }
    applyBrand(b);
    if (first) setMode(b.default_mode || "mirror");
    return true;
  } catch { return true; }
}

function showLock() {
  $("#lock").hidden = false;
  $("#lock-title").textContent = `${window.BRAND.brain_name} is locked.`;
  setTimeout(() => $("#lock-pin").focus(), 50);
}
$("#lock-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/master/unlock", { method: "POST", body: { pin: $("#lock-pin").value } });
    $("#lock-pin").value = ""; $("#lock").hidden = true;
    await loadBrand(true); loadStatus(); loadChat();
  } catch (err) { note("#lock-note", err.message, true); $("#lock-pin").select(); }
});

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  const wrap = /\bmeta\b/.test(attrs.class || ""); // meta rows are flex: give each text its own item so gaps show
  for (const k of kids.flat()) {
    if (k == null || k === "") continue;
    e.append(k.nodeType ? k : wrap ? Object.assign(document.createElement("span"), { textContent: k }) : document.createTextNode(k));
  }
  return e;
}

function note(id, text, isErr = false) {
  const n = $(id); n.textContent = text; n.classList.toggle("err", isErr);
  if (!isErr) setTimeout(() => { if (n.textContent === text) n.textContent = ""; }, 5000);
}

const fmtDate = (s) => (s || "").slice(0, 10);

// ---------- Tabs: each one is a numbered sheet ----------
const loaders = {};
const SHEETS = {
  chat: ["Chat", ""],
  learn: ["Learn", "Teach it a topic, a link or a document. It learns only what you ask."],
  journal: ["Journal", "A few minutes a day: what happened, who you met, how you felt."],
  interview: ["Interview", "Questions about your life that teach it who you are."],
  memories: ["Memories", "Everything it remembers about you. Edit, lock or forget any of it."],
  identity: ["Identity", "Your values, style and decision rules. This is who it is."],
  import: ["Import", "Bring in your own messages from WhatsApp."],
  activity: ["Activity", "Every action, in order. This log can't be edited."],
  settings: ["Settings", "AI models, web search and voice."],
  master: ["Master Control", "Name, commands, switches, look and security. Only you change these."],
};
let currentTab = "chat";
function showSheet(tab) {
  const keys = Object.keys(SHEETS), n = String(keys.indexOf(tab)).padStart(2, "0");
  const [title, desc] = SHEETS[tab] || [tab, ""];
  $("#rail-num").textContent = n; $("#rail-name").textContent = title;
  const head = $("#sheet-head");
  head.hidden = tab === "chat";
  head.replaceChildren(...[el("div", {}, el("span", { class: "code" }, `Sheet ${n}`), el("h1", {}, title)), desc ? el("p", {}, desc) : null].filter(Boolean));
}
function openTab(tab) {
  currentTab = tab;
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${tab}`));
  showSheet(tab);
  loaders[tab]?.();
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));

// ---------- Status ----------
async function loadStatus() {
  try {
    const s = await api("/api/status");
    const ready = s.chain.filter((p) => p.ready);
    const main = ready[0]
      ? `<span class="ok">${ready[0].label.split(" (")[0]}${ready.length > 1 ? ` + ${ready.length - 1} backup` : ""}</span>`
      : `<span class="off">Add an AI key in Settings</span>`;
    const rest = s.resting ? `<span class="resting">Resting</span>` : "";
    $("#status").innerHTML = `${rest}${main}<span class="count">${s.memory.total} memories</span><span class="count">${s.knowledge.sources} learned</span>`;
    if (s.resting !== window.BRAND.resting) { window.BRAND.resting = s.resting; if (currentTab === "master") renderMasterState(); }
    const b = $("#pending-badge");
    b.hidden = !s.memory.pending; b.textContent = s.memory.pending;
  } catch { $("#status").textContent = "Server not reachable"; }
}

// ---------- Chat ----------
function setMode(m) {
  mode = m;
  $$("#mode button").forEach((x) => x.classList.toggle("on", x.dataset.mode === m));
}
$$("#mode button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

function addMsg(role, text, extra) {
  const box = $("#messages");
  box.querySelector(".empty")?.remove();
  const m = el("div", { class: `msg ${role}` }, text);
  if (extra) m.append(extra);
  box.append(m);
  box.scrollTop = box.scrollHeight;
  return m;
}

function emptyChat() {
  const b = window.BRAND;
  const ask = (text) => el("button", { type: "button", onclick: () => sendChat(null, { text }) }, text);
  const g = b.greeting || "";
  const last = /[.?!]$/.test(g) ? g.slice(-1) : "";
  const hello = el("h1", { class: "hello" }, last ? g.slice(0, -1) : g);
  if (last) hello.append(el("span", { class: "dot" }, last));
  const voiceOn = b.features?.voice !== false;
  const resting = b.resting ? `${b.brain_name} is resting. Say "${b.wake_words?.[0] || "hey brain"}" to wake it.` : null;
  $("#messages").replaceChildren(el("div", { class: "empty" },
    el("span", { class: "code" }, resting ? "Resting" : `${b.brain_name} is ready`),
    hello,
    el("p", {}, resting || b.subtitle || ""),
    voiceOn ? el("div", { class: "talk-row" },
      el("button", { type: "button", class: "orb", id: "hero-talk" }, svgMic(), "Tap to talk"),
      el("span", { class: "orb-label" }, `or say "${b.wake_words?.[0] || "hey brain"}" with Wake word on`)) : null,
    (b.suggestions || []).length ? el("div", { class: "suggest" }, ...b.suggestions.map(ask)) : null));
  $("#messages").scrollTop = 0;
}

function svgMic() {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.8"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("aria-hidden", "true");
  s.innerHTML = '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>';
  return s;
}

async function loadChat() {
  const hist = await api("/api/chat/history");
  if (!hist.length) return emptyChat();
  $("#messages").replaceChildren();
  hist.forEach((m) => addMsg(m.role, m.content));
}

function forgetPicker(f) {
  const rows = [
    ...f.memories.map((m) => ({ kind: "memory", id: m.id, label: m.content })),
    ...f.knowledge.map((k) => ({ kind: "source", id: k.source_id, label: `Learned: ${k.title}` })),
  ];
  const box = el("div", { class: "forget" });
  const checks = rows.map((r) => {
    const c = el("input", { type: "checkbox" }); c.checked = true;
    box.append(el("label", { class: "forget-row" }, c, el("span", {}, r.label)));
    return [c, r];
  });
  const chat = el("input", { type: "checkbox" }); chat.checked = true;
  box.append(el("label", { class: "forget-row" }, chat, el("span", {}, "Also clear this conversation")));
  const go = el("button", { class: "mini", onclick: async () => {
    const pick = checks.filter(([c]) => c.checked).map(([, r]) => r);
    go.disabled = true;
    const res = await api("/api/forget", { method: "POST", body: {
      memory_ids: pick.filter((r) => r.kind === "memory").map((r) => r.id),
      source_ids: pick.filter((r) => r.kind === "source").map((r) => r.id), clear_chat: chat.checked } });
    if (res.chat_cleared) { emptyChat(); addMsg("assistant", `Forgotten: ${res.memories} memories and ${res.knowledge} learned sources. Conversation cleared.`); }
    else go.textContent = `Forgotten: ${res.memories} memories, ${res.knowledge} sources`;
    loadStatus();
  } }, rows.length ? "Forget selected" : "Clear this conversation");
  box.append(go);
  return box;
}

function replyExtras(r) {
  const extra = el("div", {});
  if (r.forget) extra.append(forgetPicker(r.forget));
  if (r.web_sources?.length) {
    const box = el("div", { class: "sources" }, el("strong", {}, r.action === "chat" ? "Web sources (not saved):" : "Sources learned:"),
      ...r.web_sources.map((s, i) => el("a", { href: s.url, target: "_blank", rel: "noopener" }, `[${i + 1}] ${s.title || s.url}`)));
    if (r.action === "chat") {
      const save = el("button", { class: "mini", onclick: async () => {
        save.disabled = true; save.textContent = "Saving…";
        const res = await api("/api/learn/save-sources", { method: "POST", body: { urls: r.web_sources.map((s) => s.url) } });
        save.textContent = `Saved ${res.saved.length} page(s) to knowledge`;
      } }, "Save to knowledge");
      box.append(el("div", {}, save));
    }
    extra.append(box);
  }
  const recalled = [...(r.memories_used || []).map((m) => `#${m.id} ${m.content.slice(0, 140)}`),
                    ...(r.knowledge_used || []).map((k) => `Learned: ${k.title}`)];
  if (recalled.length) {
    extra.append(el("details", {}, el("summary", {}, `${recalled.length} memories/knowledge recalled · ${r.model}`),
      ...recalled.map((t) => el("div", {}, t))));
  } else if (r.model && r.model !== "none") {
    extra.append(el("div", { class: "meta" }, r.model));
  }
  return extra;
}

// Sends a message and shows the reply word by word as it is written.
// opts.onText(piece) receives each new piece (used to start speaking after the first sentence).
async function sendChat(e, opts = {}) {
  e?.preventDefault?.();
  const input = $("#chat-input");
  const text = (opts.text ?? input.value).trim();
  if (!text) return null;
  input.value = "";
  addMsg("user", opts.spoken ? `🎤 ${text}` : text);
  const webOn = webNext;
  setWebNext(false);
  const learning = /^\/(learn|research|read|topic)\b|^(please\s+)?(learn|research)\b|^every\s+(day|week|month)\b/i.test(text);
  const bubble = addMsg("assistant thinking",
    learning ? "Learning… reading the web can take up to a minute." : webOn ? "Searching the web…" : "Thinking…");
  $("#send").disabled = true;
  let reply = "", result = null;
  try {
    const res = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, mode, use_sealed: $("#use-sealed").checked, web: webOn, voice: !!opts.spoken }) });
    if (!res.ok || !res.body) throw new Error((await res.json().catch(() => ({}))).detail || `Error ${res.status}`);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "text") {
          if (!reply) { bubble.classList.remove("thinking"); bubble.textContent = ""; }
          reply += ev.text;
          bubble.textContent = reply;
          $("#messages").scrollTop = $("#messages").scrollHeight;
          opts.onText?.(ev.text);
        } else if (ev.type === "status") {
          if (!reply) bubble.textContent = ev.text;
        } else if (ev.type === "final") {
          result = ev.result;
        } else if (ev.type === "error") {
          throw new Error(ev.error);
        }
      }
    }
    if (!result) throw new Error("The reply was cut off. Please try again.");
    bubble.classList.remove("thinking");
    bubble.textContent = result.reply;
    bubble.append(replyExtras(result));
    if (result.action === "mode" && result.new_mode) setMode(result.new_mode);
    if ("resting" in result) { window.BRAND.resting = result.resting; loadStatus(); }
    setTimeout(loadStatus, 4000); // pick up proposed memories
    return result;
  } catch (err) {
    bubble.classList.remove("thinking");
    const [main, details] = String(err.message).split("\n\nDetails: ");
    bubble.textContent = reply ? `${reply}\n\n${main}` : main;
    bubble.classList.add("error");
    if (details) bubble.append(el("details", {}, el("summary", {}, "Technical details"), el("div", {}, details)));
    return { reply: reply || `Sorry, ${err.message}`, error: true };
  } finally { $("#send").disabled = false; if (!opts.spoken) input.focus(); }
}
let webNext = false;
function setWebNext(on) { webNext = on; $("#web-toggle").classList.toggle("on", on); }
$("#web-toggle").addEventListener("click", () => setWebNext(!webNext));
$("#chat-form").addEventListener("submit", sendChat);
$("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) sendChat(e); });
$("#clear-chat").addEventListener("click", async () => {
  if (!confirm("Clear this conversation? Your memories are kept.")) return;
  await api("/api/chat/history", { method: "DELETE" }); emptyChat();
});

// ---------- Memories ----------
function memItem(m, { onChange } = {}) {
  const body = el("div", { class: "body" }, m.content);
  const actions = el("span", { class: "actions" },
    el("button", { class: "mini", onclick: async () => {
      const text = prompt("Edit memory", m.content);
      if (text == null || text.trim() === m.content) return;
      await api(`/api/memories/${m.id}`, { method: "PATCH", body: { content: text.trim() } }); onChange?.();
    } }, "Edit"),
    el("button", { class: "mini", onclick: async () => {
      await api(`/api/memories/${m.id}`, { method: "PATCH", body: { locked: !m.locked } }); onChange?.();
    } }, m.locked ? "Unlock" : "Lock"),
    el("button", { class: "mini", onclick: async () => {
      if (!confirm("Forget this memory permanently?")) return;
      await api(`/api/memories/${m.id}`, { method: "DELETE" }); onChange?.(); loadStatus();
    } }, "Forget"));
  const meta = el("div", { class: "meta" },
    `#${m.id}`, m.type, fmtDate(m.happened_on || m.created_at), m.source,
    el("span", { class: `sens-${m.sensitivity}` }, m.sensitivity), m.locked ? "🔒" : null,
    ...(m.tags || []).map((t) => el("span", { class: "tag" }, t)), actions);
  return el("div", { class: "item" }, body, m.summary ? el("div", { class: "meta" }, m.summary) : null, meta);
}

async function loadPending() {
  const list = await api("/api/pending");
  $("#pending-card").hidden = !list.length;
  $("#pending-list").replaceChildren(...list.map((p) => el("div", { class: "item" },
    el("div", { class: "body" }, p.content),
    el("div", { class: "meta" }, p.type, p.reason ? `· ${p.reason}` : "", `· from ${p.source}`,
      el("span", { class: "actions" },
        el("button", { class: "mini primary", onclick: async () => {
          await api(`/api/pending/${p.id}/approve`, { method: "POST", body: {} }); loadMemories(); loadStatus();
        } }, "Approve"),
        el("button", { class: "mini", onclick: async () => {
          await api(`/api/pending/${p.id}/reject`, { method: "POST" }); loadMemories(); loadStatus();
        } }, "Reject"))))));
}

async function loadMemories() {
  loadPending();
  const q = encodeURIComponent($("#mem-q").value.trim());
  const t = $("#mem-filter").value;
  const list = await api(`/api/memories?q=${q}&type=${t}`);
  $("#mem-list").replaceChildren(...(list.length ? list.map((m) => memItem(m, { onChange: loadMemories }))
    : [el("p", { class: "hint" }, "No memories yet. Add one above, write a journal entry, or answer an interview question.")]));
}
loaders.memories = loadMemories;
let qTimer; $("#mem-q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(loadMemories, 300); });
$("#mem-filter").addEventListener("change", loadMemories);
$("#mem-add").addEventListener("click", async () => {
  const content = $("#mem-text").value.trim(); if (!content) return;
  $("#mem-add").disabled = true;
  try {
    await api("/api/memories", { method: "POST", body: { content, type: $("#mem-type").value, sensitivity: $("#mem-sens").value } });
    $("#mem-text").value = ""; loadMemories(); loadStatus();
  } finally { $("#mem-add").disabled = false; }
});

// ---------- Journal ----------
$("#journal-date").value = new Date().toISOString().slice(0, 10);
async function loadJournal() {
  const list = await api("/api/journal");
  $("#journal-list").replaceChildren(...(list.length ? list.map((m) => memItem(m, { onChange: loadJournal }))
    : [el("p", { class: "hint" }, "No entries yet. A short entry every day adds up fast.")]));
}
loaders.journal = loadJournal;
$("#journal-save").addEventListener("click", async () => {
  const text = $("#journal-text").value.trim(); if (!text) return;
  $("#journal-save").disabled = true; note("#journal-note", "Saving…");
  try {
    await api("/api/journal", { method: "POST", body: { text, date: $("#journal-date").value, sensitivity: $("#journal-sens").value } });
    $("#journal-text").value = ""; note("#journal-note", "Saved. Any new facts the Brain noticed are waiting in Memories for your approval.");
    loadJournal(); loadStatus();
  } catch (e) { note("#journal-note", e.message, true); } finally { $("#journal-save").disabled = false; }
});

// ---------- Interview ----------
let currentQ = null;
function showInterview(data) {
  const p = data.progress; currentQ = data.next[0] || null;
  $("#iv-bar").style.width = `${(100 * p.answered) / p.total}%`;
  $("#iv-progress").textContent = `${p.answered} of ${p.total} answered`;
  if (!currentQ) {
    $("#iv-theme").textContent = "Done"; $("#iv-question").textContent = "You've answered every question. Thank you.";
    $("#iv-answer").hidden = true; $("#iv-save").hidden = true; return;
  }
  $("#iv-theme").textContent = currentQ.theme; $("#iv-question").textContent = currentQ.question;
}
loaders.interview = async () => showInterview(await api("/api/interview/next"));
$("#iv-save").addEventListener("click", async () => {
  const answer = $("#iv-answer").value.trim(); if (!answer || !currentQ) return;
  $("#iv-save").disabled = true;
  try {
    const r = await api("/api/interview/answer", { method: "POST", body: { question_id: currentQ.id, answer, sensitivity: $("#iv-sens").value } });
    $("#iv-answer").value = ""; showInterview(r); loadStatus();
  } catch (e) { alert(e.message); } finally { $("#iv-save").disabled = false; }
});

// ---------- Identity ----------
async function loadIdentity(data) {
  data = data || await api("/api/identity");
  $("#id-yaml").value = data.yaml;
  $("#id-versions").replaceChildren(el("option", { value: "" }, "Restore an earlier version…"),
    ...data.versions.map((v) => el("option", { value: v }, v.replace("identity-", "").replace(".yaml", ""))));
}
loaders.identity = () => loadIdentity();
$("#id-save").addEventListener("click", async () => {
  try { await loadIdentity(await api("/api/identity", { method: "PUT", body: { yaml: $("#id-yaml").value } })); note("#id-note", "Saved. The Brain uses this from your next message."); }
  catch (e) { note("#id-note", e.message, true); }
});
$("#id-restore").addEventListener("click", async () => {
  const v = $("#id-versions").value; if (!v || !confirm(`Restore version ${v}? Your current file is kept as a version too.`)) return;
  await loadIdentity(await api(`/api/identity/restore/${v}`, { method: "POST" })); note("#id-note", "Restored.");
});

// ---------- Import ----------
$("#wa-import").addEventListener("click", async () => {
  const f = $("#wa-file").files[0]; if (!f) return note("#wa-note", "Choose the exported .txt file first.", true);
  $("#wa-import").disabled = true; note("#wa-note", "Importing… this can take a while with the local model on.");
  try {
    const text = await f.text();
    const r = await api("/api/import/whatsapp", { method: "POST", body: { text, owner_name: $("#wa-owner").value.trim() || undefined, chat_with: $("#wa-with").value.trim() } });
    note("#wa-note", `Imported ${r.memories_created} days of your messages (${r.mine} of ${r.messages_found} messages were yours).`);
    $("#wa-samples").replaceChildren(el("h3", {}, "Writing samples you can paste into your Identity File"),
      el("textarea", { rows: 8, class: "mono", readonly: "" }, r.writing_samples.map((s) => `- ${JSON.stringify(s)}`).join("\n")));
    loadStatus();
  } catch (e) { note("#wa-note", e.message, true); } finally { $("#wa-import").disabled = false; }
});

// ---------- Activity ----------
loaders.activity = async () => {
  const list = await api("/api/audit");
  $("#audit-list").replaceChildren(...list.map((a) => el("div", { class: "item" },
    `${a.at.replace("T", " ").slice(0, 19)}  ${a.actor.padEnd(6)}  ${a.action}  ${JSON.stringify(a.detail)}`)));
};

// ---------- Settings ----------
let S = null, PRESETS = {};

function providerCard(pid, idx) {
  const pre = PRESETS[pid], cfg = (S.providers[pid] = S.providers[pid] || {});
  const needsKey = pre.needs_key !== false;
  const result = el("span", { class: "result" });
  const modelInput = el("input", { value: cfg.model || "", placeholder: pre.model || "model name", list: `models-${pid}` });
  const datalist = el("datalist", { id: `models-${pid}` });
  modelInput.addEventListener("input", () => (cfg.model = modelInput.value.trim()));

  const fields = [];
  if (needsKey) {
    const key = el("input", { type: "password", value: cfg.api_key || "", placeholder: "Paste API key", autocomplete: "off" });
    key.addEventListener("input", () => (cfg.api_key = key.value.trim()));
    fields.push(el("div", { class: "wide" }, key));
  }
  fields.push(el("div", {}, modelInput, datalist));
  const url = el("input", { value: cfg.base_url || "", placeholder: pre.base_url || "Base URL ending in /v1" });
  url.addEventListener("input", () => (cfg.base_url = url.value.trim()));
  fields.push(el("div", {}, url));

  const move = (d) => { const o = S.order; const j = idx + d; if (j < 0 || j >= o.length) return; [o[idx], o[j]] = [o[j], o[idx]]; renderSettings(); };
  const head = el("div", { class: "prov-head" },
    el("strong", {}, `${idx + 1}. ${pre.label}`),
    pre.free === true ? el("span", { class: "free" }, "FREE") : pre.free === false ? el("span", { class: "paid" }, "paid") : null,
    pre.key_url ? el("a", { href: pre.key_url, target: "_blank", rel: "noopener" }, needsKey ? "Get a key ↗" : "Download ↗") : null,
    el("span", { class: "actions" },
      el("button", { class: "mini", title: "Move up", onclick: () => move(-1) }, "↑"),
      el("button", { class: "mini", title: "Move down", onclick: () => move(1) }, "↓"),
      el("button", { class: "mini", title: "Find, test and pick the best free models for this provider", onclick: async () => {
        result.className = "result"; result.textContent = "Finding and testing free models… up to a minute.";
        try { await saveSettings(true); const r = await api(`/api/settings/discover/${pid}`, { method: "POST" });
          S = r.settings; renderSettings(); showDiscovery([r.result]);
        } catch (e) { result.className = "result bad"; result.textContent = e.message; }
      } }, "Auto-find"),
      el("button", { class: "mini", onclick: async () => {
        result.className = "result"; result.textContent = "Loading models…";
        try { await saveSettings(true); const r = await api(`/api/settings/models/${pid}`);
          datalist.replaceChildren(...r.models.map((m) => el("option", { value: m })));
          result.textContent = `${r.models.length} models loaded. Click the model box to pick one.`;
        } catch (e) { result.className = "result bad"; result.textContent = e.message; }
      } }, "Load models"),
      el("button", { class: "mini", onclick: async () => {
        result.className = "result"; result.textContent = "Testing…";
        try { await saveSettings(true); const r = await api(`/api/settings/test/${pid}`, { method: "POST" });
          result.className = `result ${r.ok ? "ok" : "bad"}`;
          result.textContent = r.ok ? `✓ Works with ${r.model}` : `✗ ${r.error}`;
        } catch (e) { result.className = "result bad"; result.textContent = e.message; }
      } }, "Test"),
      el("button", { class: "mini", onclick: () => { S.order.splice(idx, 1); renderSettings(); } }, "Remove")));
  const backups = cfg.fallbacks?.length
    ? el("div", { class: "meta" }, `Backup models (used automatically if the main one is busy): ${cfg.fallbacks.join(", ")}`) : null;
  return el("div", { class: "item prov" }, head, el("div", { class: "hint" }, pre.note), el("div", { class: "grid" }, fields), backups, result);
}

function renderSettings() {
  $("#prov-list").replaceChildren(...(S.order.length ? S.order.map(providerCard)
    : [el("p", { class: "hint" }, "No providers yet. Add Google Gemini or Groq below: both have free API keys.")]));
  const unused = Object.keys(PRESETS).filter((p) => !S.order.includes(p));
  $("#prov-add").replaceChildren(...unused.map((p) =>
    el("option", { value: p }, `${PRESETS[p].label}${PRESETS[p].free ? " (free)" : ""}`)));
  $("#prov-add-btn").disabled = !unused.length;
  $("#cloud-private").checked = !!S.cloud_private_tasks;
  $("#embed-choice").value = S.embeddings || "auto";
  $("#web-mode").value = S.web_mode || "ask";
  $("#suggest-facts").checked = S.suggest_facts !== false;
  renderSearch();
}

let SEARCH_PRESETS = {};
function renderSearch() {
  S.search = S.search || {}; S.search_order = S.search_order || ["duckduckgo"];
  $("#search-list").replaceChildren(...S.search_order.map((eid, idx) => {
    const pre = SEARCH_PRESETS[eid] || {}, cfg = (S.search[eid] = S.search[eid] || {});
    const fields = [];
    if (pre.needs_key) {
      const k = el("input", { type: "password", value: cfg.api_key || "", placeholder: "Paste API key", autocomplete: "off" });
      k.addEventListener("input", () => (cfg.api_key = k.value.trim())); fields.push(k);
    }
    if (eid === "searxng") {
      const u = el("input", { value: cfg.base_url || "", placeholder: "http://localhost:8080" });
      u.addEventListener("input", () => (cfg.base_url = u.value.trim())); fields.push(u);
    }
    const move = (d) => { const o = S.search_order, j = idx + d; if (j < 0 || j >= o.length) return; [o[idx], o[j]] = [o[j], o[idx]]; renderSearch(); };
    return el("div", { class: "item prov" },
      el("div", { class: "prov-head" }, el("strong", {}, `${idx + 1}. ${pre.label}`),
        pre.free ? el("span", { class: "free" }, "FREE") : null,
        pre.key_url ? el("a", { href: pre.key_url, target: "_blank", rel: "noopener" }, "Get a key ↗") : null,
        el("span", { class: "actions" },
          el("button", { class: "mini", onclick: () => move(-1) }, "↑"), el("button", { class: "mini", onclick: () => move(1) }, "↓"),
          el("button", { class: "mini", onclick: () => { S.search_order.splice(idx, 1); renderSearch(); } }, "Remove"))),
      el("div", { class: "hint" }, pre.note), ...fields);
  }));
  const unused = Object.keys(SEARCH_PRESETS).filter((e) => !S.search_order.includes(e));
  $("#search-add").replaceChildren(...unused.map((e) => el("option", { value: e }, SEARCH_PRESETS[e].label)));
  $("#search-add-btn").disabled = !unused.length;
}
$("#search-add-btn").addEventListener("click", () => { const e = $("#search-add").value; if (e) { S.search_order.push(e); renderSearch(); } });
$("#search-test").addEventListener("click", async () => {
  note("#search-note", "Searching…");
  try { await saveSettings(true); const r = await api("/api/settings/test-search", { method: "POST" });
    if (r.ok) note("#search-note", `✓ Web search works (${r.engine}): ${r.results.length} results, e.g. "${r.results[0].title}"`);
    else note("#search-note", r.error, true);
  } catch (e) { note("#search-note", e.message, true); }
});

function showDiscovery(results) {
  const lines = results.map((r) => {
    const name = PRESETS[r.provider]?.label || r.provider;
    if (r.ok) return `✓ ${name}: using ${r.model}${r.fallbacks?.length ? ` + ${r.fallbacks.length} backup(s)` : ""}`;
    if (r.ok === null) return `– ${name}: skipped (${r.skipped})`;
    return `✗ ${name}: ${r.error}`;
  });
  const anyOk = results.some((r) => r.ok);
  $("#discover-note").className = `note ${anyOk ? "" : "err"}`;
  $("#discover-note").style.whiteSpace = "pre-line";
  $("#discover-note").textContent = lines.join("\n") || "Add a provider with a key first.";
  loadStatus();
}

$("#discover-all").addEventListener("click", async () => {
  const btn = $("#discover-all"); btn.disabled = true;
  $("#discover-note").className = "note"; $("#discover-note").textContent = "Finding and testing free models on every provider… this can take a minute.";
  try {
    await saveSettings(true);
    const r = await api("/api/settings/discover", { method: "POST" });
    S = r.settings; renderSettings(); showDiscovery(r.results);
  } catch (e) { $("#discover-note").className = "note err"; $("#discover-note").textContent = e.message; }
  finally { btn.disabled = false; }
});

async function loadSettings() {
  const r = await api("/api/settings");
  S = r.settings; PRESETS = r.presets; SEARCH_PRESETS = r.search_presets; renderSettings();
  const st = await api("/api/status");
  $("#index-note").className = "note";
  $("#index-note").textContent = st.index.model === "none"
    ? "Search by meaning is off; keyword search is used."
    : `${st.index.indexed} of ${st.index.total} memories indexed with ${st.index.model}.`;
}
loaders.settings = loadSettings;

async function saveSettings(quiet = false) {
  S.cloud_private_tasks = $("#cloud-private").checked;
  S.embeddings = $("#embed-choice").value;
  S.web_mode = $("#web-mode").value;
  S.suggest_facts = $("#suggest-facts").checked;
  const r = await api("/api/settings", { method: "PUT", body: S });
  if (!quiet) { S = r.settings; renderSettings(); note("#settings-note", "Saved."); loadStatus(); }
}
$("#settings-save").addEventListener("click", () => saveSettings().catch((e) => note("#settings-note", e.message, true)));
$("#prov-add-btn").addEventListener("click", () => { const p = $("#prov-add").value; if (p) { S.order.push(p); renderSettings(); } });
$("#reindex").addEventListener("click", async () => {
  await saveSettings(true); note("#index-note", "Re-indexing… this can take a minute.");
  try { const r = await api("/api/memories/reindex", { method: "POST" });
    note("#index-note", `Re-indexed ${r.done} of ${r.total} memories with ${r.model}.`);
  } catch (e) { note("#index-note", e.message, true); }
});

// ---------- Learn ----------
function resultBox(target, text, sources = []) {
  $(target).replaceChildren(el("div", { class: "result-box" }, text,
    sources.length ? el("div", { class: "sources" }, el("strong", {}, "Sources:"),
      ...sources.map((s, i) => el("a", { href: s.url, target: "_blank", rel: "noopener" }, `[${i + 1}] ${s.title || s.url}`))) : null));
}

async function busy(btn, noteId, msg, fn) {
  btn.disabled = true; note(noteId, msg);
  try { await fn(); } catch (e) { note(noteId, e.message, true); } finally { btn.disabled = false; }
}

$("#lt-go").addEventListener("click", () => busy($("#lt-go"), "#lt-note", "Searching and reading the web… up to a minute.", async () => {
  const topic = $("#lt-topic").value.trim(); if (!topic) throw new Error("Type a topic first.");
  const r = await api("/api/learn/topic", { method: "POST", body: { topic, instruction: $("#lt-instr").value.trim(), frequency: $("#lt-freq").value } });
  note("#lt-note", r.scheduled ? `Learned, and scheduled ${r.scheduled.frequency}.` : "Learned and saved to your knowledge library.");
  resultBox("#lt-result", r.result.notes, r.result.sources); loadLearn();
}));

$("#lu-go").addEventListener("click", () => busy($("#lu-go"), "#lu-note", "Reading the page…", async () => {
  const url = $("#lu-url").value.trim(); if (!url) throw new Error("Paste a link first.");
  const r = await api("/api/learn/url", { method: "POST", body: { url, instruction: $("#lu-instr").value.trim() } });
  note("#lu-note", `Learned "${r.title}".`);
  resultBox("#lu-result", r.notes ? r.notes.text : `Saved ${r.source.chunks} sections from the page.`, [{ title: r.title, url: r.url }]);
  loadLearn();
}));

$("#lf-go").addEventListener("click", () => busy($("#lf-go"), "#lf-note", "Reading the document…", async () => {
  const f = $("#lf-file").files[0]; if (!f) throw new Error("Choose a file first.");
  const buf = new Uint8Array(await f.arrayBuffer());
  let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  const r = await api("/api/learn/file", { method: "POST", body: { name: f.name, data_base64: btoa(bin),
    instruction: $("#lf-instr").value.trim(), sensitivity: $("#lf-sens").value } });
  note("#lf-note", `Learned "${r.title}".`);
  resultBox("#lf-result", r.notes ? r.notes.text : `Saved ${r.source.chunks} sections from the document.`);
  loadLearn();
}));

$("#rule-add").addEventListener("click", async () => {
  const text = $("#rule-text").value.trim(); if (!text) return;
  await api("/api/rules", { method: "POST", body: { text } }); $("#rule-text").value = ""; loadLearn();
});

async function loadLearn() {
  const [rules, topics, know] = await Promise.all([api("/api/rules"), api("/api/topics"), api("/api/knowledge")]);
  $("#rule-list").replaceChildren(...(rules.length ? rules.map((r) => el("div", { class: "item" },
    el("div", { class: "body" }, r.content),
    el("div", { class: "meta" }, `added ${fmtDate(r.created_at)}`, el("span", { class: "actions" },
      el("button", { class: "mini", onclick: async () => {
        if (!confirm("Remove this rule?")) return;
        await api(`/api/memories/${r.id}`, { method: "DELETE" }); loadLearn();
      } }, "Remove")))))
    : [el("p", { class: "hint" }, "No rules yet.")]));

  $("#topic-list").replaceChildren(...(topics.length ? topics.map((t) => el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, t.topic), t.instruction && t.instruction !== t.topic ? ` — ${t.instruction}` : ""),
    el("div", { class: "meta" }, t.frequency, t.active ? "active" : "paused",
      t.last_run ? `last ${fmtDate(t.last_run)}` : "not run yet", `next ${fmtDate(t.next_run)}`,
      el("span", { class: "actions" },
        el("button", { class: "mini", onclick: async () => {
          await api(`/api/topics/${t.id}`, { method: "PATCH", body: { active: !t.active } }); loadLearn();
        } }, t.active ? "Pause" : "Resume"),
        el("button", { class: "mini", onclick: async () => {
          if (!confirm("Delete this scheduled topic? Knowledge already learned is kept.")) return;
          await api(`/api/topics/${t.id}`, { method: "DELETE" }); loadLearn();
        } }, "Delete")))))
    : [el("p", { class: "hint" }, "No scheduled topics. Choose 'every day/week/month' above, or type \"every week learn about …\" in chat.")]));

  const kinds = { web: "Web page", file: "File", note: "Note", study_notes: "Study notes" };
  $("#know-list").replaceChildren(...(know.length ? know.map((k) => {
    const view = el("div", { class: "result-box", hidden: "" });
    return el("div", { class: "item" },
      el("div", { class: "body" }, el("strong", {}, k.title || k.url), k.url && !k.url.startsWith("file:")
        ? el("div", {}, el("a", { href: k.url, target: "_blank", rel: "noopener", class: "small" }, k.url)) : null),
      k.instruction ? el("div", { class: "meta" }, `Your instruction: ${k.instruction.slice(0, 160)}`) : null,
      el("div", { class: "meta" }, kinds[k.kind] || k.kind, `learned ${fmtDate(k.created_at)}`, `${k.chunks} sections`,
        k.sensitivity === "sealed" ? el("span", { class: "sens-sealed" }, "sealed") : null,
        el("span", { class: "actions" },
          el("button", { class: "mini", onclick: async () => {
            if (view.hidden) view.textContent = (await api(`/api/knowledge/${k.id}`)).text;
            view.hidden = !view.hidden;
          } }, "View"),
          el("button", { class: "mini", onclick: async () => {
            if (!confirm("Delete this from the knowledge library?")) return;
            await api(`/api/knowledge/${k.id}`, { method: "DELETE" }); loadLearn();
          } }, "Delete"))), view);
  }) : [el("p", { class: "hint" }, "Nothing learned yet. Try: /learn latest free AI APIs for developers")]));
}
loaders.learn = loadLearn;

// ---------- Start ----------
loadBrand(true).then((open) => { if (open) { loadStatus(); loadChat(); } });
setInterval(() => { if ($("#lock").hidden) loadStatus(); }, 30000);
window.addEventListener("load", () => {  // after voice.js has loaded
  const startTab = location.hash.slice(1);
  if (startTab && SHEETS[startTab]) openTab(startTab); else showSheet("chat");
});


// ---------- Master Control ----------
const SWATCHES = ["#ffd84a", "#c8ff35", "#ff7a3d", "#ff5c8a", "#5ce1e6", "#ffffff"];
let M = null;

function renderMasterState() {
  const b = window.BRAND, resting = !!b.resting;
  $("#m-state").classList.toggle("resting", resting);
  $("#m-state-text").textContent = resting ? `${b.brain_name} is resting` : `${b.brain_name} is awake`;
  $("#m-state-hint").textContent = resting ? `Say "${b.wake_words?.[0]}" to wake` : `Say "${b.sleep_words?.[0] || "go to sleep"}" to rest`;
  $("#m-rest").textContent = resting ? "Wake up" : "Rest now";
}

function renderMaster(m) {
  M = m;
  $("#m-gate").hidden = !(m.pin_set && !m.unlocked);
  $("#m-body").hidden = m.pin_set && !m.unlocked;
  $("#m-brain").value = m.brain_name; $("#m-owner").value = m.owner_name;
  $("#m-greeting").value = m.greeting_template || ""; $("#m-subtitle").value = m.subtitle;
  $("#m-suggest").value = (m.suggestions || []).join("\n"); $("#m-mode").value = m.default_mode;
  $("#m-wake").value = (m.wake_words || []).join(", "); $("#m-sleep").value = (m.sleep_words || []).join(", ");
  $("#m-stop").value = (m.stop_words || []).join(", ");
  $("#f-web").checked = m.features.web !== false; $("#f-learning").checked = m.features.learning !== false;
  $("#f-voice").checked = m.features.voice !== false; $("#f-suggest").checked = m.features.suggest_facts !== false;
  $("#m-theme").value = m.theme;
  $("#m-swatches").replaceChildren(...SWATCHES.map((c) => el("button", { type: "button", class: `swatch${c === m.accent ? " on" : ""}`,
    style: `background:${c}`, "aria-label": `Signal colour ${c}`, onclick: () => { M.accent = c; renderMaster(M);
      document.documentElement.style.setProperty("--signal", c); } })),
    Object.assign(el("input", { type: "color", value: m.accent, "aria-label": "Custom signal colour" }), {
      oninput: (e) => { M.accent = e.target.value; document.documentElement.style.setProperty("--signal", e.target.value); } }));
  $("#m-pin-status").textContent = m.pin_set
    ? "A Master PIN is set. It's needed to change Master Control and Settings."
    : "No Master PIN yet. Anyone using this computer can change these settings.";
  $("#m-pin-old").hidden = !m.pin_set; $("#m-pin-remove").hidden = !m.pin_set;
  $("#m-pin-save").textContent = m.pin_set ? "Change PIN" : "Set PIN";
  $("#m-lockapp").checked = !!m.lock_app; $("#m-lockapp").disabled = !m.pin_set;
  $("#m-lock-now").hidden = !m.pin_set;
  renderMasterState();
}

async function loadMaster() {
  renderMaster(await api("/api/master/public"));
}
loaders.master = loadMaster;

$("#m-theme").addEventListener("change", (e) => { document.documentElement.dataset.theme = e.target.value; });

$("#m-save").addEventListener("click", async () => {
  const changes = {
    brain_name: $("#m-brain").value, owner_name: $("#m-owner").value, greeting: $("#m-greeting").value,
    subtitle: $("#m-subtitle").value, suggestions: $("#m-suggest").value, default_mode: $("#m-mode").value,
    wake_words: $("#m-wake").value, sleep_words: $("#m-sleep").value, stop_words: $("#m-stop").value,
    theme: $("#m-theme").value, accent: M.accent, lock_app: $("#m-lockapp").checked,
    features: { web: $("#f-web").checked, learning: $("#f-learning").checked, voice: $("#f-voice").checked,
                suggest_facts: $("#f-suggest").checked },
  };
  try {
    const r = await api("/api/master", { method: "PUT", body: { changes } });
    applyBrand(r); renderMaster(r); loadStatus();
    if (window.voiceReload) window.voiceReload();
    note("#m-note", `Saved. ${r.brain_name} is updated.`);
  } catch (e) { note("#m-note", e.message, true); }
});

$("#m-rest").addEventListener("click", async () => {
  const r = await api("/api/master/rest", { method: "POST", body: { resting: !window.BRAND.resting } });
  applyBrand(r); renderMasterState(); loadStatus();
});

$("#m-pin-save").addEventListener("click", async () => {
  try {
    await api("/api/master/pin", { method: "POST", body: { pin: $("#m-pin-old").value, new_pin: $("#m-pin-new").value } });
    $("#m-pin-old").value = ""; $("#m-pin-new").value = "";
    note("#m-pin-note", "PIN saved. Keep it somewhere safe: it can't be recovered, only reset by deleting data/settings.json."); loadMaster();
  } catch (e) { note("#m-pin-note", e.message, true); }
});
$("#m-pin-remove").addEventListener("click", async () => {
  if (!confirm("Remove the Master PIN? Anyone using this computer could then change Master Control.")) return;
  try {
    await api("/api/master/pin/remove", { method: "POST", body: { pin: $("#m-pin-old").value } });
    $("#m-pin-old").value = ""; note("#m-pin-note", "PIN removed."); loadMaster();
  } catch (e) { note("#m-pin-note", e.message, true); }
});
$("#m-lock-now").addEventListener("click", async () => {
  await api("/api/master/lock", { method: "POST" });
  if (window.BRAND.lock_app) showLock(); else { note("#m-pin-note", "Master Control locked."); loadMaster(); }
});
async function gateUnlock() {
  try {
    await api("/api/master/unlock", { method: "POST", body: { pin: $("#m-gate-pin").value } });
    $("#m-gate-pin").value = ""; loadMaster();
  } catch (e) { note("#m-gate-note", e.message, true); }
}
$("#m-gate-go").addEventListener("click", gateUnlock);
$("#m-gate-pin").addEventListener("keydown", (e) => { if (e.key === "Enter") gateUnlock(); });

$("#m-export").addEventListener("click", async () => {
  const res = await fetch("/api/master/export");
  if (!res.ok) { note("#m-note", (await res.json().catch(() => ({}))).detail || "Export failed", true); return; }
  const url = URL.createObjectURL(await res.blob());
  const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "brain-backup.json";
  Object.assign(document.createElement("a"), { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
});
