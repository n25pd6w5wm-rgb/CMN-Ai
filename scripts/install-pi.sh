#!/usr/bin/env bash
# cmn-ai — Raspberry Pi setup.
#
# Role of the Pi in the architecture: it runs the FREE local model (Ollama/Gemma) and is
# reached by the hosted app on Render via OLLAMA_HOST. This script installs Ollama, pulls
# the model, and exposes Ollama on the LAN so a tunnel can forward it to Render.
#
# It contains NO API keys. Paid-model keys (Anthropic, OpenAI, …) live in Supabase and are
# used by the Render app — the Pi never sees them.
#
# Usage:   ./scripts/install-pi.sh            # uses default model
#          CMN_AI_MODEL=gemma3:4b ./scripts/install-pi.sh
set -euo pipefail

# Pi-friendly small model by default. IMPORTANT: this name must match `agents.local.model`
# in your config (config/default.yaml / config/render.yaml currently say "gemma4:latest" —
# change one side so they match, or override here with CMN_AI_MODEL).
MODEL="${CMN_AI_MODEL:-gemma3:1b}"

echo "==> 1/4  Installing Ollama (if missing)"
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "    Ollama already installed: $(ollama --version 2>/dev/null || echo present)"
fi

echo "==> 2/4  Exposing Ollama on all interfaces (so a tunnel can reach it)"
if command -v systemctl >/dev/null 2>&1; then
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
  sudo systemctl daemon-reload
  sudo systemctl restart ollama
  echo "    Ollama listening on 0.0.0.0:11434 (systemd)"
else
  echo "    No systemd — start manually with:  OLLAMA_HOST=0.0.0.0:11434 ollama serve"
fi

echo "==> 3/4  Pulling model: $MODEL"
ollama pull "$MODEL"

echo "==> 4/4  (optional) Building the trained Dirigent from an exported Modelfile"
if [ -f "./dist/router-pi/Modelfile" ]; then
  ollama create cmn-dirigent -f ./dist/router-pi/Modelfile
  echo "    Built 'cmn-dirigent' (set router.strategy: ollama to use it)."
else
  echo "    Skipped (no ./dist/router-pi/Modelfile). See docs/router-on-pi.md to export it."
fi

cat <<EOF

Done.
  • Ollama serves "$MODEL" on http://0.0.0.0:11434
  • Smoke test:   ollama run "$MODEL" "hello"

Next steps (so Render can reach this Pi — do NOT open router ports directly):
  1) Install a tunnel, e.g. Cloudflare:
       curl -fsSL https://pkg.cloudflare.com/install.sh | sudo bash && sudo apt install cloudflared
       cloudflared tunnel --url http://localhost:11434
     (or: tailscale funnel 11434)
  2) Copy the printed https URL and set it as OLLAMA_HOST in the Render dashboard.
  3) Ensure your config's local model name matches: $MODEL

API keys for paid models do NOT go on the Pi — load them into Supabase (see docs/DEPLOY.md).
EOF
