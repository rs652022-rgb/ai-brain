// Makes the Brain installable and opens it even without internet (your data stays on the server).
// Pages and app files: network first (so updates show at once), cached copy when offline.
// API calls are never cached.
const CACHE = "brain-shell-v4";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/control.js", "/static/voice.js", "/static/account.js", "/static/agentui.js", "/static/extras.js", "/static/more.js",
  "/manifest.webmanifest", "/static/icons/icon-192.png", "/static/icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/download/")) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/")))
  );
});

// Notifications from your Brain (reminders, heartbeat alerts, approvals), even when the app is closed.
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch { d = { title: "Brain", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Brain", {
    body: d.body || "", icon: "/static/icons/icon-192.png", badge: "/static/icons/icon-192.png", data: { url: d.url || "/" } }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = e.notification.data?.url || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    const w = wins.find((x) => x.url.startsWith(self.location.origin));
    return w ? w.focus().then(() => w.navigate(url)) : self.clients.openWindow(url);
  }));
});
