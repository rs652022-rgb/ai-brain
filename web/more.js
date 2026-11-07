// More screens: 15 more chat apps, goals and limits, Gmail push, secrets vault, Chrome extension,
// memory engines and tracing, skill marketplace, coding workspaces with a diff viewer.

// ---------- More chat apps (Channels tab) ----------
const HOOK_NOTE = "Set the webhook URL below in the service's developer settings. Your Brain must be online over HTTPS.";
Object.assign(MORE_CHANNELS, {
  wechat: { label: "WeChat (Official Account)", hook: true,
    steps: ["At mp.weixin.qq.com, open your Official Account → Settings and development → Basic configuration. Copy the AppID and AppSecret.",
            "Set the server URL to the webhook URL below, choose a Token (any word, the same as here), and plain-text mode. " + HOOK_NOTE],
    fields: { app_id: "AppID", app_secret: "AppSecret", token: "Token" } },
  wecom: { label: "WeCom (WeChat Work)", hook: true,
    steps: ["In the WeCom admin console → Apps → create an app. Copy Corp ID, Agent ID and Secret.",
            "In the app's 'Receive messages' settings, set the webhook URL below, and copy the Token and EncodingAESKey here."],
    fields: { corp_id: "Corp ID", agent_id: "Agent ID", secret: "Secret", token: "Token", aes_key: "EncodingAESKey" } },
  feishu: { label: "Feishu / Lark", hook: true,
    steps: ["At open.feishu.cn (or open.larksuite.com), create an app, enable the Bot, and add the im.message.receive_v1 event.",
            "Set the event request URL to the webhook URL below. Copy App ID, App Secret, Verification Token (and Encrypt Key if set)."],
    fields: { app_id: "App ID", app_secret: "App Secret", verification_token: "Verification Token", encrypt_key: "Encrypt Key (optional)", domain: "lark for Lark, empty for Feishu" } },
  dingtalk: { label: "DingTalk", hook: true,
    steps: ["In the DingTalk developer console, create a robot with 'HTTP' message receiving.", "Set its message URL to the webhook URL below and copy the AppSecret here."],
    fields: { app_secret: "AppSecret" } },
  qq: { label: "QQ (official bot)",
    steps: ["At q.qq.com create a bot, and copy its AppID and AppSecret.", "It connects out to QQ (no webhook). It answers private chats and @mentions in groups."],
    fields: { app_id: "AppID", app_secret: "AppSecret" } },
  zalo: { label: "Zalo (Official Account)", hook: true,
    steps: ["At developers.zalo.me, link your Official Account to an app and get an OA access token.", "Set the webhook URL below and copy the OA secret key."],
    fields: { app_id: "App ID", oa_secret: "OA secret key", access_token: "OA access token" } },
  nextcloud: { label: "Nextcloud Talk", hook: true,
    steps: ["As a Nextcloud admin run: occ talk:bot:install \"Brain\" \"<a long random secret>\" \"<webhook URL below>\"", "Then add the bot to a conversation (its settings → Bots). Enter the same secret and your Nextcloud address here."],
    fields: { server: "Nextcloud address (https://…)", secret: "Bot secret" } },
  synology: { label: "Synology Chat", hook: true,
    steps: ["In Synology Chat → Integration → Bots, create a bot. Set its outgoing URL to the webhook URL below.", "Copy the bot's token and incoming URL here."],
    fields: { token: "Token", incoming_url: "Incoming URL" } },
  rocketchat: { label: "Rocket.Chat", hook: true,
    steps: ["Make a bot user and a personal access token for it (user ID + token).", "Administration → Integrations → Outgoing webhook to the URL below, with a token you choose."],
    fields: { server: "Server address", user_id: "Bot user ID", auth_token: "Bot access token", token: "Outgoing webhook token" } },
  zulip: { label: "Zulip",
    steps: ["Settings → Personal → Bots → add a 'Generic bot'. Copy its email and API key.", "It connects out to Zulip. It answers DMs and @mentions in streams."],
    fields: { site: "Zulip address (https://…)", email: "Bot email", api_key: "API key" } },
  viber: { label: "Viber", hook: true, register: true,
    steps: ["At partners.viber.com create a bot account and copy its token.", "Save, then press 'Connect webhook' (Viber needs an API call, done for you)."],
    fields: { auth_token: "Bot token", bot_name: "Bot name" } },
  messenger: { label: "Facebook Messenger", hook: true,
    steps: ["At developers.facebook.com create an app with Messenger, connect your Page and copy the Page access token and App secret.",
            "Set the callback URL below with the same verify token, and subscribe to 'messages'."],
    fields: { page_token: "Page access token", app_secret: "App secret", verify_token: "Verify token (any word)" } },
  instagram: { label: "Instagram DMs", hook: true,
    steps: ["Your Instagram professional account must be linked to a Facebook Page. In the Meta app add Instagram messaging.",
            "Set the callback URL below with the same verify token, subscribe to 'messages', and copy the Page token and App secret."],
    fields: { page_token: "Page access token", app_secret: "App secret", verify_token: "Verify token (any word)" } },
  webex: { label: "Webex", hook: true, register: true,
    steps: ["At developer.webex.com → My Webex Apps → create a Bot and copy its token.", "Pick a webhook secret (any long word), save, then press 'Connect webhook'."],
    fields: { bot_token: "Bot token", secret: "Webhook secret" } },
  nostr: { label: "Nostr (encrypted DMs)",
    steps: ["Give your Brain its own Nostr private key (nsec… or hex); never reuse your personal one.", "It listens on the relays for encrypted DMs (NIP-04) and answers only people you approve."],
    fields: { private_key: "Brain's private key (nsec…)", relays: "Relays (wss://relay.damus.io, wss://nos.lol)" } },
});
["app_secret", "secret", "aes_key", "encrypt_key", "verification_token", "oa_secret", "auth_token", "api_key", "page_token",
 "bot_token", "private_key", "incoming_url"].forEach((k) => SECRET_FIELDS.add(k));

