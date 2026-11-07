# Put your Brain online (free)

The same code runs in two ways:

| | Your laptop (as before) | Web version |
|---|---|---|
| Start | `start.bat` / `start.sh` | Docker on a server, or your laptop with a tunnel |
| Who uses it | You, on this computer | Anyone you let sign up, from any phone, tablet or computer |
| Accounts | None | Sign up / log in; each person gets a private Brain |
| Device control | Directly | Through the **Brain Companion** app on each user's computer |

Everything below is free and open source: Docker, Caddy (HTTPS), Cloudflare Tunnel, Oracle Cloud's Always Free server, and DuckDNS (free web address). Nothing here needs a subscription. Each user adds their own free AI key (Gemini, Groq…), so the AI costs you nothing.

---

## No card, no server: your own computer + Tailscale (most private)

Every big cloud asks for a card. If you'd rather not, keep the Brain on your own computer and reach it from your phone privately:

1. Install the **desktop app** (or run `start.bat` / `start.sh`).
2. Install **Tailscale** (free, sign in with Google; no card) on that computer and on your phone: https://tailscale.com/download
3. In the Brain: **Master → Use from my phone**: set a phone password (10+ characters) and switch it on.
4. On the phone, open the address it shows (`http://100.x.y.z:8766`) and sign in. For the microphone and "Add to Home Screen",
   run `tailscale serve --bg 8765` once on the computer and use `https://<computer>.<tailnet>.ts.net` instead.

The Brain listens only on the Tailscale address, so nothing on your Wi-Fi or the internet can reach it, and it still asks for the
password. The computer has to be on. Back it up now and then: Master → Backup and restore.

## Option A: Oracle Cloud Always Free (recommended, always on, free)

Oracle gives every account an always-free Arm server (2 CPU cores and 12 GB memory since 2026, plus 200 GB disk). The Brain needs under 1 GB. Signing up asks for a card to verify you; Always Free resources are not charged. Set a $1 budget alert anyway (Billing → Budgets) as a safety net.

1. **Create the server**
   - Sign up at https://www.oracle.com/cloud/free/ and pick a home region close to you (for example Mumbai or Hyderabad).
   - Compute → Instances → **Create instance**. Image **Ubuntu 24.04**, shape **VM.Standard.A1.Flex** with **1 OCPU and 6 GB** (well inside the free limit). Download the SSH key it offers. If it says "out of capacity", try again later or pick another availability domain.
   - Open the instance → Subnet → Security list → **Add ingress rules**: TCP ports **80** and **443** from `0.0.0.0/0`.
2. **Get a free web address:** log in at https://www.duckdns.org with Google or GitHub, create a name such as `rahulbrain` and set its IP to your server's **public IP**. Copy your DuckDNS token from the same page.
3. **Install everything with one command.** Connect with `ssh -i key.pem ubuntu@YOUR_IP` and run:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/rs652022-rgb/ai-brain/main/deploy/install.sh | bash
   ```
   It asks for your web address and DuckDNS token, then sets up: automatic security updates, a firewall (only SSH, 80, 443), fail2ban, Docker, the Brain with automatic HTTPS, FreeLLMAPI, closed sign-ups, nightly snapshots (14 days) and weekly updates.
4. **Open `https://rahulbrain.duckdns.org`** and sign up. **The first account becomes the admin**; nobody else can sign up unless you open sign-ups in Admin.
5. **Free models:** follow the last lines the installer prints to add your free provider keys to FreeLLMAPI once; every Brain on the server then uses them.
6. **Keep a copy elsewhere:** Master → Backup and restore → Download backup (encrypted with your passphrase). Restore it on any other server, your laptop or the desktop app to move your Brain.

**Google Cloud instead:** the free `e2-micro` (1 GB memory, 30 GB disk, US regions only) works the same way: create an Ubuntu VM in us-west1, us-central1 or us-east1, allow HTTP and HTTPS traffic, then run the same install command.

## Option B: your own laptop or PC + Cloudflare Tunnel (quickest)

Your Brain is reachable from anywhere over HTTPS while that computer is on. No router settings needed.

1. In the `ai-brain` folder, add this line to the `.env` file:
   ```
   BRAIN_MULTIUSER=1
   ```
   Then run `start.bat` as usual. It now asks people to sign up or log in.
