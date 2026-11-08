# AI Brain vs OpenClaw: feature check

Checked on 8 Nov 2026 against OpenClaw's feature index (docs.openclaw.ai/concepts/features) and this
repo's code. "Yes" means the code and tests exist here. It does not mean every service was tried with a
real account.

## Chat channels

| OpenClaw | AI Brain |
|---|---|
| Telegram, WebChat | Yes (web app, Telegram) |
| Discord, Slack, Signal, WhatsApp, SMS, LINE, Matrix, Mattermost, IRC, Twitch, Nostr | Yes |
| Feishu/Lark, QQ, Zalo, Nextcloud Talk, Synology Chat, WeChat | Yes (and WeCom, DingTalk, Rocket.Chat, Zulip, Viber, Messenger, Instagram, Webex, email) |
| Microsoft Teams | **Added now** (outgoing webhook, plus an incoming webhook for slow answers) |
| Google Chat | **Added now** (HTTP app; each request's Google token is checked) |
| iMessage | No: needs a Mac signed in to Messages. This project runs on Windows and Android. |
| Voice Call (phone calls) | No: SMS is in, phone calls are not. A Twilio voice call is a sizeable follow-up. |
| A2A, Reef, Raft, Tlon, Zalo Personal, Yuanbao | No: niche or unofficial. |
| Group mention rules, DM pairing and allowlists | Yes |

## Agent and tools

| OpenClaw | AI Brain |
|---|---|
| Streaming agent with tools | Yes (web search, read page, browser, your own Chrome, Python, code, schedule, delegate, MCP, skills, devices) |
| Multi-agent routing, isolated sessions | Yes (agents, per-channel conversations) |
| Browser automation | Yes (browser tool and Chrome extension) |
| Exec and sandboxing | Yes (sandboxed run_python and code) |
| Cron and heartbeat | Yes (automations, schedule tool, heartbeat) |
| Skills, plugins, ClawHub | Yes (skills, plugins, a ClawHub search and install in the marketplace) |
| Workflow pipelines (Lobster) | Yes (workflows) |
| Image and video generation | Yes |
| Web search: Brave, DuckDuckGo, Exa, Firecrawl, Perplexity, SearXNG, Tavily | Yes |
| Many model providers, local models, failover | Yes (FreeLLMAPI, Groq, OpenRouter, Ollama and more, with automatic backups) |
| Subscription sign-in (for example OpenAI Codex OAuth) | No: providers use API keys. |
| Canvas (a live visual workspace the agent draws on) | No: the biggest missing feature. |

## Devices, voice and apps

| OpenClaw | AI Brain |
|---|---|
| Android node: chat, voice, camera, device commands | Yes (Android app and phone link) |
| Computer control (Windows Hub / macOS app) | Yes (computer control and screenshots) |
| iOS node | No iOS app |
| Screen recording | No: screenshots only |
| Voice notes in, text-to-speech out | Yes. Barge-in, Hindi voices and faster replies are new in this change. |
| Media in and out, Control UI | Yes |

## Still missing, by size

1. **Canvas:** a live page the agent can draw on (large).
2. **Phone calls** through Twilio Voice (medium).
3. **Screen recording** on the computer and the phone (medium).
4. **iOS app and iMessage:** need Apple hardware (large, not possible on Windows).
5. **Subscription sign-in** for model providers (small to medium, per provider).