const _buildMore = buildMoreChannels;
buildMoreChannels = function () {
  const fresh = !$("#ch-more").dataset.built;
  _buildMore();
  if (!fresh) return;
  for (const [ch, info] of Object.entries(MORE_CHANNELS)) {
    if (!info.register) continue;
    const card = document.querySelector(`.ch-card[data-ch="${ch}"]`);
    const n = el("p", { class: "note" });
    card?.append(el("div", { class: "row" }, el("button", { type: "button", onclick: async () => {
      try { const r = await api(`/api/channels/${ch}/register-webhook`, { method: "POST" }); n.textContent = r.result; n.classList.remove("err"); }
      catch (e) { n.textContent = e.message; n.classList.add("err"); } } }, "Connect webhook")), n);
  }
};

// small helpers
const card = (title, hint, ...kids) => el("div", { class: "card" }, el("h2", {}, title), hint ? el("p", { class: "hint" }, hint) : null, ...kids);
const field = (label, input) => el("label", { class: "field" }, label, input);
const input = (id, ph = "", type = "text") => el("input", { id, placeholder: ph, type });
const noteEl = (id) => el("p", { class: "note", id });
function once(section, id, build) {
  if (document.getElementById(id)) return document.getElementById(id);
  const box = el("div", { id });
  build(box);
  $(section).append(box);
  return box;
}
async function tryNote(id, fn, ok) {
  try { const r = await fn(); if (ok) note(id, typeof ok === "function" ? ok(r) : ok); return r; }
  catch (e) { note(id, e.message, true); return null; }
}

// ---------- Goals and limits (Automations tab) ----------
let goalTimer = null;
function buildGoals(box) {
  box.append(card("Goals", "A bigger job it works on by itself, step by step with its tools, until done or the budget runs out. " +
    "While it runs you can steer it (\"only Malviya Nagar\"), pause or stop it. Or type /goal … --budget 40 in chat.",
    field("Goal", el("textarea", { id: "goal-text", rows: "2", placeholder: "Find 20 cafés in Jaipur without a website, with phone numbers, as a table." })),
    el("div", { class: "mgrid" },
      field("Budget: model requests", input("goal-req", "30", "number")),
      field("Budget: tokens", input("goal-tok", "200000", "number")),
      field("Tell me when done on", el("select", { id: "goal-deliver" }, ...["web", "telegram", "whatsapp", "discord", "slack", "email"].map((v) => el("option", { value: v }, v === "web" ? "this app" : v))))),
    el("div", { class: "row" }, el("button", { class: "primary", onclick: async () => {
      const text = $("#goal-text").value.trim();
      if (!text) return note("#goal-note", "Say what the goal is.", true);
      await tryNote("#goal-note", () => api("/api/goals", { method: "POST", body: { text, max_requests: +$("#goal-req").value || 30,
        max_tokens: +$("#goal-tok").value || 200000, deliver: $("#goal-deliver").value } }), "Started.");
      $("#goal-text").value = ""; loadGoals();
    } }, "Start goal")),
    noteEl("goal-note"), el("div", { id: "goal-list", class: "list" })));
  box.append(card("Daily limits", "A safety net for the whole Brain: once today's limit is reached it stops using models until tomorrow (0 = no limit).",
    el("div", { class: "mgrid" }, field("Model requests per day", input("lim-req", "0", "number")), field("Tokens per day", input("lim-tok", "0", "number"))),
    el("p", { class: "meta", id: "lim-today" }),
    el("div", { class: "row" }, el("button", { onclick: () => tryNote("#lim-note", () => api("/api/limits", { method: "PUT",
      body: { daily_requests: +$("#lim-req").value || 0, daily_tokens: +$("#lim-tok").value || 0 } }), "Saved.") }, "Save limits")),
    noteEl("lim-note")));
}

