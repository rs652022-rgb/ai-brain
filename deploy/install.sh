#!/usr/bin/env bash
# One-command, secure install of AI Brain on a free Ubuntu/Debian server (Oracle Cloud Always Free,
# Google Cloud e2-micro, or any VPS). Run it as the normal user (it uses sudo when needed):
#
#   curl -fsSL https://raw.githubusercontent.com/rs652022-rgb/ai-brain/main/deploy/install.sh | bash
#
# What it does, all free and open source:
#   - security updates installed automatically, fail2ban against SSH password guessing,
#     a firewall that only opens SSH, 80 and 443
#   - Docker, then the Brain + Caddy (automatic HTTPS) + FreeLLMAPI (free models), restarted on reboot
#   - sign-ups closed: the first account (you) becomes the admin; you invite others later if you want
#   - a free DuckDNS web address kept pointed at this server (optional)
#   - a nightly snapshot of all Brains, 14 days kept; weekly app updates from GitHub (optional)
set -euo pipefail

REPO="${BRAIN_REPO:-https://github.com/rs652022-rgb/ai-brain.git}"
DIR="${BRAIN_DIR:-$HOME/ai-brain}"
say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ask() { local v; read -r -p "$1" v </dev/tty; printf '%s' "$v"; }

[ "$(id -u)" -eq 0 ] && { echo "Run this as your normal user (e.g. ubuntu), not root."; exit 1; }
command -v apt-get >/dev/null || { echo "This installer is for Ubuntu or Debian."; exit 1; }

echo
echo "  AI Brain: secure install"
echo "  You need a web address pointing to this server. Free: https://www.duckdns.org (log in, create a name,"
echo "  set its IP to this server's public IP: $(curl -fsS --max-time 5 https://api.ipify.org || echo '?'))"
echo
DOMAIN="$(ask '  Your web address (e.g. rahulbrain.duckdns.org): ')"
[ -n "$DOMAIN" ] || { echo "A web address is needed for HTTPS."; exit 1; }
DUCK_TOKEN=""
if [[ "$DOMAIN" == *.duckdns.org ]]; then
  DUCK_TOKEN="$(ask '  DuckDNS token (from duckdns.org, keeps the address pointed here; Enter to skip): ')"
fi
AUTO_UPDATE="$(ask '  Install app updates from GitHub automatically every week? [Y/n]: ')"

say "Security updates, firewall and SSH protection"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates unattended-upgrades fail2ban >/dev/null
sudo dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
sudo systemctl enable --now fail2ban >/dev/null 2>&1 || true
if command -v netfilter-persistent >/dev/null; then          # Oracle's Ubuntu images use iptables rules
  for p in 80 443; do
    sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport "$p" -j ACCEPT
  done
  sudo netfilter-persistent save >/dev/null
else
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ufw >/dev/null
  sudo ufw default deny incoming >/dev/null; sudo ufw default allow outgoing >/dev/null
  sudo ufw allow OpenSSH >/dev/null; sudo ufw allow 80/tcp >/dev/null; sudo ufw allow 443/tcp >/dev/null
  sudo ufw --force enable >/dev/null
fi
# SSH: keys only (only if you already log in with a key, so you can't lock yourself out)
if [ -s "$HOME/.ssh/authorized_keys" ] && ! sudo grep -q '^PasswordAuthentication no' /etc/ssh/sshd_config; then
  echo "PasswordAuthentication no" | sudo tee /etc/ssh/sshd_config.d/90-ai-brain.conf >/dev/null
  sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd 2>/dev/null || true
fi

say "Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh >/dev/null
  sudo usermod -aG docker "$USER"
fi
sudo mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] || echo '{"log-driver":"local","log-opts":{"max-size":"10m"}}' | sudo tee /etc/docker/daemon.json >/dev/null
sudo systemctl enable --now docker >/dev/null
sudo systemctl restart docker

say "AI Brain"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"
if [ ! -f .env.server ]; then
  cp deploy/env.server.example .env.server
  sed -i "s|^BRAIN_DOMAIN=.*|BRAIN_DOMAIN=$DOMAIN|; s|^BRAIN_SIGNUP=.*|BRAIN_SIGNUP=closed|" .env.server
  echo "BRAIN_METRICS_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')" >> .env.server
  chmod 600 .env.server
fi
mkdir -p brain-data && sudo chown -R 1000:1000 brain-data
sudo docker compose --profile https --profile freellmapi up -d --build

say "Free web address, nightly snapshots and updates"
CRON="$(crontab -l 2>/dev/null | grep -v '# ai-brain' || true)"
if [ -n "$DUCK_TOKEN" ]; then
  SUB="${DOMAIN%.duckdns.org}"
  CRON="$CRON
*/5 * * * * curl -fsS \"https://www.duckdns.org/update?domains=$SUB&token=$DUCK_TOKEN&ip=\" >/dev/null 2>&1 # ai-brain"
fi
mkdir -p "$HOME/brain-backups" && chmod 700 "$HOME/brain-backups"
CRON="$CRON
30 2 * * * cd $DIR && sudo tar -czf $HOME/brain-backups/brain-\$(date +\\%F).tar.gz brain-data .env.server && find $HOME/brain-backups -name 'brain-*.tar.gz' -mtime +14 -delete # ai-brain"
if [[ ! "$AUTO_UPDATE" =~ ^[Nn] ]]; then
  CRON="$CRON
15 3 * * 0 cd $DIR && git pull -q && sudo docker compose --profile https --profile freellmapi up -d --build >/dev/null 2>&1 # ai-brain"
fi
printf '%s\n' "$CRON" | sed '/^$/d' | crontab -

echo
echo "  Done. Open https://$DOMAIN in a minute (the HTTPS certificate takes a moment) and sign up."
echo "  The first account is the admin. Sign-ups are closed to everyone else until you open them in Admin."
echo
echo "  Free models for everyone: open the FreeLLMAPI dashboard through an SSH tunnel:"
echo "    ssh -L 3001:127.0.0.1:3001 $USER@<this server>   then visit http://localhost:3001"
echo "  add your free provider keys there, copy its unified key into $DIR/.env.server as"
echo "  BRAIN_FREELLMAPI_URL=http://freellmapi:3001/v1 and BRAIN_FREELLMAPI_KEY=..., then run:"
echo "    cd $DIR && sudo docker compose --profile https --profile freellmapi up -d"
echo
echo "  Snapshots: $HOME/brain-backups (14 days). Also download an encrypted backup from Master → Backup"
echo "  now and then and keep it off this server."
