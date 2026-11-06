// Channels, Automations, Skills & tools: the OpenClaw-style features.

const DELIVER_LABEL = { web: "This app's chat", telegram: "Telegram", discord: "Discord", slack: "Slack", email: "Email", none: "Nowhere (just run it)" };
const STATE_TEXT = { running: "Running", error: "Problem", off: "Off", incomplete: "Needs setup", starting: "Starting…" };

// ---------- Channels ----------
let CH = null;

async function loadChannels() {
  try { CH = await api("/api/channels"); } catch (e) { note("#ch-note", e.message, true); return; }
  document.querySelectorAll(".ch-card").forEach((card) => {
    const ch = card.dataset.ch, cfg = CH.config[ch], st = CH.status[ch];
    card.querySelectorAll("[data-k]").forEach((inp) => {
      const k = inp.dataset.k;
      if (inp.type === "checkbox") inp.checked = !!cfg[k];
      else if (inp.type === "password") { inp.value = ""; inp.placeholder = cfg[`has_${k}`] ? "•••••• saved (leave empty to keep)" : (inp.placeholder || ""); }
      else if (document.activeElement !== inp) inp.value = cfg[k] || "";
    });
    const tag = $(`#st-${ch}`);
    tag.textContent = `${STATE_TEXT[st.state] || st.state}${st.detail ? ` · ${st.detail}` : ""}`;
    tag.className = `ch-state ${st.state}`;
  });
  const pending = CH.senders.filter((x) => x.status === "pending");
  $("#ch-pending").replaceChildren(...(pending.length ? pending.map((x) => el("div", { class: "item" },
    el("div", { class: "body" }, `${x.name || x.sender} on ${x.channel}`),
    el("div", { class: "meta" }, el("span", { class: "mono" }, x.code), fmtDate(x.created_at),
      el("span", { class: "actions" },
        el("button", { class: "mini", onclick: () => approveSender(x.code, true) }, "Approve"),
        el("button", { class: "mini danger", onclick: () => approveSender(x.code, false) }, "Block")))))
    : [el("p", { class: "hint" }, "Nobody is waiting.")]));
  const others = CH.senders.filter((x) => x.status !== "pending");
  $("#ch-allowed").replaceChildren(...(others.length ? others.map((x) => el("div", { class: "item" },
    el("div", { class: "body" }, `${x.name || x.sender}`),
    el("div", { class: "meta" }, x.channel, x.status, x.chat ? "linked" : "not messaged yet",
      el("span", { class: "actions" },
        x.status === "allowed" && x.chat ? el("button", { class: "mini", onclick: async () => {
          try { await api(`/api/channels/test/${x.channel}`, { method: "POST" }); note("#ch-save-note", `Sent a test message on ${x.channel}.`); }
          catch (e) { note("#ch-save-note", e.message, true); }
        } }, "Send test") : null,
        el("button", { class: "mini danger", onclick: async () => { await api(`/api/channels/senders/${x.id}`, { method: "DELETE" }); loadChannels(); } }, "Remove")))))
    : [el("p", { class: "hint" }, "No one yet.")]));
}
loaders.channels = loadChannels;

async function approveSender(code, allow) {
  try { await api("/api/channels/approve", { method: "POST", body: { code, allow } }); note("#ch-note", allow ? "Approved." : "Blocked."); }
  catch (e) { note("#ch-note", e.message, true); }
  loadChannels();
}
$("#ch-approve").addEventListener("click", () => $("#ch-code").value.trim() && approveSender($("#ch-code").value.trim(), true));

$("#ch-save").addEventListener("click", async (e) => {
  const changes = {};
  document.querySelectorAll(".ch-card").forEach((card) => {
    const vals = {};
    card.querySelectorAll("[data-k]").forEach((inp) => { vals[inp.dataset.k] = inp.type === "checkbox" ? inp.checked : inp.value.trim(); });
    changes[card.dataset.ch] = vals;
  });
  e.target.disabled = true;
  try { await api("/api/channels", { method: "PUT", body: { changes } }); note("#ch-save-note", "Saved. Connecting…"); }
  catch (err) { note("#ch-save-note", err.message, true); }
  e.target.disabled = false;
  setTimeout(loadChannels, 2500);
});
$("#ch-email-allow").addEventListener("click", async () => {
  try {
    await api("/api/channels/allow", { method: "POST", body: { channel: "email", sender: $("#ch-email-add").value } });
    $("#ch-email-add").value = ""; loadChannels();
  } catch (e) { note("#ch-save-note", e.message, true); }
});

