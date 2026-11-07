# Brain on your phone

## Android: the full app (activation word, phone control, offline)

**Get the app (free):** GitHub → Actions → **Build Android app** → the latest run → download **Brain-android** →
unzip → open `Brain.apk` on the phone (allow "install unknown apps" once). If you had the older Brain app, uninstall it first.

**Set it up (about 5 minutes), in the "Brain settings" app:**
1. **Brain address:** your server's `https://…` address, or, for the Brain on your own computer, the address shown in
   Master → **Use from my phone** (Tailscale: `http://100.x.x.x:8766`, or `https://<computer>.<tailnet>.ts.net`).
2. **API token:** make one in your Brain: Master → **API tokens**. Paste it and press **Save**.
3. **Activation word:** switch on "Always listen". It uses the activation words from Master → **Wake words**.
   For a name it may not know (like "Hey Jarvis"), press **Teach it my activation word** and say it once or twice.
4. **Phone control:** press the button, then Accessibility → **Brain** → On. Now your Brain can read the screen, tap,
   swipe, type, go Back/Home and open apps when you ask it to.
5. **Battery:** press the battery button and allow it, so it keeps listening with the screen off.

Then just say it: *"Hey Brain, open WhatsApp and message Amit that I'm running late."*

**With no internet** the phone still does: open apps, call contacts, prepare text messages, alarms, timers, torch,
volume, time, date and battery. Anything else is saved and done by your Brain as soon as the phone is online again
(you get a notification with the result).

**Privacy and safety**
- Speech is understood **on the phone** (Vosk, offline). Nothing leaves the phone until it hears your activation word;
  then only the words of your command go to your Brain.
- A notification shows while it listens, with **Stop listening**.
- Phone control can be switched off any time in Accessibility. Calls, messages and deleting wait for your OK in the
  Brain app; it never types passwords, OTPs, PINs or card numbers.
- After the phone restarts, Android 14 and newer need you to open the Brain app once before it can listen again.

**Battery:** always-on listening uses some battery (like any always-listening assistant). Turn it off in Brain settings
when you don't need it.

**Same signing key for updates (optional):** to install new builds over the old app without uninstalling, add two
repository secrets once: `BRAIN_KEYSTORE_B64` (a keystore made with
`keytool -genkeypair -keystore brain.jks -alias brain -keyalg RSA -validity 10000`, then `base64 -w0 brain.jks`) and
`BRAIN_KEYSTORE_PASSWORD`.

## iPhone: "Hey Siri, Ask Brain"

Apple doesn't let any app listen for its own activation word or control other apps on an iPhone. The closest thing,
free: a Siri Shortcut that sends what you say to your Brain and speaks the answer. Your Brain can still control your
computer and Android phone from there.

1. Open **Shortcuts** → **+** → name it **Ask Brain**.
2. Add **Dictate Text**.
3. Add **Get Contents of URL**:
   - URL: `https://YOUR-BRAIN/api/voice/command?format=text` (your server, or the `https://…ts.net` address from
     Use from my phone; the iPhone needs the Tailscale app too).
   - Method **POST**. Headers: `Authorization` = `Bearer YOUR-API-TOKEN`.
   - Request body **JSON**: `text` = *Dictated Text*, `device` = `siri`.
4. Add **Speak Text** with *Contents of URL*.
5. Say **"Hey Siri, Ask Brain"**, then your request.

You can also install the Brain web app (Safari → Share → Add to Home Screen) for chat, notifications and voice inside the app.

## Computers

- **Desktop app** (Windows, macOS, Linux): the whole Brain, plus the activation word (Settings → Activation word) and
  offline AI (Settings → Offline AI).
- **Brain Companion / tray app** (for a Brain on a server): lets it control that computer and listens for the activation
  word there.
