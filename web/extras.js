// Phase 2 and 3 screens: more channels, agents, MCP, plugins, workflows, standing orders, email watcher,
// API tokens, usage, notifications and the device node.

const MORE_CHANNELS = {
  whatsapp: { label: "WhatsApp", hook: true,
    steps: ["Uses Meta's official WhatsApp Cloud API (free tier). At developers.facebook.com create an app with WhatsApp, and copy the access token, phone number ID and app secret.",
            "In WhatsApp → Configuration, set the webhook URL below and the same verify token as here, and subscribe to 'messages'. Your Brain must be online over HTTPS."],
    fields: { access_token: "Access token", phone_number_id: "Phone number ID", app_secret: "App secret", verify_token: "Verify token (any word you choose)" } },
  signal: { label: "Signal",
    steps: ["Run signal-cli-rest-api (free, Docker: bbernhard/signal-cli-rest-api) and link it to a phone number.", "Enter its address and that number here."],
    fields: { base_url: "signal-cli-rest-api address", number: "Signal number (+91…)" } },
  matrix: { label: "Matrix",
    steps: ["Make a Matrix account for your Brain (e.g. on matrix.org) and copy its access token (Element → Settings → Help & About).", "Invite it to a chat. Encrypted rooms aren't supported yet; use unencrypted ones."],
    fields: { homeserver: "Homeserver (https://matrix.org)", access_token: "Access token", user_id: "User ID (@brain:matrix.org)" } },
  mattermost: { label: "Mattermost",
    steps: ["In Mattermost → Integrations → Bot accounts, add a bot and copy its token.", "DM the bot, or mention it in a channel."],
    fields: { url: "Server address", token: "Bot token" } },
  irc: { label: "IRC",
    steps: ["Pick a server (e.g. irc.libera.chat, port 6697, TLS) and a nickname.", "IRC names aren't verified: approve only your own nick and host."],
    fields: { server: "Server", port: "Port (6697)", nick: "Nickname", password: "Password (optional)", channels: "Channels to join (#a, #b)" } },
  twitch: { label: "Twitch chat",
    steps: ["Make a Twitch account for the bot and get a chat OAuth token for it.", "It answers in your channel's chat when mentioned."],
    fields: { nick: "Bot username", token: "OAuth token", channels: "Channels (#yourchannel)" } },
  sms: { label: "SMS (Twilio)", hook: true,
    steps: ["In Twilio, buy a number and copy your Account SID and Auth Token.", "Set the number's 'A message comes in' webhook to the URL below. Only numbers you add are answered."],
    fields: { account_sid: "Account SID", auth_token: "Auth token", from_number: "Your Twilio number (+1…)" } },
  line: { label: "LINE", hook: true,
    steps: ["In the LINE Developers console, create a Messaging API channel and copy its channel access token and channel secret.", "Set the webhook URL below and turn 'Use webhook' on."],
    fields: { access_token: "Channel access token", channel_secret: "Channel secret" } },
};
const SECRET_FIELDS = new Set(["access_token", "app_secret", "token", "password", "auth_token", "channel_secret"]);

function buildMoreChannels() {
  const box = $("#ch-more");
  if (box.dataset.built) return;
  box.dataset.built = "1";
  for (const [ch, info] of Object.entries(MORE_CHANNELS)) {
    const card = el("div", { class: "card ch-card", "data-ch": ch },
      el("h2", {}, info.label, " ", el("span", { class: "ch-state", id: `st-${ch}` })),
      el("ol", { class: "steps" }, ...info.steps.map((t) => el("li", {}, t))),
      info.hook ? el("p", { class: "hint" }, "Webhook URL: ", el("code", { class: "mono hook-url", "data-hook": ch }, "…")) : null,
      el("div", { class: "mgrid" }, ...Object.entries(info.fields).map(([k, label]) =>
        el("label", { class: "field" }, label, el("input", { "data-k": k, type: SECRET_FIELDS.has(k) ? "password" : "text" })))),
      el("div", { class: "switches" }, el("label", { class: "switch" }, el("span", {}, `${info.label} on`), el("input", { type: "checkbox", "data-k": "enabled" }))),
      ch === "sms" ? el("div", { class: "row" }, el("input", { id: "ch-sms-add", placeholder: "+91 98… (your own number)" }),
        el("button", { type: "button", onclick: async () => {
          try { await api("/api/channels/allow", { method: "POST", body: { channel: "sms", sender: $("#ch-sms-add").value.replace(/\s/g, "") } }); $("#ch-sms-add").value = ""; loaders.channels(); }
          catch (e) { note("#ch-save-note", e.message, true); } } }, "Allow this number")) : null);
    box.append(card);
  }
}

