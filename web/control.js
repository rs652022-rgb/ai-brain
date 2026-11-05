// Device control: the Control tab, and live task cards (in Chat and in Control).

const TASK_END = new Set(["done", "failed", "stopped"]);
const STATE_LABEL = { running: "Working", waiting: "Needs you", done: "Done", failed: "Couldn't finish", stopped: "Stopped" };

window.stopDevices = async () => {
  try { await api("/api/control/stop", { method: "POST" }); } catch {}
  loadStatus();
};

function argText(args) {
  return Object.entries(args || {}).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
}

// A live view of one task. Polls until it finishes.
function taskView(id, opts = {}) {
  const box = el("div", { class: "task" });
  const head = el("div", { class: "task-head" });
  const body = el("div", { class: "task-body no-screen" });
  const steps = el("ol", { class: "task-steps" });
  const screen = el("div", { class: "task-screen" });
  const ask = el("div", {});
  const summary = el("p", { class: "task-summary" });
  const foot = el("div", { class: "task-foot" });
  body.append(steps, screen);
  box.append(head, body, ask, summary, foot);
  let askKey = "", shotKey = "", timer = null, gone = false;

  function renderAsk(t) {
    const p = t.pending;
    const key = p ? `${t.steps.length}:${p.kind}:${p.action || p.question}` : "";
    if (key === askKey) return;
    askKey = key;
    if (!p) { ask.replaceChildren(); return; }
    const reply = async (bodyData, btns) => {
      btns.forEach((b) => (b.disabled = true));
      try { await api(`/api/control/task/${id}/reply`, { method: "POST", body: bodyData }); } catch (e) { alert(e.message); }
      poll();
    };
    if (p.kind === "approve") {
      const why = el("input", { placeholder: "Optional: tell it what to do instead" });
      const yes = el("button", { type: "button" }, "Allow this step");
      const no = el("button", { type: "button", class: "ghost" }, "Don't allow");
      yes.addEventListener("click", () => reply({ approve: true }, [yes, no]));
      no.addEventListener("click", () => reply({ approve: false, answer: why.value }, [yes, no]));
      ask.replaceChildren(el("div", { class: "task-ask" },
        el("p", {}, `May I: ${p.label}?`),
        p.thought ? el("div", {}, p.thought) : null,
        el("pre", {}, `${p.action}  ${argText(p.args)}`),
        el("div", { class: "row" }, yes, no, why)));
      if (opts.spoken) window.brainSay?.(`May I ${p.label.toLowerCase()}? Please check the screen and press Allow.`);
    } else {
      const input = el("input", { placeholder: "Your answer" });
      const send = el("button", { type: "button" }, "Answer");
      const go = () => input.value.trim() && reply({ answer: input.value.trim() }, [send]);
      send.addEventListener("click", go);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      ask.replaceChildren(el("div", { class: "task-ask" }, el("p", {}, p.question), el("div", { class: "row" }, input, send)));
      if (opts.spoken) window.brainSay?.(p.question);
      setTimeout(() => input.focus(), 50);
    }
  }

  function render(t) {
    head.replaceChildren(el("span", { class: `task-state ${t.status}` }, STATE_LABEL[t.status] || t.status),
      el("strong", {}, t.goal),
      el("span", { class: "meta" }, `${t.device === "phone" ? "Phone" : "Computer"} · ${t.steps.length} steps`));
    steps.replaceChildren(...t.steps.map((s) => el("li", { class: s.state },
      el("code", {}, s.action), s.action === "done" || s.action === "fail" ? null : ` ${argText(s.args).slice(0, 140)}`,
      el("span", { class: "why" }, s.result.slice(0, 300)))));
    steps.scrollTop = steps.scrollHeight;
    if (t.screen) {
      const key = t.screen.data.length + t.screen.data.slice(-40);
      if (key !== shotKey) {
        shotKey = key;
        const img = el("img", { src: `data:${t.screen.mime};base64,${t.screen.data}`, alt: `What the ${t.screen.device === "phone" ? "phone" : "computer"} screen looks like now` });
        img.addEventListener("click", () => body.classList.toggle("big"));
        screen.replaceChildren(img, el("div", { class: "cap" }, `Latest ${t.screen.device === "phone" ? "phone" : "screen"} view${t.vision ? "" : " (model can't see images; using text)"}`));
      }
      body.classList.remove("no-screen");
    }
    renderAsk(t);
    summary.textContent = TASK_END.has(t.status) ? t.summary : "";
    foot.replaceChildren(...(TASK_END.has(t.status) ? [] : [el("button", { type: "button", class: "mini danger", onclick: window.stopDevices }, "Stop")]));
  }

  async function poll() {
    clearTimeout(timer);
    if (gone || !box.isConnected && box.dataset.seen) return;
    let t;
    try { t = await api(`/api/control/task/${id}`); } catch (e) { summary.textContent = e.message; return; }
    if (!box.dataset.seen && !TASK_END.has(t.status)) loadStatus();    // show "Using your devices · Stop" in the header
    box.dataset.seen = "1";
    render(t);
    if (TASK_END.has(t.status)) {
      gone = true;
      loadStatus();
      if (opts.spoken && t.summary) window.brainSay?.(t.summary);
      if (currentTab === "control") loadRecent();
      return;
    }
    timer = setTimeout(poll, t.status === "waiting" ? 1500 : 900);
  }
  poll();
  return box;
}