async function loadGoals() {
  const r = await api("/api/goals").catch(() => null);
  if (!r) return;
  if (document.activeElement?.id !== "lim-req") $("#lim-req").value = r.limits.daily_requests || 0;
  if (document.activeElement?.id !== "lim-tok") $("#lim-tok").value = r.limits.daily_tokens || 0;
  $("#lim-today").textContent = `Today so far: ${r.today.requests} requests, ${r.today.tokens.toLocaleString()} tokens.`;
  const list = $("#goal-list");
  if (list.contains(document.activeElement)) return;     // don't redraw while you're typing a steer note
  list.textContent = "";
  if (!r.goals.length) list.append(el("p", { class: "hint" }, "No goals yet."));
  for (const g of r.goals) {
    const ctl = (action, extra = {}) => api(`/api/goals/${g.id}`, { method: "POST", body: { action, ...extra } }).then(loadGoals).catch((e) => alert(e.message));
    const steer = el("input", { placeholder: "Steer it: e.g. only places with good ratings" });
    const pct = Math.min(100, Math.round(100 * g.requests / g.max_requests));
    list.append(el("div", { class: "item" },
      el("div", { class: "body" }, el("strong", {}, g.text)),
      el("div", { class: "meta" }, g.status, `${g.requests}/${g.max_requests} requests`, `${g.tokens.toLocaleString()} tokens`, `${g.rounds} rounds`),
      el("div", { class: "meter" }, el("span", { style: `width:${pct}%` })),
      g.result ? el("div", { class: "body" }, g.result) : null,
      g.log.length ? el("details", {}, el("summary", {}, "Progress"), el("div", { class: "goal-log" }, g.log.map((x) => x.text).join("\n\n"))) : null,
      ["running", "paused"].includes(g.status) ? el("div", { class: "row" }, steer,
        el("button", { onclick: () => steer.value.trim() && ctl("steer", { note: steer.value.trim() }) }, "Steer"),
        g.status === "running" ? el("button", { onclick: () => ctl("pause") }, "Pause") : el("button", { onclick: () => ctl("resume") }, "Resume"),
        el("button", { onclick: () => ctl("stop") }, "Stop")) : null,
      g.status === "over budget" ? el("div", { class: "row" }, el("button", { onclick: () => ctl("budget") }, "Add 20 requests and continue")) : null));
  }
}

// ---------- Gmail push (Automations tab) ----------
function buildGmail(box) {
  box.append(card("Gmail push alerts", "Gmail tells your Brain the moment an email arrives (instead of checking every 2 minutes). " +
    "Uses your own free Google Cloud project; the Brain only gets read access. It uses the email watcher's instructions above. " +
    "Needs your Brain online over HTTPS (server or tunnel).",
    el("ol", { class: "steps" },
      el("li", {}, "console.cloud.google.com → new project → enable Gmail API and Cloud Pub/Sub API."),
      el("li", {}, "Pub/Sub → create topic brain-gmail. On it, add gmail-api-push@system.gserviceaccount.com as Pub/Sub Publisher."),
      el("li", {}, "Create a Push subscription on the topic to: ", el("code", { class: "mono", id: "gm-push" }, "…")),
      el("li", {}, "Credentials → OAuth client ID (Web application) with redirect URL: ", el("code", { class: "mono", id: "gm-redirect" }, "…"),
        ". Add yourself as a test user on the consent screen.")),
    el("div", { class: "mgrid" },
      field("OAuth client ID", input("gm-id")), field("OAuth client secret", input("gm-secret", "", "password")),
      field("Topic", input("gm-topic", "projects/your-project/topics/brain-gmail"))),
    el("p", { class: "meta", id: "gm-state" }),
    el("div", { class: "row" },
      el("button", { onclick: () => tryNote("#gm-note", async () => { await api("/api/gmail", { method: "PUT",
        body: { client_id: $("#gm-id").value.trim(), client_secret: $("#gm-secret").value.trim(), topic: $("#gm-topic").value.trim() } }); loadGmail(); }, "Saved.") }, "Save"),
      el("button", { class: "primary", onclick: () => tryNote("#gm-note", async () => { const r = await api("/api/gmail/connect", { method: "POST" }); location.href = r.url; }) }, "Connect Gmail"),
      el("button", { onclick: () => tryNote("#gm-note", async () => { await api("/api/gmail/watch", { method: "POST" }); loadGmail(); }, "Watching your inbox.") }, "Renew watch"),
      el("button", { onclick: () => confirm("Disconnect Gmail?") && tryNote("#gm-note", async () => { await api("/api/gmail/disconnect", { method: "POST" }); loadGmail(); }, "Disconnected.") }, "Disconnect")),
    noteEl("gm-note")));
}
async function loadGmail() {
  const g = await api("/api/gmail").catch(() => null);
  if (!g) return;
  $("#gm-push").textContent = g.push_url; $("#gm-redirect").textContent = g.redirect_url;
  $("#gm-id").value = g.client_id; $("#gm-topic").value = g.topic;
  $("#gm-secret").placeholder = g.has_secret ? "saved (leave empty to keep)" : "";
  $("#gm-state").textContent = g.connected ? `Connected to ${g.address}${g.watch_until ? `, watching until ${g.watch_until}` : ", not watching yet (save a topic, then Renew watch)"}.` : "Not connected.";
  if (/gmail=connected/.test(location.hash)) note("#gm-note", "Gmail connected.");
  if (/gmail=failed/.test(location.hash)) note("#gm-note", "Connecting Gmail didn't work. Check the client ID, secret and redirect URL.", true);
}

