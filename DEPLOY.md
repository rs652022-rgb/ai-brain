# Put your Brain online (free)

The same code runs in two ways:

| | Your laptop (as before) | Web version |
|---|---|---|
| Start | `start.bat` / `start.sh` | Docker on a server, or your laptop with a tunnel |
| Who uses it | You, on this computer | Anyone you let sign up, from any phone, tablet or computer |
| Accounts | None | Sign up / log in; each person gets a private Brain |
| Device control | Directly | Through the **Brain Companion** app on each user's computer |

Everything below is free and open source: Docker, Caddy (HTTPS), Cloudflare Tunnel, Oracle Cloud's Always Free server, and DuckDNS (free web address). Each user adds their own free AI key (Gemini, Groq…), so the AI costs you nothing.

---

## Option A: Oracle Cloud Always Free (recommended, always on, free forever)

Oracle gives every account a free server (up to 4 CPU cores and 24 GB memory) with no time limit. Signing up asks for a card to verify you, but Always Free resources are never charged.

1. **Create the server**
   - Sign up at https://www.oracle.com/cloud/free/ and pick a home region close to you (for example Mumbai or Hyderabad).
   - Go to Compute → Instances → **Create instance**. Choose Image **Ubuntu 22.04** and Shape **Ampere A1** (1 OCPU and 6 GB is plenty). Download the SSH key it offers.
   - Open the instance → Subnet → Security list → **Add ingress rules** for TCP ports **80** and **443** from `0.0.0.0/0`.
2. **Get a free web address**
   - Log in at https://www.duckdns.org with Google or GitHub.
   - Create a name such as `rahulbrain` and set its IP to your server's **public IP**.
   - Your address is now `rahulbrain.duckdns.org`.
3. **Install Docker and the Brain.** Connect with `ssh -i key.pem ubuntu@YOUR_IP`, then run:
   ```bash
   sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT && sudo netfilter-persistent save
   curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER && newgrp docker
   git clone https://github.com/YOUR_GITHUB/ai-brain.git && cd ai-brain
   cp deploy/env.server.example .env.server && nano .env.server     # set BRAIN_DOMAIN=rahulbrain.duckdns.org
   docker compose --profile https up -d --build
   ```
   For a private repository, make a token at GitHub → Settings → Developer settings → Fine-grained tokens (read-only "Contents" on this repo) and clone with `git clone https://YOUR_TOKEN@github.com/YOUR_GITHUB/ai-brain.git`.
4. **Open `https://rahulbrain.duckdns.org`** and sign up. **The first account becomes the admin.** HTTPS is set up automatically by Caddy.

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

## Safety notes

- Passwords are stored only as scrypt hashes, and login sessions as hashes.
- Logins are rate-limited.
- The site only accepts changes coming from its own pages.
- Users can't point the server at its own private network: custom AI or search addresses must be public. The admin can still use a local Ollama.
- On the web version, local Whisper, Piper and package installs are switched off on the server. Voice uses the browser, Groq Whisper and Microsoft's free neural voices.
- Sealed memories need a local model. On the web version they only work if the admin runs Ollama on the server.
