// Accounts (web version), the installable app, and the admin's user list.
// On your own laptop there are no accounts: this file then only offers "Install the app".

window.AUTH = { multiuser: false, user: null };

// ---------- Installable app ----------
let installPrompt = null;
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $("#a-install-btn").hidden = false;
});
window.addEventListener("appinstalled", () => {
  installPrompt = null;
  $("#a-install-btn").hidden = true;
  $("#a-install-hint").textContent = "Installed. Open Brain from your home screen, Start menu or Launchpad.";
});
$("#a-install-btn").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice.catch(() => null);
  installPrompt = null;
  $("#a-install-btn").hidden = true;
});
if (matchMedia("(display-mode: standalone)").matches || navigator.standalone) {
  $("#a-install").hidden = true;          // already running as the installed app
}

// ---------- Log in / sign up ----------
function authView(name) {
  ["login", "signup", "forgot", "reset"].forEach((v) => { $(`#auth-${v}`).hidden = v !== name; });
  note("#auth-note", "");
  const first = $(`#auth-${name} input`);
  setTimeout(() => first?.focus(), 30);
}

window.showAuth = (view) => {
  $("#auth").hidden = false;
  const a = window.AUTH;
  if (a.first_user) {
    $("#su-title").innerHTML = 'Set up your Brain<span class="dot">.</span>';
    $("#su-sub").textContent = "You're the first one here, so your account will be the admin of this Brain server.";
  }
  document.querySelector('[data-auth="signup"]').hidden = a.signup_open === false;
  view = view || (a.first_user ? "signup" : "login");
  authView(view);
};

document.querySelectorAll("[data-auth]").forEach((a) => a.addEventListener("click", (e) => {
  e.preventDefault();
  if (a.dataset.auth === "forgot" && window.AUTH.multiuser && !window.AUTH.email_reset) {
    authView("forgot");
    $("#fg-sub").textContent = "Password reset by email isn't set up on this server. Ask the admin to give you a temporary password.";
    return;
  }
  authView(a.dataset.auth);
}));

async function authPost(path, body, form) {
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    return await api(path, { method: "POST", body });
  } catch (err) {
    note("#auth-note", err.message, true);
    return null;
  } finally { btn.disabled = false; }
}

$("#auth-login").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (await authPost("/api/auth/login", { email: $("#li-email").value, password: $("#li-pass").value }, e.target)) {
    location.hash = ""; location.reload();
  }
});
$("#auth-signup").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (await authPost("/api/auth/signup", { name: $("#su-name").value, email: $("#su-email").value, password: $("#su-pass").value }, e.target)) {
    location.hash = ""; location.reload();
  }
});
$("#auth-forgot").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await authPost("/api/auth/forgot", { email: $("#fg-email").value }, e.target);
  if (r) note("#auth-note", r.message);
});
$("#auth-reset").addEventListener("submit", async (e) => {
  e.preventDefault();
  const token = decodeURIComponent(location.hash.slice("#reset=".length));
  if (await authPost("/api/auth/reset", { token, password: $("#rs-pass").value }, e.target)) {
    history.replaceState(null, "", "/");
    authView("login");
    note("#auth-note", "Password saved. Log in with your new password.");
  }
});

// Runs first. Returns true when the Brain can load (logged in, or the laptop version).
window.bootAuth = async () => {
  let me = { multiuser: false, user: null };
  try { me = await (await fetch("/api/auth/me")).json(); } catch { /* server not reachable: let the app show it */ }
  window.AUTH = me;
  document.documentElement.classList.toggle("web-version", !!me.multiuser);
  if (me.multiuser && location.hash.startsWith("#reset=")) { window.showAuth("reset"); return false; }
  if (me.multiuser && !me.user) { window.showAuth(); return false; }
  if (me.user) {
    const acct = $("#acct");
    acct.hidden = false;
    acct.textContent = me.user.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
    acct.title = `${me.user.name} · ${me.user.email}`;
    acct.addEventListener("click", () => { openTab("master"); setTimeout(() => $("#a-account").scrollIntoView({ behavior: "smooth" }), 50); });
  }
  if (location.hash === "#talk") setTimeout(() => $("#band-talk")?.click(), 600);
  return true;
};

