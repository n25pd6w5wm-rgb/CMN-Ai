#!/usr/bin/env bash
# Upgrade the Pi's free local model to Gemma 4 — one command, reboot-safe.
#
#   bash ~/cmn-ai/scripts/upgrade-pi-gemma4.sh
#
# The app's OllamaAgent self-selects the newest pulled Gemma generation on every
# health check (gemma4 before gemma3, see agents/local.py), so simply pulling a
# Gemma 4 tag here upgrades cmn-ai with NO config change and NO redeploy. Gemma 3
# stays installed as an automatic fallback if Gemma 4 is too heavy at runtime.
#
# Model choice: gemma4:e2b is the smallest Gemma 4 (~4.3 GB). It fits a Pi 5 (8 GB)
# but is noticeably heavier than gemma3:1b — expect slower first tokens. Override
# with CMN_AI_GEMMA4=gemma4:e4b (bigger/better) if your Pi has the headroom.
set -euo pipefail

MODEL="${CMN_AI_GEMMA4:-gemma4:e2b}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

command -v ollama >/dev/null 2>&1 || {
  echo "Ollama ist nicht installiert — erst: bash ~/cmn-ai/scripts/install-pi.sh" >&2
  exit 1
}

# Make sure Ollama is up (the app + the pull both need it).
systemctl is-active --quiet ollama || sudo systemctl start ollama
for _ in $(seq 1 30); do
  curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done

# Warn (don't block) if RAM looks tight for a ~4.3 GB model.
total_mb=$(free -m 2>/dev/null | awk '/Mem:/{print $2}')
if [ -n "${total_mb:-}" ] && [ "$total_mb" -lt 7000 ]; then
  echo "⚠ Nur ${total_mb} MB RAM erkannt — Gemma 4 (~4,3 GB) läuft evtl. langsam/knapp."
fi

say "Lade $MODEL (das dauert beim ersten Mal — mehrere GB) …"
ollama pull "$MODEL"

say "Kurzer Smoke-Test:"
ollama run "$MODEL" "Antworte mit genau einem Wort: ok" || true

# Keep gemma3:1b as a lightweight fallback (self-select uses gemma4 first anyway).
ollama list 2>/dev/null | grep -q '^gemma3:1b' || ollama pull gemma3:1b || true

say "Fertig — cmn-ai serviert jetzt automatisch $MODEL (gemma4 vor gemma3)."
echo "    Prüfen in der App: Einstellungen → Modelle → 'local' zeigt $MODEL,"
echo "    sobald der nächste Chat den Health-Check ausgelöst hat."
echo "    Zurück auf klein: ollama rm $MODEL   (Fallback gemma3:1b bleibt aktiv)."
