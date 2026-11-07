# AI Brain

**A private AI that learns to think and speak like you.** It remembers what you tell it, learns from the web when you ask,
answers in your own voice, works in 27 chat apps, runs scheduled jobs and goals, uses your browser and computer when you
allow it, and runs entirely on free models (FreeLLMAPI pools many free providers behind one key).

Open source under the MIT license. Free to run: no subscriptions needed.

**Get it**
- **Desktop app** (Windows, macOS, Linux): Releases, or Actions → "Build desktop app" → Artifacts. See `desktop/README.md`.
- **Web app with accounts** for you, your team or clients: see `DEPLOY.md` (free on Oracle Cloud or your own PC).
- **Android app**: `mobile/README.md`. iPhone: install the web app from Safari (Share → Add to Home Screen).
- **From source**: `start.bat` (Windows) or `./start.sh` (macOS/Linux).

Contributions welcome: see `CONTRIBUTING.md`. Security reports: `SECURITY.md`.

## What it does

The core: a memory, an identity and a voice.

- **Chat** in three modes: Mirror (speaks as you, to you), Assistant, and Coach
- **Memory system**: events, facts, how-tos and current context, with hybrid search (meaning + words + recency)
- **Daily journal** and **weekly life interviews** (24 deep questions) that build your memory
- **Identity File**: your traits, values, style, decision rules and care preferences, with version history
- **Approval queue**: facts the Brain notices in chat or your journal wait for your OK before being saved
- **Learning, only when you tell it**: research a topic on the web, read a link, learn from PDFs and Word files, follow rules you teach it, and re-check topics on a schedule you set
- **Web search in chat** when you ask, with source links
- **Voice**: talk to it and hear it answer, hands-free conversation, and a wake word ("hey brain")
- **WhatsApp import**: turns your own messages into memories and writing samples (other people's messages are not stored)
- **Safety core** and an **append-only activity log** that the Brain cannot edit or delete

- **Device control** (off until you switch it on): it operates your **computer** (mouse, keyboard, screen, apps, files, commands) and your **Android phone** (taps, typing, apps, calls and messages), with per-action permissions, approvals and an instant stop

It never spends money, sees your passwords, types card details, or solves CAPTCHAs.

## Two ways to run it

- **On your laptop** (below): private, just for you, nothing to host.
- **As a web app for everyone**: people sign up and log in from any phone, tablet or computer, and each gets their own private Brain. It installs like a normal app (Android, iPhone, iPad, Windows, Mac) and needs no app store. The **Brain Companion** app lets each person's Brain use their own computer and phone. Free hosting guide: **[DEPLOY.md](DEPLOY.md)** (Oracle Cloud Always Free, or your own PC with a free Cloudflare Tunnel).

## Setup (about 10 minutes, free)

### 1. Install Python
Python 3.10 or newer from https://www.python.org/downloads/ (on Windows, tick "Add Python to PATH").

### 2. Start it
- **Windows:** double-click `start.bat`
- **Mac/Linux:** run `./start.sh`

The first start installs everything into a `.venv` folder, then opens http://127.0.0.1:8765.

### 3. Add a free AI model in the Settings tab
Pick one or more. Each takes about two minutes to get a key:

| Provider | Free? | Get a key | Notes |
|---|---|---|---|
| Google Gemini | Free tier | https://aistudio.google.com/apikey | Good all-rounder; also gives free search-by-meaning. Free-tier data may be used by Google to improve its products. |
| Groq | Free tier | https://console.groq.com/keys | Very fast. About 30 requests/minute, 1,000/day. |
| OpenRouter | Free models | https://openrouter.ai/keys | One key, hundreds of models. Use `openrouter/free` or any model ending in `:free`. |
| Mistral | Free plan | https://console.mistral.ai/api-keys | |
| Cerebras | Free tier | https://cloud.cerebras.ai | Very fast. |
| GitHub Models | Free | https://github.com/settings/tokens | Token needs the "models" permission. |
| Ollama | Free, local | https://ollama.com | Nothing leaves your laptop. Run `ollama pull llama3.2` and `ollama pull nomic-embed-text`. |
| Claude, OpenAI, DeepSeek | Paid | | Stronger models when you want them. |
| Custom | Any | | Any OpenAI-compatible API (LM Studio, Together, vLLM…): paste its base URL. |

In Settings: **Add provider → paste key → Test → Save settings.** Use **Load models** to pick from the provider's live model list, since model names change often.

**Auto-find free models:** after adding keys, click **🔍 Auto-find free models** in Settings (it also runs once by itself when a new key is added). It lists each provider's models, keeps only free chat models, tests them, and saves the best working one plus up to three backups. If a model is busy or out of quota, the Brain switches to a backup automatically and skips the busy one for a few minutes.

**FreeLLMAPI:** if you run FreeLLMAPI (a self-hosted gateway that pools many free providers), add the **FreeLLMAPI** provider, paste its unified key, and keep the model as `auto`.

**Tip: add two or three free providers.** The Brain tries them top to bottom. When one hits its free daily limit, the next one answers automatically, so you rarely notice limits.

## Your first week

1. **Day 1:** Open **Identity** and replace every "Fill in" and "Example" line. Paste 5–10 real messages you wrote under `writing_samples`. This matters more than anything else.
2. **Every day:** a 2–5 minute **Journal** entry.
3. **Every few days:** answer 3–5 **Interview** questions.
4. **Once:** **Import** 2–3 WhatsApp chats where you talk naturally (export "Without media").
5. Check **Memories** for facts waiting for approval.
6. Chat in **Mirror** mode and correct it when it gets you wrong. Corrections become memories.

## Master Control (you're in charge)

Open the **Master** tab to change anything about your Brain:

- **Identity:** give it any name (for example "Jarvis"), set your own name, the greeting on the chat screen, the line under it, the quick prompts, and which mode it starts in. In the greeting, `{first}` becomes your first name and `{name}` the Brain's name.
- **Commands:** your own **wake** words ("hey jarvis, utho jarvis"), **rest** words ("go to sleep, so jao") and **stop** words ("stop, ruko"). Say or type them. While resting, it doesn't answer, research or learn, and costs nothing. "Hey jarvis, what's my plan?" wakes it and answers in one go. With **Wake word** on in Chat, it listens for your wake words hands-free; after you tell it to rest during a voice conversation, it goes back to waiting for them.
- **Switches:** turn web access, learning, voice and fact suggestions on or off.
- **Look:** Blueprint (light) or Midnight (dark) theme, and any signal colour.
- **Security:** set a **Master PIN**. Once set, the PIN is needed to change Master Control and Settings (API keys). Turn on **Lock the whole app** to ask for the PIN before showing anything. Only a salted hash of the PIN is stored; after 5 wrong tries it waits a minute. Forgot it? Stop the app and delete the `"master"` section's `pin_hash` from `data/settings.json`.
- **Data:** download a full backup (memories, journal, chats, learned knowledge, Identity File). API keys and the PIN are left out.

## Agent features (inspired by OpenClaw)

**FreeLLMAPI, one free key for everything.** Run [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) (free, open source) and add your free provider keys in its dashboard. Then in Settings → AI models, paste its address and unified key and click **Use FreeLLMAPI**. Chat, seeing screenshots, search-by-meaning, web search, images and voice then all go through it. It switches provider by itself when one hits its limit. Routing choices:
- `auto`: best available
- `auto:fast`: fastest
- `auto:smart`: smartest available
- `fusion`: several models answer and one combines them

**Tools while answering.** The Brain can search the web (when you allow it), read a page, look through your memories, suggest a fact (you approve it), make an image, schedule a job, use your devices, message you on your linked apps, and follow your skills. Switch any of them off in Skills → Tools.

**Slash commands:**

| Command | What it does |
|---|---|
| `/help` | Lists the commands |
| `/new` | Starts a fresh chat; the old one is kept |
| `/status` | Shows models, memory, channels and jobs |
| `/model auto:fast` | Changes the model |
| `/think high` | Thinks harder before answering |
| `/btw …` | Asks a side question that isn't saved |
| `/compact` | Summarises a long chat |
| `/search …` | Searches past chats |
| `/skills`, `/jobs` | Lists your skills and scheduled jobs |

Long chats are summarised automatically, so nothing important is lost.

**Channels (Telegram, Discord, Slack, email).** Talk to your Brain from your phone's chat apps.
- An unknown person who messages your bot gets a pairing code, and nothing happens until you approve it in **Channels**.
- Email only answers addresses you add.
- In group chats it answers only approved people who mention it.
- Direct messages share your main chat with the web app.
- Telegram voice notes are transcribed.

**Automations:**
- **Scheduled jobs** ("weekdays at 8am give me my top 3 priorities"), delivered to the chat, Telegram, Discord, Slack or email.
- **Heartbeat:** checks your checklist every so often and messages you only when something needs attention.
- **Webhooks:** let a website form, Zapier or n8n trigger the Brain. Their data is treated as information, never as instructions.

**Skills and self-learning.** Write a skill ("how I write client proposals") in the Skills tab. After multi-step tasks the Brain may suggest a new skill, but a suggestion does nothing until you switch it on.

**Dreaming.** Each night it looks back over the day, writes a short diary entry, and suggests facts to remember. They go to Memories for your approval.

## More features (Phases 2 and 3)

**27 chat apps.**
- Telegram, Discord, Slack, email.
- WhatsApp (Meta's official Cloud API).
- Signal (through signal-cli-rest-api).
- Matrix, Mattermost, IRC, Twitch chat.
- SMS (Twilio) and LINE.
- WeChat, WeCom, Feishu/Lark, DingTalk, QQ, Zalo, Nextcloud Talk, Synology Chat, Rocket.Chat, Zulip, Viber, Facebook Messenger, Instagram DMs, Webex and Nostr (encrypted DMs).

WhatsApp, SMS and LINE send messages to your Brain's web address, so it must be online over HTTPS. Every message they send is checked against the service's signature.

**Agents.** Extra assistants inside one Brain, each with its own personality, model, tools and chat apps. For example: a formal "Sales" agent on WhatsApp running `auto:fast`. Pick an agent above the chat, or bind it to a chat app.

**Helpers.** For big research the Brain splits the work across up to 4 helpers working at the same time, then combines their results.

**Attachments.** Attach photos (the model looks at them), PDFs, Word or text files, and voice recordings. Their content is treated as information, never as instructions.

**Media.**
- Images and short videos through FreeLLMAPI.
- More web search engines: Exa, Perplexity and Firecrawl.

**MCP.** Connect outside tool servers (GitHub, Notion, databases, files) by URL. On your own computer you can also connect local command servers.

**Browser agent.** Opens websites in a real (hidden) browser, clicks, types and reads the result. It never opens private addresses and never types passwords or card numbers. Install it in Skills.

**Python sandbox.** Runs code for calculations and data work. With Docker it gets no network and limited memory. Without Docker it only runs on your own computer.

**Workflows, standing orders, email watcher.**
- Workflows: steps that run in order and stop for your approval.
- Standing orders: rules like "when X happens, do Y", checked during the heartbeat, webhooks and email.
- Email watcher: reads new mail as untrusted data and tells you only what matters. While reading mail, the Brain can't send messages, browse or use your devices.

**Use it from other apps.**
- Make an API token in Master. Your Brain then speaks the OpenAI API at `/v1` (model `brain` or `brain:<agent>`), so n8n, scripts and other tools can use it.
- Terminal app: `python -m brain.cli` for chat, plus `ask`, `status`, `jobs`, `run`, `pending` and `approve`.

**Phones and notifications.**
- Installed-app notifications work on Android, iPhone/iPad (iOS 16.4+), Windows and Mac.
- **This device** (Master tab) lets your Brain ask an open phone or laptop for its location, a camera photo or a notification. It asks you every time.
- An Android APK can be built for free on GitHub: see `mobile/README.md`.

**Plugins and skill packs.**
- Python plugins add tools. They run only on your computer, or on a server where the admin installed them.
- Skills can be downloaded and shared as a .zip.

**Monitoring.** Model requests per day in Master → Usage, and Prometheus metrics at `/metrics`.

## Round 4 features

**Chrome extension.** The Brain can use the tabs you're already signed in to (Chrome, Edge, Brave), only on sites you allow in the extension (each is a Chrome permission). Settings → Chrome extension → download, then chrome://extensions → Developer mode → Load unpacked.

**Menu-bar / tray app.** The Brain Companion now also comes as a menu-bar app (macOS) and tray app (Windows, Linux): connection status, Open my Brain, Pause control, Unlink, Quit. Built by GitHub Actions as `BrainTray-*`, or run `start-companion --tray`.

**Coding tools.** Git workspaces (Skills → Code workspaces): the Brain reads, searches and patches files, or makes a branch per task. You review every change in the diff viewer and keep (commit) or discard it. It never pushes.

**Steering and goals.**
- While an answer is still working, whatever you type steers it ("only free options"); Stop ends it.
- Goals (Automations → Goals, or `/goal … --budget 40`): a bigger job it works on by itself until done, with a budget of model requests and tokens. Pause, steer, stop, or add budget.
- Daily limits for the whole Brain.

**Secrets vault.** Save passwords (encrypted) or point to 1Password / Bitwarden. The Brain only knows the name and writes `{{secret:github}}`; the value is filled in inside the browser, only on the sites you listed, over https. Every use is logged and notifies you.

**Memory engines and tracing.** Optional LanceDB vector search, optional Honcho (experimental) for a deeper understanding of you, and OpenTelemetry traces (`BRAIN_OTEL_ENDPOINT`) for Jaeger, Grafana Tempo or SigNoz. Traces never carry your chat text.

**Skill marketplace.** Search ClawHub (OpenClaw's public skill registry, flagged skills hidden) and add skills; they arrive as "suggested" for you to read first. On a shared server people can publish skills to the server's shelf; the admin approves them.

**Like Siri: always listening for your activation word.**
- **Laptop** (desktop app, or the tray app for a server Brain): Settings → Activation word. Speech is understood offline on the computer; it then does what you say, including controlling the computer.
- **Android** (the Brain app): listens all the time, controls the phone through Accessibility (read the screen, tap, type, open apps), and works offline for common things (apps, calls, messages, alarms, timers, torch). See `mobile/README.md`.
- **iPhone**: "Hey Siri, Ask Brain" through a Shortcut (Apple doesn't allow more).
- Choose your own activation word in Master → Wake words; "Teach it my activation word" helps with unusual names.

**Works offline.** With no internet the laptop answers with a free model on the computer (Settings → Offline AI, uses Ollama), and the phone handles common commands itself and saves the rest for later.

**Use from my phone.** Keep the Brain on your own computer and open it from your phone over Tailscale (free, private): it listens only on your Tailscale address and asks for a password. Master → Use from my phone.

**Backup and restore.** Your whole Brain in one passphrase-encrypted file, to keep safe or move to another computer or server. Master → Backup and restore.

**Gmail push alerts.** Gmail tells the Brain the moment mail arrives (Google's free Gmail API + Pub/Sub, your own Cloud project, read-only access). Needs the Brain online over HTTPS; on a laptop the 2-minute email watcher still works.

## Device control (your computer and phone)

Open the **Control** tab. Nothing happens until you **turn it on** and **allow a device**. You allow it once, and it stays allowed until you switch it off.

**Your computer:** switch on **Allow control of this computer**, then press **See my screen (test)**. The packages it needs install automatically with `start.bat`; if you see an **Install PC control** button, click it once.

**Your Android phone:**
1. On the phone: Settings → About phone → tap **Build number** 7 times.
2. Settings → Developer options → switch on **USB debugging** (on Xiaomi/Redmi/POCO also **USB debugging (Security settings)**).
3. In Control, click **Install ADB** (Google's free Android tools), plug the phone in by USB and tap **Allow** on the phone.
4. No cable? With both on the same Wi-Fi, use **Wireless debugging → Pair device with pairing code** on the phone, enter the IP:port and code in Control → **Pair**, then **Connect** with the IP:port from the Wireless debugging screen.
5. Switch on **Allow control of my phone** and press **See my phone screen (test)**.

iPhones can't be controlled: Apple doesn't allow it.

**Give it tasks** in Chat or by voice, for example:
- "Open Chrome and search for flights from Delhi to Goa on 20 December"
- "Open Notepad and write my to-do list for tomorrow"
- "On my phone, open WhatsApp and send Amit 'running 10 minutes late'"
- "/pc take a screenshot and tell me which apps are open"
- "Lock my laptop"

Each task appears as a live card that shows every step and the latest view of the screen. You can also type a task into **Give it a task** in Control. `/do`, `/pc` and `/phone` force a device task.

**You decide what it may do.** For each kind of action choose **Allowed**, **Ask me** (it stops and waits for you to press Allow) or **Off**:

| Kind | Default |
|---|---|
| See the screen | Allowed |
| Open apps and websites | Allowed |
| Mouse and keyboard / touch | Allowed |
| Read files | Allowed |
| Change files | Ask me |
| Run commands | Ask me |
| Lock, sleep, shut down | Always asks |
| Calls and messages | Always asks |

Deleting always asks too, and it never erases anything: files go into a `_deleted_by_brain` folder next to them. Files can only be read or changed inside the folders you list (Desktop, Documents and Downloads to start with).

**Stopping it:** press **Stop** (in the header or in the task card), say or type **stop** or your rest word, or push the mouse hard into any screen corner.

**Safety rules it can't switch off:**
- It never types passwords, OTPs, PINs, card, bank or ID numbers, never confirms payments, and never solves CAPTCHAs. It asks you to do those parts.
- Instructions it reads on screen (websites, emails, messages) are treated as information, never as orders.
- Dangerous commands (formatting disks, disabling antivirus, deleting system folders, downloading and running code) are blocked.
- Every step goes into the Activity log.
- Only pages served by the Brain on your computer can talk to it, so other websites can't send it commands.
- Set a **Master PIN** so nobody else can turn device control on or change it.

**Which AI model:** the Brain sees your screen through screenshots, so use a model that understands images. Free **Gemini** models work well (Groq's Llama 4 Scout also works). With a text-only model it still works on the phone (it reads the on-screen buttons) and with keyboard shortcuts on the computer, but less reliably.

## Forgetting things

Say or type **"forget everything about mathematics"** (or "remove X from your memory", "X wali baat bhool jao"). The Brain lists every memory and learned source about it with a tick box; nothing is deleted until you press **Forget selected**. You can also clear the conversation in the same step. This works even when your AI models are out of free quota.

## How it learns (only from your instructions)

The Brain never decides by itself to learn something. It learns when you tell it to, in the **Learn** tab or in chat:

| You type in chat | What it does |
|---|---|
| `/learn Groq pricing` or `Learn about Groq pricing, focus on free limits` | Searches the web, reads the best pages, writes study notes following your instruction, and saves them with sources |
| `/read https://…` or `Read and learn this https://…` | Reads that page (or online PDF) and saves it |
| `From now on, reply to clients in formal English` or `/rule …` | Saves a standing rule it follows in every conversation |
| `Every week learn about AI voice agents` or `/topic weekly …` | Researches the topic now and then on that schedule (while the app is running) |
| `Search the web for …`, `/web …`, or the 🌐 button | Answers from a live web search with source links. **Not saved** unless you click "Save to knowledge" |

In the **Learn** tab you can also upload documents (PDF, Word, TXT, Markdown, HTML, CSV), see everything it has learned with your original instruction and source, and delete anything.

What it will not do:
- Learn, research or save anything you didn't ask for. Fact suggestions from your chats wait for your approval, and you can switch them off in Settings.
- Follow instructions written inside web pages or documents. Page text is treated as information only, hidden text is removed, and the study notes warn you if a page tries to give instructions.
- Read addresses on your own computer or home network, or pages behind a login.

**Web search engines** (Settings → Web access): DuckDuckGo works with no key. For more reliable results add a free Brave Search or Tavily key, or your own SearXNG server. They're tried in order. Pages that need JavaScript or a login may come back empty.

## Voice

Click the **mic** to say one thing, **Talk** for a hands-free conversation (it listens, answers aloud, and listens again), or **Wake word** to have it wait for your wake words (set in Master Control) while the page is open. The **speaker** button turns spoken replies on or off. Press **Esc** or say **"stop"** between turns to stop it; say **"goodbye"** to end a hands-free conversation.

Set it up in **Settings → Voice**:

| | Option | Cost | Privacy |
|---|---|---|---|
| Listening | Browser (Chrome/Edge) | Free | Audio goes to Google/Microsoft for transcription |
| Listening | Local Whisper | Free | Stays on your laptop. Click "Install local Whisper"; the first use downloads the model (about 150 MB for Base) |
| Listening | Groq Whisper | Free tier | Audio goes to Groq. Uses the Groq key from AI models |
| Speaking | Natural voice (default) | Free, no key | Microsoft neural voices (Indian English, Hindi, multilingual). Text is sent to Microsoft to be spoken |
| Speaking | Browser voices | Free | Stays on your laptop, but sounds robotic |
| Speaking | Piper | Free | Stays on your laptop. Click "Install Piper", then "Download Piper voice" |

Replies stream in word by word and speech starts after the first sentence, so you hear an answer in about 2–3 seconds. In voice conversations the Brain answers in 1–3 short, natural sentences. For the fastest replies put **Groq** first in Settings → AI models; for the best listening of Hindi and Hinglish choose **Groq Whisper**.

For full privacy choose **Local Whisper + Piper**. English, Hindi and Hinglish all work; Whisper handles mixed Hinglish best.

Things you can say: "Learn about …", "Search the web for …", "Remember that …", "Add to my journal: …", "From now on …", "Every week learn about …", "Switch to coach mode", "What did I say about …", "Stop".

With Device control on, voice commands like "open YouTube" or "on my phone, open WhatsApp" work too, and it speaks the result when the task is done.

## Privacy

- Everything lives in the `data/` folder on your laptop: `brain.db` (memories, chat, activity log) and `identity.yaml`.
- The server only listens on `127.0.0.1`, so other devices cannot reach it.
- Each chat message sends the Identity File and up to 8 relevant memories to the cloud provider answering it. Your whole memory store is never sent.
- Free tiers often use your data to improve their models (Google says so for Gemini's free tier). Mark your most personal memories **sealed**: only sealed memories are guaranteed to stay on your laptop.
- **Sealed** memories never go to any cloud provider. Tick "Use sealed memories" to use them; that conversation then runs only on the local model.
- Back up the `data/` folder regularly (an encrypted drive or encrypted cloud folder).
- To delete everything, stop the app and delete the `data/` folder.

## Settings

Providers and keys live in the Settings tab (saved to `data/settings.json` on your laptop). `.env` only holds:

| Setting | Default | What it does |
|---|---|---|
| `BRAIN_OWNER_NAME` | (empty: set it in Master) | Your name, used in prompts and imports |
| `BRAIN_PORT` | `8765` | Port for the web app |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, … | (empty) | Optional: seeds Settings on first start |

## Project layout

```
brain/
  config.py        settings
  safety_core.md   rules that come before everything; only you edit this file
  db.py            SQLite storage and the append-only activity log
  providers.py     provider presets (free and paid)
  settings.py      your providers, keys and order (data/settings.json)
  llm.py           universal connector with automatic fallback
  master.py        Master Control: name, commands, rest mode, switches, PIN and app lock
  control.py       Device control permissions: on/off, allowed devices, levels, folders
  devices.py       drivers for this computer (pyautogui) and Android phones (ADB), safety guards
  agent.py         the device task loop: look, decide one step, check permission, act, repeat
  accounts.py      web version: sign-up, login, sessions, admin, companion pairing
  hub.py           web version: live link to each user's Brain Companion app
  freellm.py       FreeLLMAPI: chat profiles, images, speech, transcription, search, quick setup
  tools.py         tools the Brain can use while answering
  channels.py      Telegram, Discord, Slack and email, with pairing
  automations.py   scheduled jobs, heartbeat, webhooks
  skills.py        SKILL.md skills and self-learning suggestions
  dreaming.py      nightly look back over the day
  channels_more.py WhatsApp, Signal, Matrix, Mattermost, IRC, Twitch, SMS, LINE
  agents.py        extra agents with their own persona, model, tools and chat apps
  attachments.py   photos, documents and voice recordings in chat
  mcp.py           MCP client (HTTP and local command servers)
  browser.py       the browser agent (Playwright)
  sandbox.py       Python sandbox
  workflows.py     workflows with approvals, standing orders, email watcher
  push.py          push notifications and the device node
  usage.py         usage tracking and Prometheus metrics
  plugins.py       plugins and skill packs
  cli.py           terminal app
  memory.py        add, tag, search, edit, forget, approval queue, rules
  web.py           web search, safe page reading, PDF/Word reading
  knowledge.py     what it learned: sources, study notes, scheduled topics
  identity.py      Identity File with versions
  core.py          the think loop (recall → answer → reflect)
  interview.py     life interview questions
  importer.py      WhatsApp import
  server.py        local web API
web/               the web app (chat, journal, interview, memories, identity, import, activity), installable (PWA)
companion/         Brain Companion: lets a web-hosted Brain use your computer and phone
deploy/, Dockerfile, docker-compose.yml   free hosting (see DEPLOY.md)
tests/             tests with fake models: python -m pytest tests
```

## Earlier roadmap (now built)

Read-only and draft-only connections to Gmail, Calendar and your CRM, a daily summary, and the permission tier system, as described in the master plan.
