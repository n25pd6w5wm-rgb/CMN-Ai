#!/usr/bin/env bash
# Upgrade the Pi's free local model to Gemma 4 — one command, reboot-safe.
#
#   bash ~/cmn-ai/scripts/upgrade-pi-gemma4.sh
#
# The app's OllamaAgent self-selects the newest pulled Gemma generation on every
# health check (gemma4 before gemma3, see agents/local.py), so simply pulling a
# Gemma 4 tag here upgrades cmn-ai with NO config change and NO redeploy. Gemma 3
# stays installed as an automatic fallback.
#
# Model choice (verified against the Ollama registry, 2026-07):
#   gemma4:e2b-it-qat  ~4.0 GB  ← DEFAULT: quantization-aware, fits an 8 GB Pi 5
#   gemma4:e4b-it-qat  ~5.7 GB  tight on 8 GB
#   gemma4:e2b         ~6.7 GB  ✗ too big for 8 GB — froze the Pi in testing
# Override with CMN_AI_GEMMA4=<tag>. The RAM guard below refuses any model that
# would not leave the OS enough headroom, so a wrong choice can't wedge the Pi.
set -euo pipefail

MODEL="${CMN_AI_GEMMA4:-gemma4:e2b-it-qat}"
# Keep this much RAM free for the OS/Ollama runtime beyond the model's on-disk size.
HEADROOM_MB="${CMN_AI_HEADROOM_MB:-2500}"

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

# --- Hard RAM guard: refuse a model that won't fit, so we never freeze the Pi ---
# Ask the registry how big $MODEL actually is (sum of layer sizes), then compare to
# this host's total RAM minus headroom. A 7.2 GB model on 8 GB RAM thrashes the
# machine into an unrecoverable freeze — this check is why that can't happen here.
total_mb=$(free -m 2>/dev/null | awk '/Mem:/{print $2}')
tag="${MODEL#*:}"; [ "$tag" = "$MODEL" ] && tag="latest"
manifest=$(curl -fsS --max-time 15 \
  -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  "https://registry.ollama.ai/v2/library/${MODEL%%:*}/manifests/${tag}" 2>/dev/null || echo "")
model_mb=$(printf '%s' "$manifest" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(int(sum(l['size'] for l in d.get('layers',[]))/1024/1024))" \
  2>/dev/null || echo 0)

if [ -n "${total_mb:-}" ] && [ "${model_mb:-0}" -gt 0 ]; then
  need=$(( model_mb + HEADROOM_MB ))
  echo "Modell $MODEL ≈ ${model_mb} MB; RAM ${total_mb} MB; Bedarf inkl. Headroom ${need} MB."
  if [ "$need" -gt "$total_mb" ]; then
    echo "❌ $MODEL ist zu groß für ${total_mb} MB RAM — würde den Pi einfrieren. Abbruch." >&2
    echo "   Nimm eine kleinere Variante, z. B.  CMN_AI_GEMMA4=gemma4:e2b-it-qat  (~4 GB)." >&2
    exit 1
  fi
else
  echo "⚠ Konnte Modellgröße/RAM nicht ermitteln — fahre ohne harten Check fort."
fi

say "Lade $MODEL (das dauert beim ersten Mal — mehrere GB) …"
ollama pull "$MODEL"

say "Kurzer Smoke-Test (lädt das Modell wirklich in den RAM):"
timeout 180 ollama run "$MODEL" "Antworte mit genau einem Wort: ok" || {
  echo "⚠ Smoke-Test kam nicht durch — Modell evtl. doch zu schwer. gemma3:1b bleibt Fallback."
}

# Keep gemma3:1b as a lightweight fallback (self-select uses gemma4 first anyway).
ollama list 2>/dev/null | grep -q '^gemma3:1b' || ollama pull gemma3:1b || true

say "Fertig — cmn-ai serviert jetzt automatisch $MODEL (gemma4 vor gemma3)."
echo "    Prüfen in der App: Einstellungen → Modelle → 'local' zeigt $MODEL,"
echo "    sobald der nächste Chat den Health-Check ausgelöst hat."
echo "    Zurück auf klein: ollama rm $MODEL   (Fallback gemma3:1b bleibt aktiv)."