const _loadChannels = loaders.channels;
loaders.channels = async () => {
  buildMoreChannels();
  await _loadChannels();
  const r = await api("/api/channels").catch(() => null);
  if (r) document.querySelectorAll("[data-hook]").forEach((c) => { c.textContent = r.webhooks[c.dataset.hook] || ""; });
};

// ---------- Agents, MCP, plugins (Skills tab) ----------
const split = (v) => v.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

async function loadAgents() {
  const r = await api("/api/agents").catch(() => ({ agents: [] }));
  $("#ag-list").replaceChildren(...(r.agents.length ? r.agents.map((a) => el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, a.name), ` · ${a.persona.slice(0, 160)}`),
    el("div", { class: "meta" }, a.model || "usual models", a.channels.length ? `on ${a.channels.join(", ")}` : "web chat only",
      a.tools_off.length ? `no ${a.tools_off.join(", ")}` : "",
      el("span", { class: "actions" },
        el("button", { class: "mini", onclick: () => { $("#ag-name").value = a.name; $("#ag-model").value = a.model; $("#ag-channels").value = a.channels.join(", ");
          $("#ag-tools").value = a.tools_off.join(", "); $("#ag-persona").value = a.persona; $("#ag-name").scrollIntoView({ behavior: "smooth" }); } }, "Edit"),
        el("button", { class: "mini danger", onclick: async () => { if (confirm(`Delete agent ${a.name}?`)) { await api(`/api/agents/${a.id}`, { method: "DELETE" }); loadAgents(); loadAgentPicker(); } } }, "Delete")))))
    : [el("p", { class: "hint" }, "No extra agents. Your main Brain answers everywhere.")]));
}
$("#ag-save").addEventListener("click", async () => {
  try {
    await api("/api/agents", { method: "POST", body: { agent: { name: $("#ag-name").value, model: $("#ag-model").value.trim(),
      channels: split($("#ag-channels").value.toLowerCase()), tools_off: split($("#ag-tools").value), persona: $("#ag-persona").value } } });
    note("#ag-note", "Saved. Pick it above the chat, or message it on its chat apps.");
    ["#ag-name", "#ag-model", "#ag-channels", "#ag-tools", "#ag-persona"].forEach((id) => { $(id).value = ""; });
    loadAgents(); loadAgentPicker();
  } catch (e) { note("#ag-note", e.message, true); }
});

