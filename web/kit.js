// Agent kit screens: question cards, the plan checklist and undo points under replies, the Board tab
// with past-chat search, undo points for code workspaces, skill ratings, safety findings and the
// "skills it writes switch on by themselves" setting. Loaded after more.js (uses card, field, once, tryNote).

// ---------- Under each reply ----------
const TODO_MARK = { pending: "○", in_progress: "◐", done: "●" };

function questionCard(q) {
  const box = el("div", { class: "kit-question" }, el("strong", {}, q.question));
  if (!q.options?.length) return box.append(el("p", { class: "hint" }, "Type your answer below.")), box;
  const row = el("div", { class: "row" });
  for (const opt of q.options) {
    row.append(el("button", { class: "mini", onclick: () => {
      row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      sendChat(null, { text: opt });
    } }, opt));
  }
  box.append(row);
  return box;
}

function todoList(items) {
  const done = items.filter((t) => t.status === "done").length;
  return el("details", { class: "kit-todos", open: "" },
    el("summary", {}, `Plan: ${done} of ${items.length} done`),
    ...items.map((t) => el("div", { class: `kit-todo ${t.status}` }, `${TODO_MARK[t.status] || "○"} ${t.text}`)));
}

function undoNotice(ids) {
  const last = ids[ids.length - 1];
  const btn = el("button", { class: "mini", onclick: async () => {
    if (!confirm("Put the files back the way they were before this change?")) return;
    btn.disabled = true;
    try {
      const r = await api(`/api/checkpoints/${encodeURIComponent(last)}/restore`, { method: "POST" });
      btn.textContent = `Restored ${r.restored.length} file(s)`;
    } catch (e) { btn.textContent = e.message; btn.disabled = false; }
  } }, "Undo these file changes");
  return el("div", { class: "meta" }, `Saved ${ids.length} undo point(s) before changing files.`, btn);
}

function forgedNotice(list) {
  return el("div", { class: "meta" }, ...list.map((f) =>
    `New skill ${f.skill}: ${f.status === "active" ? "switched on" : "waiting for you in Skills"}` +
    (f.findings?.length ? ` (safety check flagged ${f.findings.length} thing(s))` : "")));
}

const _kitReplyExtras = replyExtras;
// eslint-disable-next-line no-global-assign
replyExtras = function (r) {
  const extra = _kitReplyExtras(r);
  if (r.todos?.length) extra.prepend(todoList(r.todos));
  if (r.question) extra.prepend(questionCard(r.question));
  if (r.checkpoints?.length) extra.append(undoNotice(r.checkpoints));
  if (r.skills_forged?.length) extra.append(forgedNotice(r.skills_forged));
  return extra;
};

// ---------- Board tab ----------
SHEETS.board = ["Board", "Work that lasts longer than one chat. You and the Brain both add cards; only you delete them."];
const COL_NAMES = { todo: "To do", doing: "Doing", done: "Done", blocked: "Blocked" };

function boardCard(c, columns) {
  const move = el("select", { "aria-label": "Move card", onchange: () => patchCard(c.id, { column: move.value }) },
    ...columns.map((col) => el("option", { value: col }, COL_NAMES[col] || col)));
  move.value = c.column;
  return el("div", { class: "item kit-card" },
    el("div", { class: "body" }, el("strong", {}, c.title), c.notes ? el("p", { class: "hint" }, c.notes) : null),
    el("div", { class: "meta" }, c.created_by === "brain" ? "added by the Brain" : "added by you", move,
      el("button", { class: "mini danger", onclick: () => confirm(`Delete "${c.title}"?`) &&
        api(`/api/board/${c.id}`, { method: "DELETE" }).then(loadBoard) }, "Delete")));
}

async function patchCard(id, body) {
  await tryNote("#bd-note", () => api(`/api/board/${id}`, { method: "PATCH", body }));
  loadBoard();
}