// ---------- The Control tab ----------
let C = null;

async function loadControl() {
  try { C = await api("/api/control"); } catch (e) { note("#c-note", e.message, true); return; }
  renderControl();
  loadRecent();
}
loaders.control = loadControl;

function renderControl() {
  const s = C.settings, st = C.status;
  $("#c-gate").hidden = !(C.pin_set && !C.unlocked);
  $("#c-pin-hint").hidden = C.pin_set;
  const on = s.enabled, anyDev = s.devices.pc || s.devices.phone;
  $("#c-state").classList.toggle("off", !on);
  $("#c-state-text").textContent = !on ? "Device control is off" : anyDev ? "Device control is on" : "On, but no device allowed yet";
  $("#c-state-hint").textContent = on ? [s.devices.pc && "computer", s.devices.phone && "phone"].filter(Boolean).join(" + ") : "";
  $("#c-power").textContent = on ? "Turn off" : "Turn on";
  $("#c-allow-pc").checked = s.devices.pc;
  $("#c-allow-phone").checked = s.devices.phone;

  $("#c-install-pc").hidden = st.pc_installed;
  $("#c-pc-status").textContent = st.pc_installed
    ? `Ready to control this ${st.system === "Darwin" ? "Mac" : st.system === "Windows" ? "Windows PC" : "computer"}.`
    : "PC control needs a small one-time install (about 10 MB).";

  $("#c-install-adb").hidden = st.adb_installed;
  let ph;
  if (st.remote) {                     // web version: the companion app is the Brain's hands
    $("#c-remote").hidden = false;
    $("#c-pc-title").textContent = "Your computer";
    $("#c-install-pc").hidden = true;
    $("#c-pc-status").textContent = st.connected
      ? `Connected: ${st.device_name} (${st.system === "Darwin" ? "Mac" : st.system})${st.pc_installed ? "." : ". Its screen-control packages are missing: restart the companion."}`
      : "No computer connected. Install and start the Brain Companion (see Your computers).";
    $("#c-install-adb").hidden = !st.connected || st.adb_installed;
    $("#c-install-adb").textContent = "Install ADB on my computer";
    $("#c-look-pc").disabled = !st.connected;
    $("#c-look-phone").disabled = !st.connected;
    loadCompanions();
  }
  if (st.remote && !st.connected) ph = "Connect your computer first; the phone plugs into it (or joins over Wi-Fi).";
  else if (!st.adb_installed) ph = "ADB isn't installed yet. Click Install ADB.";
  else if (st.phone_error) ph = st.phone_error;
  else if (!st.phones.length) ph = "No phone found yet. Plug it in (USB debugging on) or connect over Wi-Fi, then Refresh.";
  else ph = st.phones.map((p) => `${p.model || p.serial}: ${p.state === "device" ? "connected" : p.state === "unauthorized" ? "tap Allow on the phone" : p.state}`).join(" · ");
  $("#c-phone-status").textContent = ph;

  $("#c-levels").replaceChildren(...Object.entries(C.categories).map(([key, [label, desc]]) => {
    const seg = el("div", { class: "seg", role: "group", "aria-label": label });
    for (const [lv, text] of [["off", "Off"], ["ask", "Ask me"], ["auto", "Allowed"]]) {
      const b = el("button", { type: "button" }, text);
      b.classList.toggle("on", s.levels[key] === lv);
      if (lv === "auto" && C.always_ask.includes(key)) { b.disabled = true; b.title = "Always asks you first"; }
      b.addEventListener("click", () => { s.levels[key] = lv; renderControl(); });
      seg.append(b);
    }
    return el("div", { class: "level" }, el("div", {}, el("strong", {}, label), el("small", {}, desc)), seg);
  }));
  if (document.activeElement !== $("#c-folders")) $("#c-folders").value = (s.folders || []).join("\n");
  if (document.activeElement !== $("#c-max")) $("#c-max").value = s.max_steps;
}

