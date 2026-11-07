const $ = (id) => document.getElementById(id);
const clean = (s) => { s = s.trim().toLowerCase(); try { if (s.includes("://")) s = new URL(s).hostname; } catch {} return s.split("/")[0].replace(/^\*\./, ""); };

async function render() {
  const c = await chrome.storage.local.get(["brainUrl", "token", "sites", "paused"]);
  $("url").value = c.brainUrl || ""; $("token").value = c.token || "";
  $("pause").textContent = c.paused ? "Resume" : "Pause";
  const ul = $("sites"); ul.textContent = "";
  for (const s of c.sites || []) {
    const li = document.createElement("li"); li.textContent = s;
    const b = document.createElement("button"); b.textContent = "✕";
    b.onclick = async () => {
      const sites = (c.sites || []).filter((x) => x !== s);
      await chrome.permissions.remove({ origins: [`https://${s}/*`, `https://*.${s}/*`, `http://${s}/*`] }).catch(() => {});
      await chrome.storage.local.set({ sites }); render();
    };
    li.append(b); ul.append(li);
  }
  chrome.runtime.sendMessage("status", (st) => { $("state").textContent = st ? (st.state === "on" ? "· connected" : `· ${st.state}${st.lastError ? " – " + st.lastError : ""}`) : ""; });
}

async function allow(host) {
  host = clean(host);
  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/.test(host) && host !== "localhost") return alert("Enter a site like mail.google.com");
  const ok = await chrome.permissions.request({ origins: [`https://${host}/*`, `https://*.${host}/*`, `http://${host}/*`] });
  if (!ok) return;
  const { sites = [] } = await chrome.storage.local.get("sites");
  if (!sites.includes(host)) sites.push(host);
  await chrome.storage.local.set({ sites }); render();
}

$("save").onclick = async () => {
  const brainUrl = $("url").value.trim().replace(/\/$/, "");
  const origin = new URL(brainUrl).origin + "/*";
  await chrome.permissions.request({ origins: [origin] }).catch(() => {});
  await chrome.storage.local.set({ brainUrl, token: $("token").value.trim(), paused: false });
  chrome.runtime.sendMessage("reconnect"); setTimeout(render, 1200);
};
$("pause").onclick = async () => { const { paused } = await chrome.storage.local.get("paused"); await chrome.storage.local.set({ paused: !paused }); render(); };
$("add").onclick = () => allow($("site").value);
$("addcur").onclick = async () => { const [t] = await chrome.tabs.query({ active: true, currentWindow: true }); if (t?.url) allow(new URL(t.url).hostname); };
render();