async function loadMcp() {
  const r = await api("/api/mcp").catch(() => null);
  if (!r) return;
  $("#mcp-kind").querySelector('[value="stdio"]').disabled = !r.can_stdio;
  $("#mcp-list").replaceChildren(...(r.servers.length ? r.servers.map((s) => el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, s.name), ` · ${s.kind === "http" ? s.url : s.command}`),
    el("div", { class: "meta" }, `${s.tools.length} tools`, s.tools.slice(0, 6).map((t) => t.name).join(", "),
      el("span", { class: "actions" },
        el("button", { class: "mini", onclick: async () => { try { await api(`/api/mcp/${s.name}/refresh`, { method: "POST" }); loadMcp(); } catch (e) { note("#mcp-note", e.message, true); } } }, "Reconnect"),
        el("button", { class: "mini danger", onclick: async () => { await api(`/api/mcp/${s.name}`, { method: "DELETE" }); loadMcp(); } }, "Remove")))))
    : [el("p", { class: "hint" }, "No MCP servers yet.")]));
  const state = [r.browser ? "Browser tool: installed." : "Browser tool: not installed.",
                 r.sandbox === "docker" ? "Python sandbox: Docker (no internet)." : r.sandbox === "local" ? "Python sandbox: limited process on this computer (no network isolation)." : "Python sandbox: off (install Docker, or switch on sandbox_local on your own computer)."];
  $("#pl-state").textContent = state.join(" ");
  $("#br-install").hidden = r.browser;
}
$("#mcp-kind").addEventListener("change", (e) => { $("#mcp-url-f").hidden = e.target.value !== "http"; $("#mcp-cmd-f").hidden = e.target.value === "http"; });
$("#mcp-save").addEventListener("click", async (e) => {
  const h = $("#mcp-header").value.trim(), i = h.indexOf(":");
  e.target.disabled = true; note("#mcp-note", "Connecting…");
  try {
    const r = await api("/api/mcp", { method: "POST", body: { server: { name: $("#mcp-name").value, kind: $("#mcp-kind").value,
      url: $("#mcp-url").value, command: $("#mcp-cmd").value, headers: i > 0 ? { [h.slice(0, i).trim()]: h.slice(i + 1).trim() } : {} } } });
    note("#mcp-note", `Connected: ${r.tools.length} tools.`); loadMcp();
  } catch (err) { note("#mcp-note", err.message, true); }
  e.target.disabled = false;
});

async function loadPlugins() {
  const r = await api("/api/plugins").catch(() => null);
  if (!r) return;
  $("#pl-install-btn").hidden = !r.can_install;
  $("#pl-list").replaceChildren(...r.plugins.map((p) => el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, p.name), p.description ? ` · ${p.description}` : ""),
    el("div", { class: "meta" }, p.enabled ? `on · ${p.tools.length} tools` : "off", p.error ? `error: ${p.error}` : "",
      el("span", { class: "actions" }, el("button", { class: "mini", onclick: async () => {
        if (!p.enabled && !confirm(`Turn on the plugin ${p.name}? Plugins run Python code on this computer; only use ones you trust.`)) return;
        await api(`/api/plugins/${p.name}`, { method: "POST", body: { enabled: !p.enabled } }); loadPlugins(); } }, p.enabled ? "Turn off" : "Turn on"))))));
}
$("#pl-install-btn").addEventListener("click", () => $("#pl-file").click());
$("#pl-file").addEventListener("change", async (e) => {
  const fd = new FormData(); fd.append("file", e.target.files[0]);
  const res = await fetch("/api/plugins", { method: "POST", body: fd }); const data = await res.json();
  note("#pl-note", res.ok ? `Installed ${data.installed}. Turn it on below.` : data.detail, !res.ok); loadPlugins(); e.target.value = "";
});
$("#sk-export").addEventListener("click", () => { window.location.href = "/api/skills/export"; });
$("#sk-import-btn").addEventListener("click", () => $("#sk-file").click());
$("#sk-file").addEventListener("change", async (e) => {
  const fd = new FormData(); fd.append("file", e.target.files[0]);
  const res = await fetch("/api/skills/import", { method: "POST", body: fd }); const data = await res.json();
  note("#pl-note", res.ok ? `Imported ${data.added.length} skills as suggestions; switch on the ones you want.` : data.detail, !res.ok);
  loaders.skills(); e.target.value = "";
});
$("#br-install").addEventListener("click", async (e) => {
  e.target.disabled = true; e.target.textContent = "Installing (a few minutes)…";
  try { await api("/api/browser/install", { method: "POST" }); note("#pl-note", "Browser tool installed."); } catch (err) { note("#pl-note", err.message, true); }
  e.target.disabled = false; e.target.textContent = "Install the browser tool"; loadMcp();
});