2. Install `cloudflared` from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/. On Windows: `winget install --id Cloudflare.cloudflared`.
3. Run:
   ```
   cloudflared tunnel --url http://localhost:8765
   ```
   It prints an address like `https://something-random.trycloudflare.com`. Open it on any phone or computer.

That quick address changes every time you restart cloudflared. For a permanent address, add a domain to a free Cloudflare account, create a tunnel in Zero Trust → Networks → Tunnels, and use its token with Docker: put `TUNNEL_TOKEN=...` in `.env.server`, then run `docker compose --profile tunnel up -d --build`.

## Option C: any other server

Any Linux machine with Docker works, including a home mini-PC or a VPS. Follow Option A from step 3. If you already have a reverse proxy, just run `docker compose up -d --build` and point your proxy at `127.0.0.1:8765`. Make sure the proxy passes WebSockets through, and serve it over HTTPS.

---

## After it's online

- **Install the app.** On Android (Chrome), use menu → **Install app**. On iPhone and iPad (Safari), use Share → **Add to Home Screen**. On Windows and Mac (Chrome or Edge), click the install icon in the address bar. It gets its own icon and window, and opens even when offline. Your data stays on the server. Installing needs HTTPS, which all options above give you.
- **Admin** (Master tab → Admin):
  - Close sign-ups when everyone you want is in.
  - Give someone a temporary password if they forget theirs.
  - Switch accounts off, or delete them.
- **Password-reset emails (optional).** Fill in the `BRAIN_SMTP_...` lines in `.env.server`. Free choices:
  - Gmail: turn on 2-step verification, then create an app password at https://myaccount.google.com/apppasswords.
  - Brevo: free plan with 300 emails a day.
- **Device control on the web version.** Each user installs the **Brain Companion** on their own computer:
  1. Open Control → Your computers → **Make a pairing code**.
  2. Start the companion and type in the site address and the code.
  3. Keep the companion window open. An Android phone plugged into that computer, or joined over Wi-Fi, can be controlled too.

  The server itself never moves a mouse or runs a command.
- **Companion downloads.** The Python version is always available from Control and works on Windows, Mac and Linux once Python is installed. For one-click apps that need nothing installed:
  1. In GitHub, open the **Actions** tab → **Build Brain Companion** → **Run workflow**.
  2. When it finishes, download the three files from the run.
  3. Put them in `brain-data/downloads/` on the server. Control then offers them.
- **Back up** the `brain-data` folder; it holds every user's Brain. Example daily backup:
  `0 3 * * * tar czf ~/brain-backup-$(date +\%a).tgz -C ~/ai-brain brain-data`
- **Update:** run `git pull && docker compose --profile https up -d --build`.

## Optional extras on a server

- **Browser agent:** build with Playwright: add `RUN pip install playwright && playwright install --with-deps chromium` to the Dockerfile (about 400 MB more).
- **Python sandbox:** mount the Docker socket only if you understand the risk. Without Docker, code running is off on shared servers.
- **Monitoring:** set `BRAIN_METRICS_TOKEN` and scrape `/metrics` with it as a Bearer token.
- **Tracing:** `pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http`, then set `BRAIN_OTEL_ENDPOINT=http://<collector>:4318` (Jaeger, Grafana Tempo, SigNoz are free).
- **Memory engines:** `pip install lancedb` for LanceDB; `pip install honcho-ai` and a Honcho address for Honcho.
- **Gmail push:** each user sets it up in Automations with their own Google Cloud project. Google must reach `https://<your address>/api/gmail/push/...`.
- **Viber and Webex:** after saving the bot token, press "Connect webhook" in Channels.
- **Secrets vault:** the encryption key is `brain-data/.vault.key`. Back it up with the data folder, and keep it private.
- **Android app:** Actions → Build Android app → enter your https address. See `mobile/README.md`.

## Safety notes

- Passwords are stored only as scrypt hashes, and login sessions as hashes.
- Logins are rate-limited.
- The site only accepts changes coming from its own pages.
- Users can't point the server at its own private network: custom AI or search addresses must be public. The admin can still use a local Ollama.
- On the web version, local Whisper, Piper and package installs are switched off on the server. Voice uses the browser, Groq Whisper and Microsoft's free neural voices.
- Sealed memories need a local model. On the web version they only work if the admin runs Ollama on the server.