async function loadBoard() {
  const r = await tryNote("#bd-note", () => api("/api/board"));
  if (!r) return;
  $("#bd-cols").replaceChildren(...r.columns.map((col) => el("div", { class: "kit-col" },
    el("h3", {}, `${COL_NAMES[col] || col} (${r.cards[col]?.length || 0})`),
    ...(r.cards[col] || []).map((c) => boardCard(c, r.columns)))));
}

async function addCard() {
  const title = $("#bd-title").value.trim();
  if (!title) return note("#bd-note", "Give the card a title.", true);
  const ok = await tryNote("#bd-note", () => api("/api/board", { method: "POST",
    body: { title: title.slice(0, 200), notes: $("#bd-notes").value.trim().slice(0, 2000), column: "todo" } }), "Card added.");
  if (ok) { $("#bd-title").value = ""; $("#bd-notes").value = ""; loadBoard(); }
}

function buildBoard(box) {
  box.append(card("Task board", "Ask in chat: \"put 'renew domain' on my board\". Change a card's column to move it.",
    el("div", { class: "mgrid" }, field("Title", input("bd-title", "Renew the domain")),
      field("Notes (optional)", input("bd-notes", "Before 1 December"))),
    el("div", { class: "row" }, el("button", { class: "primary", onclick: addCard }, "Add card")),
    noteEl("bd-note"), el("div", { id: "bd-cols", class: "kit-cols" })));
  box.append(card("Search past chats", "Finds words from any earlier conversation, not only this one.",
    el("form", { class: "row", onsubmit: (e) => { e.preventDefault(); searchChats(); } },
      input("cs-q", "e.g. invoice, Jaipur, password reset"), el("button", { class: "primary", type: "submit" }, "Search")),
    noteEl("cs-note"), el("div", { id: "cs-results", class: "list" })));
}

async function searchChats() {
  const q = $("#cs-q").value.trim().slice(0, 300);
  if (!q) return;
  const r = await tryNote("#cs-note", () => api(`/api/chats/search?q=${encodeURIComponent(q)}&limit=30`));
  if (!r) return;
  const list = $("#cs-results");
  list.replaceChildren(...(r.hits.length ? r.hits.map(hitRow) : [el("p", { class: "hint" }, "Nothing found.")]));
}

function hitRow(h) {
  const more = el("div", {});
  const open = el("button", { class: "mini", onclick: async () => {
    open.disabled = true;
    const r = await tryNote("#cs-note", () => api(`/api/chats/around/${h.id}`));
    if (r) more.replaceChildren(...r.messages.map((m) => el("p", { class: m.id === h.id ? "kit-hit" : "" },
      el("strong", {}, m.role === "user" ? "You: " : "Brain: "), String(m.content || "").slice(0, 600))));
  } }, "Show around it");
  return el("div", { class: "item" }, el("div", { class: "body" }, h.snippet),
    el("div", { class: "meta" }, `${h.role === "user" ? "you" : "the Brain"} · ${fmtDate(h.created_at)} · ${h.conversation}`, open), more);
}

(function addBoardTab() {
  const nav = $("#tabs");
  if (!nav || document.querySelector('#tabs button[data-tab="board"]')) return;
  const btn = el("button", { "data-tab": "board" }, "Board");
  btn.addEventListener("click", () => openTab("board"));
  nav.insertBefore(btn, nav.querySelector('button[data-tab="skills"]'));
  const sec = el("section", { id: "tab-board", class: "tab" });
  $("#tab-skills").before(sec);
  loaders.board = () => { once("#tab-board", "board-box", buildBoard); loadBoard(); };
})();

