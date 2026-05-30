#!/bin/bash
# Double-clickable launcher for cmn-ai (macOS).
# Loads .env (Supabase creds), starts the web server, opens the browser.

cd "$(dirname "$0")" || exit 1

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and add your Supabase URL + key."
  echo "(The app will still run local-only without it.)"
  echo ""
fi

HOST="127.0.0.1"
PORT="8000"

# Open the browser once the server is up.
(
  for _ in $(seq 1 40); do
    if curl -s "http://$HOST:$PORT/api/budget" >/dev/null 2>&1; then
      open "http://$HOST:$PORT/"
      break
    fi
    sleep 0.5
  done
) &

echo "Starting cmn-ai on http://$HOST:$PORT  (Ctrl-C to stop)"
exec uv run cmn-ai serve --host "$HOST" --port "$PORT"