async function saveControl(changes, msg = "Saved.") {
  try {
    const s = await api("/api/control", { method: "PUT", body: { changes } });
    C.settings = s; renderControl(); note("#c-note", msg); loadStatus();
    return true;
  } catch (e) {
    note("#c-note", e.message, true);
    if (/PIN/.test(e.message)) { C.unlocked = false; renderControl(); }
    loadControl();
    return false;
  }
}

$("#c-power").addEventListener("click", () => {
  const on = !C.settings.enabled;
  if (on && !confirm(`Turn on device control?\n\n${window.BRAND.brain_name} will be able to use the devices you allow, within the limits you set. You can stop it anytime by saying "stop" or pressing Stop.`)) return;
  saveControl({ enabled: on }, on ? "Device control is on." : "Device control is off.");
});
$("#c-stop").addEventListener("click", window.stopDevices);
$("#c-allow-pc").addEventListener("change", (e) => {
  if (e.target.checked && !confirm("Allow control of this computer?\n\nIt can move the mouse, type, open apps and websites, and (with your OK) change files and run commands. This stays allowed until you switch it off.")) { e.target.checked = false; return; }
  saveControl({ devices: { pc: e.target.checked }, ...(e.target.checked && !C.settings.enabled ? { enabled: true } : {}) },
    e.target.checked ? "This computer is allowed." : "This computer is no longer allowed.");
});
$("#c-allow-phone").addEventListener("change", (e) => {
  if (e.target.checked && !confirm("Allow control of your phone?\n\nIt can see the screen, tap, type and open apps. Calls and messages always ask you first. This stays allowed until you switch it off.")) { e.target.checked = false; return; }
  saveControl({ devices: { phone: e.target.checked }, ...(e.target.checked && !C.settings.enabled ? { enabled: true } : {}) },
    e.target.checked ? "Your phone is allowed." : "Your phone is no longer allowed.");
});
$("#c-save").addEventListener("click", () => saveControl({
  levels: C.settings.levels, folders: $("#c-folders").value, max_steps: Number($("#c-max").value) || 30 }));

async function install(component, btn, noteId) {
  btn.disabled = true; const old = btn.textContent; btn.textContent = "Installing… (1–2 minutes)";
  try {
    await api("/api/control/install", { method: "POST", body: { component } });
    note(noteId, component === "pc" ? "Installed. Try “See my screen”." : "ADB installed. Plug in your phone and press Refresh phones.");
  } catch (e) { note(noteId, e.message, true); }
  btn.disabled = false; btn.textContent = old; loadControl();
}
$("#c-install-pc").addEventListener("click", (e) => install("pc", e.target, "#c-pc-note"));
$("#c-install-adb").addEventListener("click", (e) => install("adb", e.target, "#c-phone-note"));
$("#c-phone-refresh").addEventListener("click", loadControl);

async function look(device, imgId, noteId, btn) {
  btn.disabled = true;
  try {
    const r = await api(`/api/control/look/${device}`);
    const img = $(imgId); img.src = r.image; img.hidden = false;
    note(noteId, `It can see your ${device === "pc" ? "screen" : "phone"} (${r.width}×${r.height} sent to the AI model).`);
  } catch (e) { note(noteId, e.message, true); }
  btn.disabled = false;
}
$("#c-look-pc").addEventListener("click", (e) => look("pc", "#c-pc-shot", "#c-pc-note", e.target));
$("#c-look-phone").addEventListener("click", (e) => look("phone", "#c-phone-shot", "#c-phone-note", e.target));

async function phoneConnect(address, code, btn) {
  btn.disabled = true;
  try {
    const r = await api("/api/control/phone/connect", { method: "POST", body: { address, code } });
    note("#c-phone-note", (r.message || "Done.") + (code ? " Now enter the IP:port from the Wireless debugging screen and press Connect." : ""));
  } catch (e) { note("#c-phone-note", e.message, true); }
  btn.disabled = false; loadControl();
}
$("#c-pair").addEventListener("click", (e) => phoneConnect($("#c-pair-addr").value.trim(), $("#c-pair-code").value.trim(), e.target));
$("#c-connect").addEventListener("click", (e) => phoneConnect($("#c-conn-addr").value.trim(), "", e.target));