// ---------- Skills tab: ratings, safety findings, undo points, auto-skills ----------
async function loadLedger() {
  const r = await api("/api/skills-ledger").catch(() => null);
  if (!r) return;
  for (const item of $$("#sk-list .item")) {
    const name = item.querySelector("strong")?.textContent;
    if (!name || item.querySelector(".kit-rate")) continue;
    const x = r.ledger[name] || { uses: 0, good: 0, bad: 0 };
    const weak = r.needs_work.includes(name);
    item.append(el("div", { class: "meta kit-rate" }, `used ${x.uses}x · 👍 ${x.good} · 👎 ${x.bad}`,
      weak ? el("span", { class: "task-state waiting" }, "Needs work") : null,
      el("button", { class: "mini", title: "It worked well", onclick: () => rateSkill(name, true) }, "👍"),
      el("button", { class: "mini", title: "It didn't work well", onclick: () => rateSkill(name, false) }, "👎"),
      item.textContent.includes("Suggested") ? scanButton(name, item) : null));
  }
}

async function rateSkill(name, good) {
  await api(`/api/skills/${encodeURIComponent(name)}/rate`, { method: "POST", body: { good } }).catch(() => null);
  $$("#sk-list .kit-rate").forEach((n) => n.remove());
  loadLedger();
}

function scanButton(name, item) {
  const b = el("button", { class: "mini", onclick: async () => {
    b.disabled = true;
    const r = await api(`/api/skills/${encodeURIComponent(name)}/scan`).catch((e) => ({ error: e.message }));
    const text = r.error ? r.error : r.findings.length
      ? "Safety check: " + r.findings.map((f) => `${f.issue} ("${f.excerpt}")`).join("; ")
      : "Safety check: nothing unsafe found.";
    item.append(el("p", { class: r.findings?.length ? "note err" : "note" }, text));
  } }, "Safety check");
  return b;
}

function buildKitSkills(box) {
  const sw = el("input", { type: "checkbox", id: "ks-auto" });
  sw.addEventListener("change", () => tryNote("#ks-note", () => api("/api/kit-settings", { method: "PUT", body: { auto_skills: sw.checked } }),
    (r) => r.auto_skills ? "Skills it writes now switch on when the safety check is clean." : "Skills it writes now wait for you."));
  box.append(card("Skills it writes itself", "After a hard task the Brain can save how it did it as a skill, or improve a skill you rated badly. " +
    "Your own skills are never changed: it suggests a new version instead. Every skill gets a safety check.",
    el("label", { class: "switch" }, el("span", {}, "Switch on its new skills by itself",
      el("small", {}, "Only when the safety check finds nothing. Off: every new skill waits for you.")), sw),
    noteEl("ks-note")));
  box.append(card("Undo points", "Before it changes a file in a code workspace, the Brain saves a copy. Restore one to put those files back.",
    el("div", { id: "cp-list", class: "list" })));
}

async function loadKitSkills() {
  const s = await api("/api/kit-settings").catch(() => null);
  if (s) $("#ks-auto").checked = s.auto_skills;
  const r = await api("/api/checkpoints?limit=20").catch(() => null);
  if (!r) return;
  $("#cp-list").replaceChildren(...(r.checkpoints.length ? r.checkpoints.map((c) => el("div", { class: "item" },
    el("div", { class: "body" }, el("strong", {}, c.workspace), ` · ${c.reason} · ${c.files.join(", ")}`),
    el("div", { class: "meta" }, fmtDate(c.created_at), el("button", { class: "mini", onclick: () =>
      confirm(`Put ${c.files.length} file(s) in ${c.workspace} back to this point?`) &&
      tryNote("#ks-note", () => api(`/api/checkpoints/${encodeURIComponent(c.id)}/restore`, { method: "POST" }),
        (x) => `Restored ${x.restored.length} file(s).`) }, "Restore"))))
    : [el("p", { class: "hint" }, "No undo points yet.")]));
}

const _kitSkLoad = loaders.skills;
loaders.skills = async () => {
  await _kitSkLoad();
  once("#tab-skills", "kit-skills-box", buildKitSkills);
  loadLedger(); loadKitSkills();
};