// ---------- Automations ----------
let AU = null;

function fillDeliver(sel, value) {
  sel.replaceChildren(...AU.deliver.map((d) => el("option", { value: d }, DELIVER_LABEL[d] || d)));
  sel.value = value || "web";
}

async function loadAutomations() {
  try { AU = await api("/api/automations"); } catch (e) { note("#au-note", e.message, true); return; }
  ["#au-deliver", "#wh-deliver"].forEach((id) => fillDeliver($(id), $(id).value));
  const hb = AU.heartbeat;
  fillDeliver($("#hb-deliver"), hb?.deliver);
  $("#hb-on").checked = !!hb?.enabled;
  $("#hb-min").value = hb ? parseInt(hb.schedule.split(" ")[1], 10) : 60;
  if (document.activeElement !== $("#hb-list")) $("#hb-list").value = hb?.prompt || AU.default_checklist;
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (!AU.timezone && tz) api("/api/master", { method: "PUT", body: { changes: { timezone: tz } } }).catch(() => {});
  const jobs = AU.jobs.filter((j) => j.kind !== "heartbeat");
  $("#au-list").replaceChildren(...(jobs.length ? jobs.map((j) => el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, j.name), ` · ${j.prompt.slice(0, 160)}`),
    el("div", { class: "meta" }, j.kind === "webhook" ? "webhook" : j.schedule, `→ ${DELIVER_LABEL[j.deliver] || j.deliver}`,
      j.kind !== "webhook" ? (j.next_run ? `next ${new Date(j.next_run).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}` : "finished") : "",
      j.last_run ? `last ${fmtDate(j.last_run)}` : "never run", j.enabled ? "" : "paused",
      el("span", { class: "actions" },
        el("button", { class: "mini", onclick: async (ev) => {
          ev.target.disabled = true; ev.target.textContent = "Running…";
          try { const r = await api(`/api/automations/${j.id}/run`, { method: "POST" }); note("#au-note", r.quiet ? "Ran: nothing needed your attention." : `Ran: ${r.output.slice(0, 200)}`); }
          catch (e) { note("#au-note", e.message, true); }
          loadAutomations();
        } }, "Run now"),
        el("button", { class: "mini", onclick: async () => { await api(`/api/automations/${j.id}`, { method: "PATCH", body: { changes: { enabled: !j.enabled } } }); loadAutomations(); } }, j.enabled ? "Pause" : "Resume"),
        el("button", { class: "mini danger", onclick: async () => { if (confirm(`Delete "${j.name}"?`)) { await api(`/api/automations/${j.id}`, { method: "DELETE" }); loadAutomations(); } } }, "Delete"))),
    j.last_result ? el("div", { class: "meta" }, `Last answer: ${j.last_result.slice(0, 200)}`) : null))
    : [el("p", { class: "hint" }, "Nothing scheduled yet.")]));
}
loaders.automations = loadAutomations;

$("#au-add").addEventListener("click", async () => {
  try {
    const j = await api("/api/automations", { method: "POST", body: { name: $("#au-name").value, prompt: $("#au-prompt").value,
      when: $("#au-when").value, deliver: $("#au-deliver").value, tz: Intl.DateTimeFormat().resolvedOptions().timeZone } });
    note("#au-note", `Scheduled. First run: ${new Date(j.next_run).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}.`);
    $("#au-name").value = $("#au-prompt").value = $("#au-when").value = "";
    loadAutomations();
  } catch (e) { note("#au-note", e.message, true); }
});
$("#hb-save").addEventListener("click", async () => {
  try {
    await api("/api/automations/heartbeat", { method: "POST", body: { enabled: $("#hb-on").checked, minutes: Number($("#hb-min").value) || 60,
      checklist: $("#hb-list").value, deliver: $("#hb-deliver").value } });
    note("#hb-note", $("#hb-on").checked ? "Heartbeat on." : "Heartbeat off.");
    loadAutomations();
  } catch (e) { note("#hb-note", e.message, true); }
});
$("#wh-add").addEventListener("click", async () => {
  try {
    const r = await api("/api/automations/webhook", { method: "POST", body: { name: $("#wh-name").value || "Webhook", prompt: $("#wh-prompt").value, deliver: $("#wh-deliver").value } });
    const box = $("#wh-out");
    box.hidden = false;
    box.replaceChildren(el("div", {}, el("span", { class: "code" }, "Secret address (shown once)"), el("strong", { class: "mono" }, r.url)),
      el("p", { class: "hint" }, "Send a POST with JSON to this address. Anyone with it can trigger the job, so keep it private; delete the job to revoke it."));
    loadAutomations();
  } catch (e) { note("#wh-note", e.message, true); }
});