async function runTask() {
  const goal = $("#c-goal").value.trim();
  if (!goal) return $("#c-goal").focus();
  try {
    const t = await api("/api/control/task", { method: "POST", body: { goal, device: $("#c-device").value } });
    $("#c-goal").value = "";
    $("#c-task").replaceChildren(taskView(t.id));
    $("#c-stop").hidden = false;
    loadStatus();
  } catch (e) { note("#c-note", e.message, true); }
}
$("#c-run").addEventListener("click", runTask);
$("#c-goal").addEventListener("keydown", (e) => { if (e.key === "Enter") runTask(); });

async function loadRecent() {
  let tasks = [];
  try { tasks = await api("/api/control/tasks"); } catch { return; }
  $("#c-stop").hidden = !tasks.some((t) => !TASK_END.has(t.status));
  $("#c-state").classList.toggle("busy", !$("#c-stop").hidden);
  $("#c-recent").replaceChildren(...(tasks.length ? tasks.map((t) => el("div", { class: "item" },
    el("div", { class: "body" }, t.goal),
    el("div", { class: "meta" }, el("span", { class: `task-state ${t.status}` }, STATE_LABEL[t.status] || t.status),
      new Date(t.started * 1000).toLocaleString(), `${t.steps.length} steps`, t.device === "phone" ? "phone" : "computer"),
    t.summary ? el("div", { class: "meta" }, t.summary) : null))
    : [el("p", { class: "hint" }, "No tasks yet. Tasks are kept until the app restarts; every step is also in the Activity log.")]));
}

async function controlGateUnlock() {
  try {
    await api("/api/master/unlock", { method: "POST", body: { pin: $("#c-gate-pin").value } });
    $("#c-gate-pin").value = ""; loadControl();
  } catch (e) { note("#c-gate-note", e.message, true); }
}
$("#c-gate-go").addEventListener("click", controlGateUnlock);
$("#c-gate-pin").addEventListener("keydown", (e) => { if (e.key === "Enter") controlGateUnlock(); });

// ---------- Web version: linked computers (Brain Companion) ----------
async function loadCompanions() {
  let list = [], dl = { files: [], script_zip: "" };
  try { [list, dl] = await Promise.all([api("/api/companion/devices"), api("/api/downloads")]); } catch { return; }
  const links = [...dl.files.map((f) => el("a", { href: f.url, download: "" }, f.name.replace("BrainCompanion-", "").replace(/\.(exe|zip)$/, "") + ` (${Math.round(f.size / 1e6)} MB)`)),
                 el("a", { href: dl.script_zip, download: "" }, dl.files.length ? "Python version (any system)" : "Brain Companion (Windows, Mac, Linux; needs Python)")];
  $("#c-dl").replaceChildren(...links.flatMap((a, i) => (i ? [" · ", a] : [a])));
  $("#c-companions").replaceChildren(...(list.length ? list.map((d) => el("div", { class: "item" },
    el("div", { class: "body" }, d.name),
    el("div", { class: "meta" }, el("span", { class: `task-state ${d.online ? "done" : "stopped"}` }, d.online ? "Online" : "Offline"),
      `linked ${fmtDate(d.created)}`, d.last_seen ? `last seen ${fmtDate(d.last_seen)}` : "",
      el("span", { class: "actions" }, el("button", { class: "mini danger", onclick: async () => {
        if (!confirm(`Unlink ${d.name}? It won't be able to connect until you pair it again.`)) return;
        await api(`/api/companion/devices/${d.id}`, { method: "DELETE" }); loadControl();
      } }, "Unlink")))))
    : [el("p", { class: "hint" }, "No computers linked yet.")]));
}

$("#c-pair-code-btn").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    const r = await api("/api/companion/code", { method: "POST" });
    const box = $("#c-pair-out");
    box.hidden = false;
    box.replaceChildren(
      el("div", {}, el("span", { class: "code" }, "Web address"), el("strong", { class: "mono" }, r.server)),
      el("div", {}, el("span", { class: "code" }, "Pairing code"), el("strong", { class: "mono big" }, r.code.replace(/(.{4})/, "$1-"))),
      el("p", { class: "hint" }, "Type both into the companion within 10 minutes. The code works once."));
  } catch (err) { note("#c-remote-note", err.message, true); }
  e.target.disabled = false;
});