// ---------- Account and admin cards (in Master Control) ----------
function renderAccount() {
  const me = window.AUTH;
  $("#a-account").hidden = !me.user;
  $("#a-admin").hidden = me.user?.role !== "admin";
  $("#m-lock-row").hidden = !!me.multiuser;          // logging in already protects the web version
  $("#m-lock-now-row").hidden = !!me.multiuser;
  if (!me.user) return;
  $("#a-who").textContent = `${me.user.name} · ${me.user.email}${me.user.role === "admin" ? " · admin" : ""}`;
  if (me.user.role === "admin") loadUsers();
}

const _loadMaster = loaders.master;
loaders.master = async () => { renderAccount(); await _loadMaster(); };

$("#a-pass").addEventListener("click", async () => {
  try {
    await api("/api/account/password", { method: "POST", body: { old: $("#a-old").value, new: $("#a-new").value } });
    $("#a-old").value = $("#a-new").value = "";
    note("#a-note", "Password changed. Other devices were logged out.");
  } catch (e) { note("#a-note", e.message, true); }
});
$("#a-logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  location.reload();
});
$("#a-logout-all").addEventListener("click", async () => {
  if (!confirm("Log out on every phone, tablet and computer?")) return;
  await api("/api/account/logout-all", { method: "POST" }).catch(() => {});
  location.reload();
});
$("#a-delete").addEventListener("click", async () => {
  const pw = prompt("This deletes your account and everything your Brain knows: memories, journal, chats, learned knowledge and settings. It can't be undone.\n\nDownload a backup first (Data card below).\n\nType your password to delete your account:");
  if (!pw) return;
  try {
    await api("/api/account/delete", { method: "POST", body: { password: pw } });
    alert("Your account has been deleted.");
    location.reload();
  } catch (e) { note("#a-note", e.message, true); }
});

async function loadUsers() {
  let r;
  try { r = await api("/api/admin/users"); } catch (e) { note("#a-admin-note", e.message, true); return; }
  $("#a-signup").checked = r.signup_open;
  const me = window.AUTH.user;
  $("#a-users").replaceChildren(...r.users.map((u) => {
    const actions = el("span", { class: "actions" });
    if (u.id !== me.id) {
      actions.append(
        el("button", { class: "mini", onclick: async () => {
          if (!confirm(`Give ${u.name} a temporary password? They'll be logged out everywhere.`)) return;
          const res = await api(`/api/admin/users/${u.id}/reset`, { method: "POST" });
          prompt(`Temporary password for ${u.email} (copy it and send it to them):`, res.temporary_password);
        } }, "Reset password"),
        el("button", { class: "mini", onclick: async () => {
          await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { changes: { disabled: !u.disabled } } }); loadUsers();
        } }, u.disabled ? "Switch on" : "Switch off"),
        el("button", { class: "mini", onclick: async () => {
          await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { changes: { role: u.role === "admin" ? "user" : "admin" } } }); loadUsers();
        } }, u.role === "admin" ? "Remove admin" : "Make admin"),
        el("button", { class: "mini danger", onclick: async () => {
          if (!confirm(`Delete ${u.name}'s account and their whole Brain? This can't be undone.`)) return;
          await api(`/api/admin/users/${u.id}`, { method: "DELETE" }); loadUsers();
        } }, "Delete"));
    }
    return el("div", { class: "item" },
      el("div", { class: "body" }, `${u.name} · ${u.email}`),
      el("div", { class: "meta" }, u.role, u.disabled ? "switched off" : "active",
        `joined ${fmtDate(u.created_at)}`, u.last_login ? `last login ${fmtDate(u.last_login)}` : "never logged in", actions));
  }));
}
$("#a-signup").addEventListener("change", async (e) => {
  try {
    await api("/api/admin/signup", { method: "POST", body: { open: e.target.checked } });
    note("#a-admin-note", e.target.checked ? "Anyone with the link can sign up." : "Sign-ups are closed.");
  } catch (err) { note("#a-admin-note", err.message, true); e.target.checked = !e.target.checked; }
});
