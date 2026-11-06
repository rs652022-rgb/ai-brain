// Capacitor settings for the Brain Android app: it opens your Brain's web address in a full-screen app.
const [url, name] = process.argv.slice(2);
const host = new URL(url).host;
console.log(JSON.stringify({
  appId: "app.aibrain.brain",
  appName: name || "Brain",
  webDir: "www",
  server: { url, cleartext: false, allowNavigation: [host] },
  android: { allowMixedContent: false, captureInput: true },
}, null, 2));