const _autoLoad = loaders.automations;
loaders.automations = async () => {
  await _autoLoad();
  once("#tab-automations", "goals-box", buildGoals);
  once("#tab-automations", "gmail-box", buildGmail);
  loadGoals(); loadGmail();
  clearInterval(goalTimer);
  goalTimer = setInterval(() => { if (!$("#tab-automations").classList.contains("active")) return clearInterval(goalTimer); loadGoals(); }, 5000);
};

// ---------- Secrets vault, Chrome extension, memory engines (Settings tab) ----------
function buildVault(box) {
  box.append(card("Secrets vault", "Save passwords so the Brain can sign in for you without ever seeing them. It only writes a placeholder; " +
    "the real value is filled in inside the browser, only on the sites you list, over https. Every use is logged and notifies you. " +
    "You can also point to 1Password (op://…) or Bitwarden (item name) on this computer.",
    el("div", { class: "mgrid" },
      field("Name", input("vt-name", "github")),
      field("Sites it may be used on", input("vt-sites", "github.com")),
      field("Where it's kept", el("select", { id: "vt-source", onchange: () => {
        const local = $("#vt-source").value === "local";
        $("#vt-value").closest("label").hidden = !local; $("#vt-ref").closest("label").hidden = local;
      } }, el("option", { value: "local" }, "In the Brain (encrypted)"), el("option", { value: "1password" }, "1Password (op CLI)"), el("option", { value: "bitwarden" }, "Bitwarden (bw CLI)"))),
      field("Username (optional)", input("vt-user", "", "text")),
      field("Password or secret", input("vt-value", "", "password")),
      field("1Password reference / Bitwarden item", input("vt-ref", "op://Private/GitHub/password"))),
    el("div", { class: "row" }, el("button", { class: "primary", onclick: () => tryNote("#vt-note", async () => {
      await api("/api/vault", { method: "POST", body: { name: $("#vt-name").value.trim(), sites: $("#vt-sites").value, value: $("#vt-value").value,
        username: $("#vt-user").value, source: $("#vt-source").value, ref: $("#vt-ref").value.trim() } });
      ["#vt-value", "#vt-user"].forEach((s) => { $(s).value = ""; }); loadVault();
    }, "Saved. The Brain sees only its name.") }, "Save secret")),
    noteEl("vt-note"), el("div", { id: "vt-list", class: "list" })));
  box.querySelector("#vt-ref").closest("label").hidden = true;
}
async function loadVault() {
  const r = await api("/api/vault").catch(() => null);
  if (!r) return;
  const list = $("#vt-list"); list.textContent = "";
  for (const x of r.items) list.append(el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, x.name), el("code", { class: "mono" }, ` {{secret:${x.name}}}`)),
    el("div", { class: "meta" }, x.source, `on ${x.sites.join(", ")}`, `used ${x.uses}×`, x.last_used ? `last ${x.last_used}` : "",
      el("button", { class: "mini", onclick: () => confirm(`Delete ${x.name}?`) && api(`/api/vault/${encodeURIComponent(x.name)}`, { method: "DELETE" }).then(loadVault).catch((e) => note("#vt-note", e.message, true)) }, "Delete"))));
}

function buildExtension(box) {
  box.append(card("Chrome extension", "Lets the Brain use the tabs you're already signed in to (Gmail, CRM, banking is up to you) in Chrome, Edge or Brave, " +
    "only on the sites you allow in the extension. Passwords come only from the vault.",
    el("ol", { class: "steps" },
      el("li", {}, "Download the extension below and unzip it."),
      el("li", {}, "Open chrome://extensions, switch on Developer mode, press 'Load unpacked' and pick the unzipped folder."),
      el("li", {}, "Make an API token (Settings → API tokens), then click the extension's icon: enter this Brain's address and the token."),
      el("li", {}, "Add the sites it may use (each one asks Chrome for permission).")),
    el("p", { class: "meta", id: "ext-state" }),
    el("div", { class: "row" }, el("a", { class: "button", href: "/api/ext/download", download: "ai-brain-extension.zip" }, "Download the extension"),
      el("button", { onclick: loadExtension }, "Check connection"))));
}
async function loadExtension() {
  const s = await api("/api/ext/status").catch(() => null);
  if (s) $("#ext-state").textContent = s.connected ? `Connected: ${s.browser}, allowed sites: ${s.sites.join(", ") || "none yet"}.` : "Not connected.";
}

