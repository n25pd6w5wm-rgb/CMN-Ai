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
#   CMN_AI_MODEL=gemma4:e2b ./scripts/install-pi.sh  # pick the model to pull
#
# gemma3:1b is the default because it is the biggest model a Pi runs comfortably;
# the app auto-serves the newest Gemma generation the host has pulled (gemma4 >
# gemma3, see src/cmn_ai/agents/local.py), so pulling a Gemma 4 on stronger
# hardware needs no config change. Smallest Gemma 4 is gemma4:e2b-it-qat (4.3 GB).
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

log "7/8  Vault service (your Obsidian notes stay here on the Pi)"
# Find the cmn-ai repo: run from inside it, or set CMN_AI_REPO=<git-url> to clone it.
REPO_DIR="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)"
if [ ! -f "$REPO_DIR/pyproject.toml" ] && [ -n "${CMN_AI_REPO:-}" ]; then
  git clone "$CMN_AI_REPO" "$HOME/cmn-ai" 2>/dev/null || (cd "$HOME/cmn-ai" && git pull --ff-only || true)
  REPO_DIR="$HOME/cmn-ai"
fi
if [ ! -f "$REPO_DIR/pyproject.toml" ]; then
  echo "    ⚠ Skipped: the cmn-ai code isn't here. Run this script from inside the cloned"
  echo "      repo, or re-run with CMN_AI_REPO=https://github.com/<you>/cmn-ai.git to clone it."
  echo "      Ollama (above) is ready regardless; the vault can be set up later."
else
  if ! have uv; then curl -LsSf https://astral.sh/uv/install.sh | sh; fi
  UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"
  (cd "$REPO_DIR" && "$UV_BIN" sync)
  $SUDO tee /etc/systemd/system/cmn-ai-vault.service >/dev/null <<EOF
[Unit]
Description=cmn-ai vault service (markdown notes search + upload)
After=network.target
[Service]
User=$(whoami)
WorkingDirectory=$REPO_DIR
Environment=CMN_AI_VAULT_DIR=$HOME/.cmn-ai/vault
ExecStart=$UV_BIN run cmn-ai vault-serve --host 0.0.0.0 --port 11435
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now cmn-ai-vault
  echo "    vault service on :11435 — notes in $HOME/.cmn-ai/vault (never leaves the Pi)"
fi

log "8/8  Tunnel to Render (no port forwarding)"
if [ -n "$CF_TUNNEL_TOKEN" ]; then
  echo "    Installing persistent named tunnel as a service (stable URL, reboot-safe)…"
  $SUDO cloudflared service install "$CF_TUNNEL_TOKEN"
  cat <<EOF

Pi is READY. The named tunnel runs as a service and survives reboots.
  • Route one hostname → http://localhost:11434 → set OLLAMA_HOST on Render.
  • Route a second hostname → http://localhost:11435 (the vault) → set VAULT_HOST on Render.
  • Local model in your cmn-ai config must be: $MODEL
  • Your notes live in $HOME/.cmn-ai/vault and never leave the Pi.
EOF
elif command -v tailscale >/dev/null 2>&1 || [ -n "${CMN_AI_SKIP_TUNNEL:-}" ]; then
  # No Cloudflare token, but Tailscale is installed (or the user asked to skip the
  # auto-tunnel). Do NOT block on a quick tunnel — the stable, no-domain path is
  # Tailscale Funnel via start-pi.sh. Hand off cleanly instead of hijacking the shell.
  cat <<EOF

Install done. Ollama serves "$MODEL" on http://0.0.0.0:11434 (no ports opened).

No Cloudflare token given — using the stable, no-domain path (Tailscale Funnel).
Bring the tunnels up (feste URLs, reboot-fest) and print the Render values with:

    bash ~/cmn-ai/scripts/start-pi.sh

If Tailscale isn't set up yet:
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up
Your notes live in $HOME/.cmn-ai/vault and never leave the Pi.
EOF
else
  cat <<EOF

Install done. Ollama serves "$MODEL" on http://0.0.0.0:11434 (no ports opened).

Starting a QUICK tunnel now (temporary URL, no account). Copy the printed
https://<...>.trycloudflare.com URL into Render's OLLAMA_HOST. Keep this running,
or re-run with CF_TUNNEL_TOKEN=... for a stable tunnel that runs as a service.

The vault runs on :11435 — expose it with a SECOND quick tunnel and set VAULT_HOST:
    cloudflared tunnel --url http://localhost:11435
Your notes live in $HOME/.cmn-ai/vault and never leave the Pi.

(Ctrl-C stops the quick tunnel.)

Next time (e.g. after a reboot) just run:
    bash ~/cmn-ai/scripts/start-pi.sh
It restarts services + both tunnels and prints the URLs for Render.
EOF
  exec cloudflared tunnel --url http://localhost:11434
fi