// ---------- Skills, tools, dreams ----------
async function loadSkills() {
  let skills = [], tl = null, dreams = [];
  try { [skills, tl, dreams] = await Promise.all([api("/api/skills"), api("/api/tools"), api("/api/dreams")]); }
  catch (e) { note("#sk-note", e.message, true); return; }
  $("#sk-list").replaceChildren(...(skills.length ? skills.map((s) => el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, s.name), ` · ${s.description}`),
    el("details", {}, el("summary", {}, "Instructions"), el("pre", { class: "skill-body" }, s.body)),
    el("div", { class: "meta" }, s.status === "suggested" ? el("span", { class: "task-state waiting" }, "Suggested") : s.status,
      s.origin === "self-learning" ? "learned by itself" : "",
      el("span", { class: "actions" },
        el("button", { class: "mini", onclick: async () => { await api(`/api/skills/${s.name}`, { method: "PATCH", body: { status: s.status === "active" ? "off" : "active" } }); loadSkills(); } },
          s.status === "active" ? "Switch off" : "Switch on"),
        el("button", { class: "mini", onclick: () => { $("#sk-name").value = s.name; $("#sk-desc").value = s.description; $("#sk-body").value = s.body; $("#sk-name").scrollIntoView({ behavior: "smooth" }); } }, "Edit"),
        el("button", { class: "mini danger", onclick: async () => { if (confirm(`Delete the skill ${s.name}?`)) { await api(`/api/skills/${s.name}`, { method: "DELETE" }); loadSkills(); } } }, "Delete")))))
    : [el("p", { class: "hint" }, "No skills yet.")]));
  $("#tl-list").replaceChildren(...tl.tools.map((t) => {
    const c = el("input", { type: "checkbox", "data-tool": t.name }); c.checked = t.on;
    return el("label", { class: "switch" }, el("span", {}, t.name.replace(/_/g, " "),
      el("small", {}, t.description + (t.ready ? "" : " (not set up yet)"))), c);
  }));
  $("#tl-think").value = tl.thinking;
  $("#dr-list").replaceChildren(...(dreams.length ? dreams.map((d) => el("div", { class: "item" },
    el("div", { class: "meta" }, d.day, d.suggested ? `${d.suggested} facts suggested` : ""), el("div", { class: "body" }, d.diary)))
    : [el("p", { class: "hint" }, "No dreams yet. They appear after a day of conversation.")]));
}
loaders.skills = loadSkills;

$("#sk-save").addEventListener("click", async () => {
  try {
    await api("/api/skills", { method: "POST", body: { name: $("#sk-name").value, description: $("#sk-desc").value, body: $("#sk-body").value } });
    note("#sk-note", "Saved. Your Brain will use it when a task matches.");
    $("#sk-name").value = $("#sk-desc").value = $("#sk-body").value = "";
    loadSkills();
  } catch (e) { note("#sk-note", e.message, true); }
});
$("#tl-save").addEventListener("click", async () => {
  const off = [...document.querySelectorAll("[data-tool]")].filter((c) => !c.checked).map((c) => c.dataset.tool);
  try { await api("/api/tools", { method: "PUT", body: { off, thinking: $("#tl-think").value } }); note("#tl-note", "Saved."); }
  catch (e) { note("#tl-note", e.message, true); }
});
$("#dr-run").addEventListener("click", async (e) => {
  e.target.disabled = true; note("#dr-note", "Dreaming…");
  try { const d = await api("/api/dreams/run", { method: "POST" }); note("#dr-note", `Done. ${d.suggested} facts suggested for your approval.`); loadSkills(); }
  catch (err) { note("#dr-note", err.message, true); }
  e.target.disabled = false;
});