function buildEngines(box) {
  box.append(card("Memory engines and tracing", "Optional. The built-in memory works on its own; these add to it.",
    el("div", { class: "mgrid" },
      field("Vector search", el("select", { id: "me-vector" }, el("option", { value: "builtin" }, "Built-in (SQLite)"), el("option", { value: "lancedb" }, "LanceDB (faster with many memories)"))),
      field("Honcho address (self-hosted or cloud)", input("me-hurl", "https://api.honcho.dev or http://localhost:8000")),
      field("Honcho API key", input("me-hkey", "", "password")),
      field("Honcho workspace", input("me-hws", "ai-brain"))),
    el("div", { class: "switches" }, el("label", { class: "switch" }, el("span", {}, "Send chats to Honcho so it learns about you (experimental)"), el("input", { type: "checkbox", id: "me-honcho" }))),
    el("p", { class: "meta", id: "me-state" }),
    el("div", { class: "row" }, el("button", { onclick: () => tryNote("#me-note", async () => {
      await api("/api/memory-engines", { method: "PUT", body: { vector: $("#me-vector").value, honcho: $("#me-honcho").checked,
        honcho_url: $("#me-hurl").value.trim(), honcho_key: $("#me-hkey").value.trim(), honcho_workspace: $("#me-hws").value.trim() } });
      $("#me-hkey").value = ""; loadEngines();
    }, "Saved.") }, "Save engines")), noteEl("me-note")));
}
async function loadEngines() {
  const c = await api("/api/memory-engines").catch(() => null);
  if (!c) return;
  $("#me-vector").value = c.vector; $("#me-honcho").checked = c.honcho; $("#me-hurl").value = c.honcho_url; $("#me-hws").value = c.honcho_workspace;
  $("#me-hkey").placeholder = c.has_honcho_key ? "saved (leave empty to keep)" : "";
  $("#me-state").textContent = [c.lancedb_installed ? "LanceDB installed" : "LanceDB not installed (pip install lancedb)",
    c.honcho_installed ? "Honcho installed" : "Honcho not installed (pip install honcho-ai)",
    c.tracing.on ? `OpenTelemetry tracing to ${c.tracing.endpoint}` : "Tracing off (set BRAIN_OTEL_ENDPOINT)"].join(" · ");
}

const _setLoad = loaders.settings;
loaders.settings = async () => {
  await _setLoad();
  once("#tab-settings", "vault-box", buildVault);
  once("#tab-settings", "ext-box", buildExtension);
  once("#tab-settings", "engines-box", buildEngines);
  loadVault(); loadExtension(); loadEngines();
};

// ---------- Skill marketplace and coding workspaces (Skills tab) ----------
function buildMarket(box) {
  box.append(card("Skill marketplace", "Find skills others made. From ClawHub (OpenClaw's public registry, only skills it hasn't flagged) or this server's shelf. " +
    "Every skill you add arrives as 'suggested': read it, then switch it on above.",
    el("div", { class: "row" }, input("mk-q", "e.g. invoice follow-up, SEO audit, cold email"),
      el("button", { class: "primary", onclick: searchClawhub }, "Search ClawHub")),
    noteEl("mk-note"), el("div", { id: "mk-results", class: "list" }),
    el("h3", { style: "margin-top:18px" }, "This server's shelf"),
    el("div", { class: "row" }, el("select", { id: "mk-pub" }), el("button", { onclick: () => tryNote("#mk-note", async () => {
      const r = await api("/api/market/shelf/publish", { method: "POST", body: { name: $("#mk-pub").value } }); loadShelf();
      return r; }, (r) => r.status === "waiting" ? "Sent to the admin for approval." : "Published.") }, "Publish my skill")),
    el("div", { id: "mk-shelf", class: "list" })));
}
async function searchClawhub() {
  const q = $("#mk-q").value.trim(); if (!q) return;
  const box = $("#mk-results"); box.textContent = "Searching…";
  const r = await tryNote("#mk-note", () => api(`/api/market/clawhub?q=${encodeURIComponent(q)}`));
  box.textContent = "";
  if (!r) return;
  if (!r.results.length) box.append(el("p", { class: "hint" }, "Nothing found."));
  for (const x of r.results) box.append(el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, x.name), " ", x.summary),
    el("div", { class: "meta" }, x.owner ? `by ${x.owner}` : "", x.version ? `v${x.version}` : "", el("a", { href: x.url, target: "_blank", rel: "noopener" }, "View on ClawHub"),
      el("button", { class: "mini", onclick: () => tryNote("#mk-note", async () => { const a = await api("/api/market/clawhub/install", { method: "POST", body: { slug: x.slug } }); loaders.skills(); return a; },
        (a) => `Added ${a.added.join(", ")} as suggested. Read it before switching it on.`) }, "Add"))));
}
async function loadShelf() {
  const [r, sk] = await Promise.all([api("/api/market/shelf").catch(() => null), api("/api/skills").catch(() => null)]);
  const pub = $("#mk-pub"); pub.textContent = "";
  for (const s of (sk?.skills || sk || [])) if (s.name) pub.append(el("option", { value: s.name }, s.name));
  const box = $("#mk-shelf"); box.textContent = "";
  if (!r) return;
  if (!r.items.length) box.append(el("p", { class: "hint" }, "Nothing on the shelf yet."));
  for (const x of r.items) box.append(el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, x.name), " ", x.description),
    el("div", { class: "meta" }, `by ${x.publisher}`, x.status !== "approved" ? x.status : "", `${x.installs} installs`,
      el("button", { class: "mini", onclick: () => tryNote("#mk-note", () => api("/api/market/shelf/install", { method: "POST", body: { id: x.id } }).then((a) => { loaders.skills(); return a; }), "Added as suggested.") }, "Add"),
      r.admin && x.status === "waiting" ? el("button", { class: "mini", onclick: () => api("/api/market/shelf/moderate", { method: "POST", body: { id: x.id, approve: true } }).then(loadShelf) }, "Approve") : null,
      r.admin && x.status === "waiting" ? el("button", { class: "mini", onclick: () => api("/api/market/shelf/moderate", { method: "POST", body: { id: x.id, approve: false } }).then(loadShelf) }, "Reject") : null,
      x.mine || r.admin ? el("button", { class: "mini", onclick: () => api(`/api/market/shelf/${x.id}`, { method: "DELETE" }).then(loadShelf) }, "Remove") : null)));
}