const _loadSkills = loaders.skills;
loaders.skills = async () => { await _loadSkills(); loadAgents(); loadMcp(); loadPlugins(); };

// ---------- Workflows, standing orders, email watcher (Automations tab) ----------
function parseSteps(text) {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^(prompt|approve|send|tool)\s*:\s*(.*)$/i);
    if (!m) throw new Error(`I don't understand the step "${l}".`);
    const type = m[1].toLowerCase(), rest = m[2];
    if (type === "send") return { type, channel: rest.trim().toLowerCase() || "web" };
    if (type === "tool") { const sp = rest.indexOf(" "); return { type, name: sp > 0 ? rest.slice(0, sp) : rest, args: sp > 0 ? JSON.parse(rest.slice(sp + 1)) : {} }; }
    return { type, text: rest };
  });
}
const stepText = (s) => s.type === "send" ? `send: ${s.channel}` : s.type === "tool" ? `tool: ${s.name} ${JSON.stringify(s.args || {})}` : `${s.type}: ${s.text}`;

async function loadWorkflows() {
  const r = await api("/api/workflows").catch(() => null);
  if (!r) return;
  $("#wf-list").replaceChildren(...r.workflows.map((w) => el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, w.name), ` · id ${w.id} · ${w.steps.length} steps`),
    el("div", { class: "meta" }, el("span", { class: "actions" },
      el("button", { class: "mini", onclick: async () => { const run = await api(`/api/workflows/${w.id}/run`, { method: "POST" }); note("#wf-note", `Run ${run.status}.`); loadWorkflows(); } }, "Run now"),
      el("button", { class: "mini", onclick: () => { $("#wf-name").value = w.name; $("#wf-steps").value = w.steps.map(stepText).join("\n"); $("#wf-name").dataset.id = w.id; } }, "Edit"),
      el("button", { class: "mini danger", onclick: async () => { await api(`/api/workflows/${w.id}`, { method: "DELETE" }); loadWorkflows(); } }, "Delete"))))));
  $("#wf-runs").replaceChildren(...(r.runs.length ? r.runs.slice(0, 10).map((x) => el("div", { class: "item" },
    el("div", { class: "body" }, `${x.name}: `, el("span", { class: `task-state ${x.status === "waiting" ? "waiting" : x.status === "done" ? "done" : "stopped"}` }, x.status)),
    x.status === "waiting" ? el("div", { class: "task-ask" }, el("p", {}, x.question), el("pre", {}, (x.previous || "").slice(0, 800)),
      el("div", { class: "row" },
        el("button", { onclick: async () => { await api(`/api/workflows/runs/${x.id}/decide`, { method: "POST", body: { approve: true } }); loadWorkflows(); } }, "Approve"),
        el("button", { class: "ghost", onclick: async () => { await api(`/api/workflows/runs/${x.id}/decide`, { method: "POST", body: { approve: false } }); loadWorkflows(); } }, "Reject"))) : null,
    el("details", {}, el("summary", {}, `${x.outputs.length} steps done`), ...x.outputs.map((o) => el("div", { class: "meta" }, `${o.type}: ${o.output.slice(0, 300)}`)))))
    : [el("p", { class: "hint" }, "No runs yet.")]));
  if (document.activeElement !== $("#so-list")) $("#so-list").value = r.orders.map((o) => `${o.when} => ${o.do}`).join("\n");
  const m = r.mail;
  fillDeliver($("#mw-deliver"), m.deliver);
  $("#mw-address").value = m.address; $("#mw-host").value = m.imap_host; $("#mw-prompt").value = m.prompt; $("#mw-on").checked = m.enabled;
  $("#mw-password").placeholder = m.has_password ? "•••••• saved" : "";
}
$("#wf-save").addEventListener("click", async () => {
  try {
    await api("/api/workflows", { method: "POST", body: { workflow: { id: $("#wf-name").dataset.id || undefined, name: $("#wf-name").value, steps: parseSteps($("#wf-steps").value) } } });
    note("#wf-note", "Saved."); $("#wf-name").value = $("#wf-steps").value = ""; delete $("#wf-name").dataset.id; loadWorkflows();
  } catch (e) { note("#wf-note", e.message, true); }
});
$("#so-save").addEventListener("click", async () => {
  const orders = $("#so-list").value.split("\n").map((l) => l.split("=>")).filter((p) => p.length === 2).map(([when, d]) => ({ when: when.trim(), do: d.trim() }));
  try { const r = await api("/api/orders", { method: "PUT", body: { orders } }); note("#so-note", `Saved ${r.orders.length} orders.`); } catch (e) { note("#so-note", e.message, true); }
});
$("#mw-save").addEventListener("click", async () => {
  try {
    await api("/api/mailwatch", { method: "PUT", body: { changes: { enabled: $("#mw-on").checked, address: $("#mw-address").value, password: $("#mw-password").value,
      imap_host: $("#mw-host").value, deliver: $("#mw-deliver").value, prompt: $("#mw-prompt").value } } });
    $("#mw-password").value = ""; note("#mw-note", "Saved."); loadWorkflows();
  } catch (e) { note("#mw-note", e.message, true); }
});
$("#mw-check").addEventListener("click", async () => {
  try { const r = await api("/api/mailwatch/check", { method: "POST" }); note("#mw-note", `Checked. ${r.new} new emails read.`); } catch (e) { note("#mw-note", e.message, true); }
});
const _loadAutomations = loaders.automations;
loaders.automations = async () => { await _loadAutomations(); loadWorkflows(); };

