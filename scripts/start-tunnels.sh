#!/usr/bin/env bash
# Start both Cloudflare quick tunnels (Ollama :11434 + Vault :11435) in parallel
# and print the URLs as soon as they appear.
#
# Usage:  bash ~/cmn-ai/scripts/start-tunnels.sh
# Stop:   Ctrl-C  (kills both tunnels automatically)

set -euo pipefail

LOG_OLLAMA="/tmp/cf-ollama.log"
LOG_VAULT="/tmp/cf-vault.log"

cleanup() {
  echo ""
  echo "Stopping tunnels…"
  kill "$PID_OLLAMA" "$PID_VAULT" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Start both tunnels in background, log to temp files
cloudflared tunnel --url http://localhost:11434 >"$LOG_OLLAMA" 2>&1 &
PID_OLLAMA=$!

cloudflared tunnel --url http://localhost:11435 >"$LOG_VAULT" 2>&1 &
PID_VAULT=$!

echo "Waiting for tunnel URLs…"

extract_url() {
  grep -m1 'trycloudflare.com' "$1" 2>/dev/null | grep -oP 'https://[^\s]+trycloudflare\.com' || true
}

OLLAMA_URL=""
VAULT_URL=""

for _ in $(seq 1 60); do
  [ -z "$OLLAMA_URL" ] && OLLAMA_URL="$(extract_url "$LOG_OLLAMA")"
  [ -z "$VAULT_URL"  ] && VAULT_URL="$(extract_url "$LOG_VAULT")"
  if [ -n "$OLLAMA_URL" ] && [ -n "$VAULT_URL" ]; then
    break
  fi
  sleep 1
done

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Tunnel URLs — copy these into your hosting environment vars ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf  "║  OLLAMA_HOST = %-46s ║\n" "${OLLAMA_URL:-❌ not found (check /tmp/cf-ollama.log)}"
printf  "║  VAULT_HOST  = %-46s ║\n" "${VAULT_URL:-❌ not found (check /tmp/cf-vault.log)}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Both tunnels running. Press Ctrl-C to stop."

wait "$PID_OLLAMA" "$PID_VAULT"