function buildWorkspaces(box) {
  box.append(card("Code workspaces", "Git folders the Brain may work in (ask in chat: \"in my site workspace, fix the footer year\"). " +
    "It reads, searches and patches files, or makes a branch per task. Nothing is kept until you review the changes here; it never pushes.",
    el("div", { class: "mgrid" }, field("Name", input("ws-name", "my-site")), field("Clone from (optional)", input("ws-url", "https://github.com/you/repo.git"))),
    el("div", { class: "row" }, el("button", { class: "primary", onclick: () => tryNote("#ws-note", async () => {
      await api("/api/workspaces", { method: "POST", body: { name: $("#ws-name").value.trim(), url: $("#ws-url").value.trim() } });
      $("#ws-name").value = ""; $("#ws-url").value = ""; loadWorkspaces();
    }, "Workspace ready.") }, "Create workspace")),
    noteEl("ws-note"), el("div", { id: "ws-list", class: "list" }), el("div", { id: "ws-diff" })));
}
async function loadWorkspaces() {
  const r = await api("/api/workspaces").catch(() => null);
  const list = $("#ws-list"); list.textContent = "";
  if (!r) return;
  if (!r.workspaces.length) list.append(el("p", { class: "hint" }, "No workspaces yet."));
  for (const w of r.workspaces) list.append(el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, w.name), ` on ${w.branch}`),
    el("div", { class: "meta" }, w.changed ? `${w.changed} changed files` : "no changes",
      el("button", { class: "mini", onclick: () => showDiff(w.name) }, "Review changes"),
      el("button", { class: "mini", onclick: () => confirm(`Delete the workspace ${w.name} and its files?`) && api(`/api/workspaces/${w.name}`, { method: "DELETE" }).then(loadWorkspaces) }, "Delete"))));
}
function renderDiff(text) {
  const box = el("div", { class: "diff" });
  for (const line of text.split("\n")) {
    const cls = line.startsWith("diff --git") ? "file" : line.startsWith("@@") ? "hunk" : line.startsWith("+") && !line.startsWith("+++") ? "add"
      : line.startsWith("-") && !line.startsWith("---") ? "del" : "";
    box.append(el("div", cls ? { class: cls } : {}, line || " "));
  }
  return box;
}
async function showDiff(name) {
  const out = $("#ws-diff"); out.textContent = "Loading…";
  const r = await tryNote("#ws-note", () => api(`/api/workspaces/${name}/diff`));
  out.textContent = "";
  if (!r) return;
  const msg = el("input", { placeholder: "What changed (commit message)" });
  out.append(el("h3", {}, `Changes in ${name}`),
    r.diff.trim() ? renderDiff(r.diff) : el("p", { class: "hint" }, "No changes."),
    r.diff.trim() ? el("div", { class: "row" }, msg,
      el("button", { class: "primary", onclick: () => tryNote("#ws-note", async () => { const a = await api(`/api/workspaces/${name}/commit`, { method: "POST", body: { name, message: msg.value } }); showDiff(name); loadWorkspaces(); return a; }, (a) => a.result) }, "Keep (commit)"),
      el("button", { onclick: () => confirm("Throw away all these changes?") && tryNote("#ws-note", async () => { const a = await api(`/api/workspaces/${name}/discard`, { method: "POST", body: { name } }); showDiff(name); loadWorkspaces(); return a; }, (a) => a.result) }, "Discard")) : null);
}

const _skLoad = loaders.skills;
loaders.skills = async () => {
  await _skLoad();
  once("#tab-skills", "market-box", buildMarket);
  once("#tab-skills", "ws-box", buildWorkspaces);
  loadShelf(); loadWorkspaces();
};

// ---------- First start: ask the owner's name (the app ships without one) ----------
(async function askName() {
  const b = await api("/api/master/public").catch(() => null);
  if (!b || (b.owner_name || "").trim() || b.lock_app) return;
  const name = el("input", { placeholder: "Your name", "aria-label": "Your name" });
  const bar = el("form", { class: "card", style: "margin:12px 0" },
    el("h2", {}, "Welcome! What should I call you?"),
    el("p", { class: "hint" }, "Your Brain thinks and speaks like you, so it starts with your name. Then add a free FreeLLMAPI key in Settings."),
    el("div", { class: "row" }, name, el("button", { class: "primary", type: "submit" }, "Save")));
  bar.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!name.value.trim()) return;
    try { await api("/api/master", { method: "PUT", body: { changes: { owner_name: name.value.trim() } } }); bar.remove(); loadBrand(); }
    catch (err) { alert(err.message); }
  });
  $("#tab-chat").prepend(bar);
})();