// ---------- Master: this device, API tokens, usage ----------
const b64url = (s) => { const p = "=".repeat((4 - (s.length % 4)) % 4); const raw = atob((s + p).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from([...raw].map((c) => c.charCodeAt(0))); };

async function pushState() {
  const ok = "serviceWorker" in navigator && "PushManager" in window;
  if (!ok) { $("#d-push").disabled = true; $("#d-push-state").textContent = "This browser can't receive notifications (on iPhone, install the app to the Home Screen first)."; return null; }
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  $("#d-push").checked = !!sub;
  $("#d-push-state").textContent = sub ? "On for this device." : "Off.";
  return { reg, sub };
}
$("#d-push").addEventListener("change", async (e) => {
  const st = await pushState(); if (!st) return;
  try {
    if (e.target.checked) {
      if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications were blocked in the browser settings.");
      const { key } = await api("/api/push");
      const sub = await st.reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64url(key) });
      await api("/api/push/subscribe", { method: "POST", body: { subscription: sub.toJSON() } });
      note("#d-note", "Notifications are on for this device.");
    } else if (st.sub) {
      await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: st.sub.endpoint } });
      await st.sub.unsubscribe();
      note("#d-note", "Notifications are off for this device.");
    }
  } catch (err) { note("#d-note", err.message, true); e.target.checked = !e.target.checked; }
  pushState();
});
$("#d-test").addEventListener("click", async () => {
  try { const r = await api("/api/push/test", { method: "POST" }); note("#d-note", `Sent to ${r.sent} device(s).`); } catch (e) { note("#d-note", e.message, true); }
});

