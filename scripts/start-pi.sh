#!/usr/bin/env bash
# One-command start for the Raspberry Pi: makes sure Ollama + the vault service run
# and both Cloudflare tunnels are up.
#
#   bash ~/cmn-ai/scripts/start-pi.sh
#
# Tunnel modes (checked in this order):
#   1. NAMED tunnels (stable, reboot-safe) — Philipp's tunnels:
#        Ollama: 0475ed16-5058-4ece-a852-68a06e7127df
#        Vault:  d61de74f-7292-4774-9358-5c460f28c4a0
#      Needs the tunnel credentials on this Pi ONCE:
#        cloudflared tunnel login          # opens browser, pick the domain
#      (or drop the credentials JSONs into ~/.cloudflared/<id>.json)
#      The script then installs them as systemd services — they survive reboots,
#      and the URLs never change again.
#   2. QUICK tunnels (fallback) — new random URL on every start; the script prints
#      the values to paste into Render (OLLAMA_HOST / VAULT_HOST).

set -euo pipefail

TUNNEL_OLLAMA_ID="${TUNNEL_OLLAMA_ID:-0475ed16-5058-4ece-a852-68a06e7127df}"
TUNNEL_VAULT_ID="${TUNNEL_VAULT_ID:-d61de74f-7292-4774-9358-5c460f28c4a0}"
CRED_DIR="$HOME/.cloudflared"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/3  Dienste prüfen"
if systemctl is-active --quiet ollama; then
  echo "    ollama: läuft"
else
  echo "    ollama: starte …"
  sudo systemctl start ollama
fi
if systemctl list-unit-files --no-legend cmn-ai-vault.service 2>/dev/null | grep -q vault; then
  systemctl is-active --quiet cmn-ai-vault || sudo systemctl start cmn-ai-vault
  echo "    vault:  läuft"
else
  echo "    vault:  nicht installiert (optional — scripts/install-pi.sh richtet ihn ein)"
fi

# ---------- named-tunnel mode ----------

install_named_tunnel() { # $1=name  $2=tunnel-id  $3=local port
  local name=$1 id=$2 port=$3 cfg="$CRED_DIR/cmn-$name.yml"
  cat >"$cfg" <<EOF
tunnel: $id
credentials-file: $CRED_DIR/$id.json
ingress:
  - service: http://localhost:$port
EOF
  sudo tee "/etc/systemd/system/cloudflared-$name.service" >/dev/null <<EOF
[Unit]
Description=Cloudflare tunnel ($name -> localhost:$port)
After=network-online.target
[Service]
ExecStart=$(command -v cloudflared) tunnel --config $cfg run
Restart=always
User=$USER
[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now "cloudflared-$name"
}

# Token mode: tokens from the Zero-Trust dashboard (or fetched via API) in
# ~/.config/cmn-ai/cf-tunnels.env as CF_TOKEN_OLLAMA=… / CF_TOKEN_VAULT=…
TOKEN_FILE="$HOME/.config/cmn-ai/cf-tunnels.env"
[ -f "$TOKEN_FILE" ] && . "$TOKEN_FILE"

install_token_tunnel() { # $1=name  $2=token
  local name=$1 token=$2
  sudo tee "/etc/systemd/system/cloudflared-$name.service" >/dev/null <<EOF
[Unit]
Description=Cloudflare tunnel ($name, token mode)
After=network-online.target
[Service]
ExecStart=$(command -v cloudflared) tunnel run --token $token
Restart=always
User=$USER
[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now "cloudflared-$name"
}

if [ -n "${CF_TOKEN_OLLAMA:-}" ] && [ -n "${CF_TOKEN_VAULT:-}" ]; then
  say "2/3  Named Tunnels per Token einrichten (reboot-fest)"
  install_token_tunnel ollama "$CF_TOKEN_OLLAMA"
  install_token_tunnel vault "$CF_TOKEN_VAULT"
  say "3/3  Fertig — Tunnel laufen als systemd-Dienste (Token-Modus)."
  echo "    Status:  systemctl status cloudflared-ollama cloudflared-vault"
  exit 0
fi

if [ -f "$CRED_DIR/$TUNNEL_OLLAMA_ID.json" ] && [ -f "$CRED_DIR/$TUNNEL_VAULT_ID.json" ]; then
  say "2/3  Named Tunnels als Dienste einrichten (reboot-fest)"
  install_named_tunnel ollama "$TUNNEL_OLLAMA_ID" 11434
  install_named_tunnel vault "$TUNNEL_VAULT_ID" 11435
  say "3/3  Fertig — Tunnel laufen als systemd-Dienste."
  echo "    Status:  systemctl status cloudflared-ollama cloudflared-vault"
  echo ""
  echo "    Stabile URLs brauchen einmalig DNS-Routen auf deiner Cloudflare-Domain:"
  echo "      cloudflared tunnel route dns $TUNNEL_OLLAMA_ID ollama.<deine-domain>"
  echo "      cloudflared tunnel route dns $TUNNEL_VAULT_ID  vault.<deine-domain>"
  echo "    Danach in Render eintragen:"
  echo "      OLLAMA_HOST = https://ollama.<deine-domain>"
  echo "      VAULT_HOST  = https://vault.<deine-domain>"
  exit 0
fi

# ---------- quick-tunnel fallback ----------

say "2/3  Keine Tunnel-Credentials in $CRED_DIR gefunden → Quick Tunnels (URLs wechseln!)"
echo "    Für stabile URLs einmalig:  cloudflared tunnel login   und Skript neu starten."

LOG_OLLAMA="/tmp/cf-ollama.log"; LOG_VAULT="/tmp/cf-vault.log"
: >"$LOG_OLLAMA"; : >"$LOG_VAULT"
cloudflared tunnel --url http://localhost:11434 >"$LOG_OLLAMA" 2>&1 &
PID_OLLAMA=$!
cloudflared tunnel --url http://localhost:11435 >"$LOG_VAULT" 2>&1 &
PID_VAULT=$!

cleanup() { echo ""; echo "Stoppe Tunnel …"; kill "$PID_OLLAMA" "$PID_VAULT" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

url_from_log() {
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