// ---------- Backup and restore (Master tab) ----------
function buildBackup(box) {
  box.append(card("Backup and restore", "Your whole Brain in one encrypted file: memories, chats, identity, skills, settings, jobs and vault. " +
    "Keep it on Google Drive or a USB stick. Restoring it moves your Brain to another computer or server. Without the passphrase nobody can open it.",
    el("div", { class: "mgrid" }, field("Passphrase (8+ characters, write it down)", input("bk-pass", "", "password"))),
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: async () => {
        const pass = $("#bk-pass").value;
        if (pass.length < 8) return note("#bk-note", "Use at least 8 characters.", true);
        note("#bk-note", "Making the backup…");
        try {
          const res = await fetch("/api/backup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passphrase: pass }) });
          if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Error ${res.status}`);
          const url = URL.createObjectURL(await res.blob());
          const a = el("a", { href: url, download: `ai-brain-backup-${new Date().toISOString().slice(0, 10)}.brainbackup` });
          document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
          note("#bk-note", "Backup downloaded. Keep the file and the passphrase safe.");
        } catch (e) { note("#bk-note", e.message, true); }
      } }, "Download backup"),
      el("label", { class: "button", style: "cursor:pointer" }, "Restore from a backup…",
        el("input", { type: "file", accept: ".brainbackup", hidden: "", onchange: async (e) => {
          const f = e.target.files[0]; e.target.value = "";
          if (!f) return;
          const pass = $("#bk-pass").value;
          if (!pass) return note("#bk-note", "Type the backup's passphrase first.", true);
          if (!confirm("Replace this Brain with the backup? A copy of the current one is kept on this computer just in case.")) return;
          note("#bk-note", "Restoring…");
          const fd = new FormData(); fd.append("file", f);
          try {
            const res = await fetch("/api/backup/restore", { method: "POST", headers: { "X-Backup-Passphrase": pass }, body: fd });
            const r = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(r.detail || `Error ${res.status}`);
            note("#bk-note", `Restored ${r.files} files. Reloading…`); setTimeout(() => location.reload(), 1500);
          } catch (err) { note("#bk-note", err.message, true); }
        } }))),
    noteEl("bk-note")));
}
const _mLoad = loaders.master;
loaders.master = async () => { await _mLoad(); once("#tab-master", "backup-box", buildBackup); };

// ---------- Use from my phone (Tailscale) ----------
window.showRemoteLogin = function () {
  if (document.getElementById("remote-login")) return;
  const pw = el("input", { type: "password", placeholder: "Phone password", autocomplete: "current-password", "aria-label": "Phone password" });
  const msg = el("p", { class: "note" });
  const box = el("div", { id: "remote-login", class: "lock" },
    el("form", { class: "card", style: "max-width:360px;margin:15vh auto" },
      el("h2", {}, "Sign in to your Brain"),
      el("p", { class: "hint" }, "You're opening your Brain from another device. Enter the phone password you set on your computer."),
      el("div", { class: "row" }, pw, el("button", { class: "primary", type: "submit" }, "Sign in")), msg));
  box.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/remote/login", { method: "POST", body: { password: pw.value } }); location.reload(); }
    catch (err) { msg.textContent = err.message; msg.classList.add("err"); pw.select(); }
  });
  document.body.append(box);
  setTimeout(() => pw.focus(), 50);
};

function buildRemote(box) {
  box.append(card("Use from my phone", "Open this Brain from your phone or another laptop, privately. It uses Tailscale (free): " +
    "only your own devices can reach it, never the internet or your Wi-Fi neighbours, and it asks for a password. This computer must be on.",
    el("ol", { class: "steps" },
      el("li", {}, "Install Tailscale on this computer and on your phone (tailscale.com/download) and sign in to both with the same Google account."),
      el("li", {}, "Set a phone password below (10+ characters) and switch phone access on."),
      el("li", {}, "On your phone, open the address shown below and sign in. For the microphone and 'Add to Home Screen', use the https address: run ",
        el("code", { class: "mono" }, "tailscale serve --bg 8765"), " once on this computer.")),
    el("div", { class: "mgrid" }, field("Phone password", input("rm-pass", "at least 10 characters", "password"))),
    el("div", { class: "switches" }, el("label", { class: "switch" }, el("span", {}, "Phone access on"), el("input", { type: "checkbox", id: "rm-on" }))),
    el("p", { class: "meta", id: "rm-state" }),
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: () => tryNote("#rm-note", async () => {
        const body = { enabled: $("#rm-on").checked };
        if ($("#rm-pass").value) body.password = $("#rm-pass").value;
        await api("/api/remote", { method: "PUT", body }); $("#rm-pass").value = ""; loadRemote();
      }, "Saved.") }, "Save"),
      el("button", { onclick: () => confirm("Sign out every phone and laptop?") && tryNote("#rm-note", async () => { await api("/api/remote/logout-all", { method: "POST" }); loadRemote(); }, "Everyone is signed out.") }, "Sign out everywhere")),
    noteEl("rm-note")));
}
async function loadRemote() {
  const s = await api("/api/remote").catch(() => null);
  if (!s) return;
  if (!s.available) { $("#remote-box").hidden = true; return; }
  $("#rm-on").checked = s.enabled;
  $("#rm-pass").placeholder = s.has_password ? "saved (type a new one to change)" : "at least 10 characters";
  const parts = [];
  if (!s.tailscale_ip) parts.push("Tailscale isn't running on this computer yet.");
  else parts.push(`Tailscale is on (${s.tailscale_ip}).`);
  if (s.enabled && s.listening) parts.push(`Open on your phone: ${s.address}` + (s.https_address ? `  ·  or ${s.https_address} (after tailscale serve)` : ""));
  else if (s.enabled) parts.push("Waiting for Tailscale to connect…");
  if (s.sessions) parts.push(`${s.sessions} device(s) signed in.`);
  $("#rm-state").textContent = parts.join("  ");
}
const _mLoad2 = loaders.master;
loaders.master = async () => { await _mLoad2(); once("#tab-master", "remote-box", buildRemote); loadRemote(); };

// ---------- Offline AI (Settings tab) ----------
let offTimer = null;
function buildOffline(box) {
  box.append(card("Offline AI", "A free AI that runs on this computer, so your Brain keeps working with no internet. With internet it still uses your online models first (they're smarter).",
    el("ol", { class: "steps" },
      el("li", {}, "Install Ollama (free): ", el("a", { href: "https://ollama.com/download", target: "_blank", rel: "noopener" }, "ollama.com/download"), ", then open it once."),
      el("li", {}, "Pick a model and press Download (about 2 GB, once).")),
    el("div", { class: "row" }, el("select", { id: "off-model" }),
      el("button", { class: "primary", onclick: () => tryNote("#off-note", async () => { await api("/api/offline/pull", { method: "POST", body: { model: $("#off-model").value } }); loadOffline(); }, "Downloading…") }, "Download")),
    el("p", { class: "meta", id: "off-state" }), noteEl("off-note")));
}
async function loadOffline() {
  const s = await api("/api/offline").catch(() => null);
  if (!s) return;
  if (!s.available) { $("#offline-box").hidden = true; return; }
  const sel = $("#off-model");
  if (!sel.options.length) for (const [m, d] of Object.entries(s.choices)) sel.append(el("option", { value: m }, `${m} · ${d}`));
  const p = s.pull;
  const parts = [s.online ? "Online now." : "Offline now: answering with the model on this computer.",
    s.running ? `Ollama is running. Models here: ${s.models.join(", ") || "none yet"}.` : "Ollama isn't running.",
    s.in_order ? `Offline model: ${s.chosen}.` : ""];
  if (p.model && p.status !== "success" && !p.error) parts.push(`Downloading ${p.model}: ${p.total ? Math.round(100 * p.done / p.total) + "%" : p.status}`);
  if (p.error) parts.push(`Download failed: ${p.error}`);
  $("#off-state").textContent = parts.filter(Boolean).join("  ");
  clearTimeout(offTimer);
  if (p.model && p.status !== "success" && !p.error) offTimer = setTimeout(loadOffline, 2000);
}
const _setLoad2 = loaders.settings;
loaders.settings = async () => { await _setLoad2(); once("#tab-settings", "offline-box", buildOffline); loadOffline(); };

// ---------- Always listening for the activation word (Settings tab) ----------
let lsTimer = null;
function buildListener(box) {
  box.append(card("Activation word (this computer)", "Like Siri: your Brain listens all the time for your activation word, then does what you say, using your computer if you've allowed it in Control. " +
    "Speech is understood on this computer, offline. Nothing leaves it until it hears the activation word, and then only the words of your command. Change the activation word in Master → Wake words.",
    el("div", { class: "mgrid" }, field("Language", el("select", { id: "ls-lang" }, el("option", { value: "en" }, "English"), el("option", { value: "hi" }, "Hindi")))),
    el("div", { class: "switches" }, el("label", { class: "switch" }, el("span", {}, "Always listen for the activation word"), el("input", { type: "checkbox", id: "ls-on" }))),
    el("p", { class: "meta", id: "ls-state" }),
    el("div", { class: "row" }, el("button", { class: "primary", onclick: () => tryNote("#ls-note", async () => {
      await api("/api/listener", { method: "PUT", body: { enabled: $("#ls-on").checked, lang: $("#ls-lang").value } }); loadListener();
    }, "Saved.") }, "Save")), noteEl("ls-note")));
}
const LS_STATES = { off: "Off", loading: "Getting ready (first time: downloading the speech model, about 40 MB)…", waiting: "Listening for your activation word",
  listening: "Listening to your command…", thinking: "Working on it…", speaking: "Answering…" };
async function loadListener() {
  const s = await api("/api/listener").catch(() => null);
  if (!s) return;
  if (!s.available) { $("#listener-box").hidden = true; return; }
  if (!$("#ls-note").textContent) { $("#ls-on").checked = s.enabled; $("#ls-lang").value = s.lang; }
  $("#ls-state").textContent = !s.installed ? "Voice packages aren't installed here (the desktop app has them)."
    : [LS_STATES[s.state] || s.state, s.error, s.heard ? `Last heard: "${s.heard}"` : ""].filter(Boolean).join("  ·  ");
  clearTimeout(lsTimer);
  if ($("#tab-settings").classList.contains("active") && s.enabled) lsTimer = setTimeout(loadListener, 3000);
}
const _setLoad3 = loaders.settings;
loaders.settings = async () => { await _setLoad3(); once("#tab-settings", "listener-box", buildListener); loadListener(); };