// The device node: while this page is open, the Brain may ask for location, a photo or a notification.
let NODE = null;
function deviceName() {
  const ua = navigator.userAgent;
  return /Android/.test(ua) ? "Android phone" : /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows PC" : "This device";
}
async function nodeDo(msg) {
  const ask = { location: "share your location", photo: "take a photo with the camera", notify: "show a notification" }[msg.action];
  if (!confirm(`${window.BRAND?.brain_name || "Your Brain"} wants to ${ask}.${msg.text ? `\n\n"${msg.text}"` : ""}\n\nAllow?`)) return { ok: false, error: "The owner said no on the device." };
  if (msg.action === "location") {
    const pos = await new Promise((res, rej) => navigator.geolocation.getCurrentPosition(res, rej, { enableHighAccuracy: true, timeout: 20000 }));
    return { ok: true, result: { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy } };
  }
  if (msg.action === "photo") {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    const v = el("video", { playsinline: "", muted: "" }); v.srcObject = stream; await v.play();
    await new Promise((r) => setTimeout(r, 700));
    const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0); stream.getTracks().forEach((t) => t.stop());
    return { ok: true, result: { data: c.toDataURL("image/jpeg", 0.8).split(",")[1] } };
  }
  new Notification(window.BRAND?.brain_name || "Brain", { body: msg.text || "Hello from your Brain" });
  return { ok: true, result: {} };
}
function nodeConnect() {
  if (NODE) return;
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/node/ws`);
  NODE = ws;
  ws.onopen = () => ws.send(JSON.stringify({ name: deviceName(), caps: ["location", "photo", "notify"].filter((c) =>
    c === "location" ? "geolocation" in navigator : c === "photo" ? !!navigator.mediaDevices : "Notification" in window) }));
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    let reply;
    try { reply = await nodeDo(msg); } catch (err) { reply = { ok: false, error: err.message || String(err) }; }
    ws.send(JSON.stringify({ id: msg.id, ...reply }));
  };
  ws.onclose = () => { NODE = null; if (localStorageGet("brain_node") === "1") setTimeout(nodeConnect, 5000); };
}
function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
$("#d-node").addEventListener("change", (e) => {
  localStorageSet("brain_node", e.target.checked ? "1" : "0");
  if (e.target.checked) { nodeConnect(); note("#d-note", "This device is available to your Brain while the app is open."); }
  else { NODE?.close(); NODE = null; note("#d-note", "This device is no longer available."); }
});
if (localStorageGet("brain_node") === "1") { $("#d-node").checked = true; setTimeout(nodeConnect, 2000); }

async function loadTokens() {
  $("#tk-base").textContent = `${location.origin}/v1`;
  const list = await api("/api/tokens").catch(() => []);
  $("#tk-list").replaceChildren(...(list.length ? list.map((t) => el("div", { class: "item" },
    el("div", { class: "body" }, t.name), el("div", { class: "meta" }, `made ${fmtDate(t.created)}`, t.last_used ? `used ${fmtDate(t.last_used)}` : "never used",
      el("span", { class: "actions" }, el("button", { class: "mini danger", onclick: async () => { await api(`/api/tokens/${t.id}`, { method: "DELETE" }); loadTokens(); } }, "Delete")))))
    : [el("p", { class: "hint" }, "No tokens yet.")]));
}
$("#tk-make").addEventListener("click", async () => {
  try {
    const t = await api("/api/tokens", { method: "POST", body: { name: $("#tk-name").value || "API token" } });
    const box = $("#tk-out"); box.hidden = false;
    box.replaceChildren(el("div", {}, el("span", { class: "code" }, "Your token (shown once)"), el("strong", { class: "mono" }, t.token)),
      el("p", { class: "hint" }, "Copy it now. Anyone with it can use your Brain; delete it here to revoke."));
    $("#tk-name").value = ""; loadTokens();
  } catch (e) { alert(e.message); }
});

async function loadUsage() {
  const rows = await api("/api/usage").catch(() => []);
  if (!rows.length) { $("#us-table").replaceChildren(el("p", { class: "hint" }, "No model requests yet.")); return; }
  const t = el("table", { class: "usage" }, el("thead", {}, el("tr", {}, ...["Day", "Provider", "Model", "OK", "Failed", "Avg s"].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...rows.slice(0, 40).map((r) => el("tr", {}, ...[r.day, r.provider, r.model, r.ok, r.failed, r.avg_seconds].map((v) => el("td", {}, String(v)))))));
  $("#us-table").replaceChildren(t);
}

const _loadMasterX = loaders.master;
loaders.master = async () => { await _loadMasterX(); pushState(); loadTokens(); loadUsage(); };
