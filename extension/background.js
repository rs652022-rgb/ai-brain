// AI Brain extension: a live link from this browser to your Brain.
// It only acts on sites you allowed (each one is a Chrome permission you approve in the popup).
let ws = null, state = "off", lastError = "";

async function cfg() {
  const c = await chrome.storage.local.get(["brainUrl", "token", "sites", "paused"]);
  return { brainUrl: c.brainUrl || "", token: c.token || "", sites: c.sites || [], paused: !!c.paused };
}

function hostOf(url) { try { return new URL(url).hostname.toLowerCase(); } catch { return ""; } }
function siteAllowed(url, sites) {
  const u = (() => { try { return new URL(url); } catch { return null; } })();
  if (!u || !/^https?:$/.test(u.protocol)) return false;
  const h = u.hostname.toLowerCase();
  return sites.some((s) => h === s || h.endsWith("." + s));
}

async function connect() {
  const c = await cfg();
  if (!c.brainUrl || !c.token || c.paused) { state = c.paused ? "paused" : "off"; return; }
  if (ws && (ws.readyState === 0 || ws.readyState === 1)) return;
  const url = c.brainUrl.replace(/\/$/, "").replace(/^http/, "ws") + "/api/ext/ws";
  try { ws = new WebSocket(url); } catch (e) { lastError = String(e); return; }
  state = "connecting";
  ws.onopen = () => ws.send(JSON.stringify({ type: "hello", token: c.token, browser: navigator.userAgent.match(/(Edg|Chrome|Brave)\/[\d.]+/)?.[0] || "Chrome", sites: c.sites }));
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "welcome") { state = "on"; lastError = ""; return; }
    if (msg.type === "error") { lastError = msg.error; state = "off"; return; }
    if (!msg.id) return;
    let reply;
    try { reply = { id: msg.id, ok: true, result: await handle(msg) }; }
    catch (e) { reply = { id: msg.id, ok: false, error: String(e.message || e) }; }
    try { ws.send(JSON.stringify(reply)); } catch {}
  };
  ws.onclose = () => { state = state === "paused" ? "paused" : "off"; ws = null; };
  ws.onerror = () => { lastError = "Can't reach your Brain at " + c.brainUrl; };
}

// Keep the link up (and the service worker awake while connected).
chrome.alarms.create("brain-keepalive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "ping" }));
  else connect();
});
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.storage.onChanged.addListener(() => { if (ws) ws.close(); setTimeout(connect, 300); });
chrome.runtime.onMessage.addListener((m, _s, send) => {
  if (m === "status") send({ state, lastError });
  if (m === "reconnect") { if (ws) ws.close(); setTimeout(connect, 200); send({}); }
  return true;
});
connect();

async function tabFor(msg, sites) {
  const tab = await chrome.tabs.get(Number(msg.tab)).catch(() => null);
  if (!tab) throw new Error("No such tab. Use op tabs to list them.");
  if (!siteAllowed(tab.url, sites)) throw new Error(`The Brain may not use ${hostOf(tab.url) || "this page"}. Allow the site in the extension first.`);
  return tab;
}

function run(tabId, func, args) {
  return chrome.scripting.executeScript({ target: { tabId }, func, args }).then((r) => r[0]?.result);
}

