#!/usr/bin/env bash
# One-command start for the Raspberry Pi after a reboot (or whenever the tunnels died):
# makes sure Ollama + the vault service run, brings up both Cloudflare quick tunnels,
# and prints the URLs to paste into Render (OLLAMA_HOST / VAULT_HOST).
#
# Usage:  bash ~/cmn-ai/scripts/start-pi.sh
# Stop:   Ctrl-C  (stops the tunnels; Ollama + vault keep running as services)
#
# Note: quick tunnels get a NEW URL on every start — after running this, update the
# two env vars in the Render dashboard. For URLs that never change, create a named
# Cloudflare tunnel (see docs/DEPLOY.md).

set -euo pipefail

LOG_OLLAMA="/tmp/cf-ollama.log"
LOG_VAULT="/tmp/cf-vault.log"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/3  Dienste prüfen"
if systemctl is-active --quiet ollama; then
  echo "    ollama: läuft"
else
  echo "    ollama: starte …"
  sudo systemctl start ollama
fi
if systemctl list-unit-files --no-legend cmn-ai-vault.service 2>/dev/null | grep -q vault; then
  if systemctl is-active --quiet cmn-ai-vault; then
    echo "    vault:  läuft"
  else
    echo "    vault:  starte …"
    sudo systemctl start cmn-ai-vault
  fi
else
  echo "    vault:  nicht installiert (optional — scripts/install-pi.sh richtet ihn ein)"
fi

say "2/3  Tunnel starten"
: >"$LOG_OLLAMA"; : >"$LOG_VAULT"
cloudflared tunnel --url http://localhost:11434 >"$LOG_OLLAMA" 2>&1 &
PID_OLLAMA=$!
cloudflared tunnel --url http://localhost:11435 >"$LOG_VAULT" 2>&1 &
PID_VAULT=$!

cleanup() {
  echo ""
  echo "Stoppe Tunnel …"
  kill "$PID_OLLAMA" "$PID_VAULT" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

url_from_log() { # waits until the quick-tunnel URL shows up in the log
  local log=$1 url=""
  for _ in $(seq 1 60); do
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 || true)
    [ -n "$url" ] && { echo "$url"; return 0; }
    sleep 1
  done
  return 1
}

URL_OLLAMA=$(url_from_log "$LOG_OLLAMA") || { echo "Ollama-Tunnel kam nicht hoch — siehe $LOG_OLLAMA"; exit 1; }
URL_VAULT=$(url_from_log "$LOG_VAULT") || { echo "Vault-Tunnel kam nicht hoch — siehe $LOG_VAULT"; exit 1; }

say "3/3  Fertig — diese Werte in Render eintragen (Environment):"
echo "    OLLAMA_HOST = $URL_OLLAMA"
echo "    VAULT_HOST  = $URL_VAULT"
echo ""
echo "Tunnel laufen, bis dieses Fenster geschlossen wird (Ctrl-C zum Beenden)."
wait
