# AI Brain — Phase 1

Your personal AI Brain, running privately on your laptop. Phase 1 gives it a memory, an identity and a voice:

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

Phase 1 cannot act on your devices, send messages or spend money. That comes in later phases, behind permission tiers.

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

Voice can't yet control your laptop or phone (opening apps, sending messages, calls). That comes in Phase 3, behind permission tiers.

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
| `BRAIN_OWNER_NAME` | `Rahul Sharma` | Your name, used in prompts and imports |
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
  memory.py        add, tag, search, edit, forget, approval queue, rules
  web.py           web search, safe page reading, PDF/Word reading
  knowledge.py     what it learned: sources, study notes, scheduled topics
  identity.py      Identity File with versions
  core.py          the think loop (recall → answer → reflect)
  interview.py     life interview questions
  importer.py      WhatsApp import
  server.py        local web API
web/               the web app (chat, journal, interview, memories, identity, import, activity)
tests/             tests with fake models: python -m pytest tests
```

## What's next (Phase 2)

Read-only and draft-only connections to Gmail, Calendar and your CRM, a daily summary, and the permission tier system, as described in the master plan.