// ---- functions that run inside the page (each must be self-contained) ----
function pageRead() {
  const els = [...document.querySelectorAll("a,button,input,textarea,select,[role=button],[contenteditable=true]")]
    .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).slice(0, 80);
  els.forEach((e, i) => e.setAttribute("data-brain-ix", String(i)));
  const lines = els.map((e, i) => {
    const t = (e.innerText || (e.type === "password" ? "" : e.value) || e.placeholder || e.getAttribute("aria-label") || e.name || "").trim().slice(0, 60);
    return `[${i}] ${e.tagName.toLowerCase()}${e.type ? "[" + e.type + "]" : ""}: ${t}`;
  });
  return { url: location.href, title: document.title, text: (document.body?.innerText || "").slice(0, 8000), elements: lines.join("\n") };
}
function pageAct(op, target, text, secret, lockSites) {
  const find = (target) => {
    let el = /^\d+$/.test(String(target)) ? document.querySelector(`[data-brain-ix="${target}"]`) : null;
    if (!el) { try { el = document.querySelector(target); } catch {} }
    if (!el) {
      const t = String(target).toLowerCase();
      el = [...document.querySelectorAll("a,button,[role=button],input,textarea,label")]
        .find((e) => (e.innerText || e.placeholder || e.getAttribute("aria-label") || "").toLowerCase().includes(t));
    }
    return el;
  };
  if (secret) {
    const h = location.hostname.toLowerCase();
    if (location.protocol !== "https:" || !lockSites.some((s) => h === s || h.endsWith("." + s)))
      return { error: "The page changed to a site this secret isn't allowed on, so I didn't type it." };
  }
  const el = target !== undefined && target !== null && target !== "" ? find(target) : document.activeElement;
  if (!el) return { error: `Couldn't find ${target} on the page.` };
  if (/card|cc-|cvv|cvc/i.test((el.autocomplete || "") + " " + (el.name || "") + " " + (el.id || "")))
    return { error: "I don't type into card fields." };
  if (op === "click") { el.scrollIntoView({ block: "center" }); el.click(); return { done: "clicked" }; }
  if (op === "type") {
    if (el.type === "password" && !secret) return { error: "Passwords only come from the vault." };
    el.focus();
    if (el.isContentEditable) { document.execCommand("insertText", false, text); }
    else {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return { done: "typed" };
  }
  if (op === "press") {
    for (const type of ["keydown", "keypress", "keyup"]) el.dispatchEvent(new KeyboardEvent(type, { key: text, bubbles: true }));
    if (text === "Enter" && el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    return { done: "pressed " + text };
  }
  return { error: "unknown" };
}

async function settle(tabId) {
  for (let i = 0; i < 20; i++) {
    const t = await chrome.tabs.get(tabId);
    if (t.status === "complete") return;
    await new Promise((r) => setTimeout(r, 250));
  }
}

async function handle(msg) {
  const { sites } = await cfg();
  switch (msg.op) {
    case "tabs": {
      const tabs = await chrome.tabs.query({});
      return { tabs: tabs.filter((t) => siteAllowed(t.url || "", sites)).map((t) => ({ id: t.id, title: t.title, url: t.url, active: t.active })),
               hidden: tabs.filter((t) => !siteAllowed(t.url || "", sites)).length };
    }
    case "open": {
      if (!siteAllowed(msg.url || "", sites)) throw new Error(`The Brain may not open ${hostOf(msg.url) || msg.url}. Allow the site in the extension first.`);
      const t = await chrome.tabs.create({ url: msg.url, active: false });
      await settle(t.id);
      return { tab: t.id, ...(await run(t.id, pageRead, [])) };
    }
    case "read": { const t = await tabFor(msg, sites); return { tab: t.id, ...(await run(t.id, pageRead, [])) }; }
    case "click": case "type": case "press": {
      const t = await tabFor(msg, sites);
      const r = await run(t.id, pageAct, [msg.op, msg.target ?? "", msg.op === "press" ? (msg.key || msg.text || "Enter") : (msg.text || ""), !!msg.secret, msg.lock_sites || []]);
      if (r?.error) throw new Error(r.error);
      await new Promise((res) => setTimeout(res, 700));
      await settle(t.id);
      const after = await chrome.tabs.get(t.id);
      if (!siteAllowed(after.url, sites)) return { tab: t.id, done: r.done, url: after.url, text: "(The page moved to a site the Brain may not read.)" };
      return { tab: t.id, done: r.done, ...(await run(t.id, pageRead, [])) };
    }
    case "scroll": {
      const t = await tabFor(msg, sites);
      await run(t.id, (d) => window.scrollBy(0, d === "up" ? -800 : 800), [msg.direction || "down"]);
      return { tab: t.id, ...(await run(t.id, pageRead, [])) };
    }
    case "screenshot": {
      const t = await tabFor(msg, sites);
      await chrome.tabs.update(t.id, { active: true });
      await new Promise((r) => setTimeout(r, 400));
      const shot = await chrome.tabs.captureVisibleTab(t.windowId, { format: "jpeg", quality: 60 });
      return { tab: t.id, shot: shot.split(",")[1] };
    }
    case "close": { const t = await tabFor(msg, sites); await chrome.tabs.remove(t.id); return { done: "closed" }; }
  }
  throw new Error("Unknown action");
}
