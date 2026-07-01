#!/usr/bin/env bash
# cmn-ai — one-shot Raspberry Pi setup. Installs EVERYTHING the Pi needs to be the
# free-local-model node and connects it to Render — with NO port forwarding.
#
# It installs: base deps, Ollama (listening on the LAN), the model, optionally the
# trained Dirigent, and cloudflared; then brings up an OUTBOUND tunnel so Render can
# reach the Pi. No router ports are opened.
#
# The Pi runs only the model here — paid-model API keys live in Supabase and are used by
# the Render app, never on the Pi.
#
# Usage:
#   ./scripts/install-pi.sh                         # installs + starts a temporary quick tunnel
#   CF_TUNNEL_TOKEN=eyJ... ./scripts/install-pi.sh  # installs + persistent named tunnel (stable URL)
#   CMN_AI_MODEL=gemma3:4b ./scripts/install-pi.sh   # pick the model to pull
#
# Get CF_TUNNEL_TOKEN from Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel
# (Cloudflared) → copy the token. That gives a stable https URL with no interactive login.
set -euo pipefail

MODEL="${CMN_AI_MODEL:-gemma3:1b}"
CF_TUNNEL_TOKEN="${CF_TUNNEL_TOKEN:-}"

log() { printf "\n\033[1;36m==>\033[0m %s\n" "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

if ! have apt-get; then
  echo "This script targets Debian/Raspberry Pi OS (apt). For other systems, install"
  echo "Ollama + cloudflared manually; the model pull + tunnel steps still apply." >&2
  exit 1
fi

log "1/7  Base dependencies"
$SUDO apt-get update -y
$SUDO apt-get install -y curl ca-certificates gnupg jq

log "2/7  Ollama"
if ! have ollama; then
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "    already installed"
fi

log "3/7  Expose Ollama on the LAN (systemd override; no router port opened)"
$SUDO mkdir -p /etc/systemd/system/ollama.service.d
$SUDO tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now ollama 2>/dev/null || true
$SUDO systemctl restart ollama

log "4/7  Waiting for Ollama, then pulling model: $MODEL"
for _ in $(seq 1 30); do
  curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done
ollama pull "$MODEL"

log "5/7  Trained Dirigent (optional)"
if [ -f "./dist/router-pi/Modelfile" ]; then
  ollama create cmn-dirigent -f ./dist/router-pi/Modelfile && echo "    built 'cmn-dirigent'"
else
  echo "    skipped (no ./dist/router-pi/Modelfile — see docs/router-on-pi.md)"
fi

log "6/7  cloudflared (outbound tunnel client)"
if ! have cloudflared; then
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg |
    $SUDO tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" |
    $SUDO tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
  $SUDO apt-get update -y && $SUDO apt-get install -y cloudflared
else
  echo "    already installed"
fi

# quick smoke test
echo "    smoke test:"; ollama run "$MODEL" "reply with just: ok" || true

log "7/7  Tunnel to Render (no port forwarding)"
if [ -n "$CF_TUNNEL_TOKEN" ]; then
  echo "    Installing persistent named tunnel as a service (stable URL, reboot-safe)…"
  $SUDO cloudflared service install "$CF_TUNNEL_TOKEN"
  cat <<EOF

Pi is READY. The named tunnel runs as a service and survives reboots.
  • In Cloudflare Zero Trust, that tunnel should route your hostname → http://localhost:11434
  • Set OLLAMA_HOST on Render to that https hostname.
  • Local model in your cmn-ai config must be: $MODEL
EOF
else
  cat <<EOF

Install done. Ollama serves "$MODEL" on http://0.0.0.0:11434 (no ports opened).

Starting a QUICK tunnel now (temporary URL, no account). Copy the printed
https://<...>.trycloudflare.com URL into Render's OLLAMA_HOST. Keep this running,
or re-run with CF_TUNNEL_TOKEN=... for a stable tunnel that runs as a service.

(Ctrl-C stops the quick tunnel.)
EOF
  exec cloudflared tunnel --url http://localhost:11434
fi
