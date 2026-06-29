#!/usr/bin/env bash
# Export the trained Dirigent (routing model) so it can run on a Raspberry Pi via Ollama.
# RUN THIS ON A MAC with the training extra installed: `uv sync --extra train`.
#
# Why a dedicated bf16 export (not the Mac's live 4-bit adapter):
#   The Mac serves routing with a 4-bit MLX model for speed. That 4-bit representation does
#   NOT convert cleanly to GGUF (Ollama errors "unknown data type: U32"), and dequantizing
#   it degrades the model. So for the Pi we fine-tune a parallel LoRA adapter on the *bf16*
#   base and fuse it into a clean bf16 checkpoint that Ollama imports faithfully. f16 is kept
#   (NOT re-quantized) because q4 quantization destroys this tiny 1B model's output.
#
# Output: dist/router-pi/  (fused/ model + Modelfile). Copy it to the Pi and run:
#   ollama create cmn-dirigent -f Modelfile
# See docs/router-on-pi.md for the full walkthrough.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

BF16_BASE="mlx-community/gemma-3-1b-it-bf16"
BF16_ADAPTER="${HOME}/.cmn-ai/router-adapter-gemma3-1b-bf16"
DATA_DIR="${HOME}/.cmn-ai/router-data-bf16"
OLLAMA_MODEL="cmn-dirigent"

OUT_DIR="${REPO_ROOT}/dist/router-pi"
FUSED_DIR="${OUT_DIR}/fused"

echo "[export] base model : ${BF16_BASE}"
echo "[export] adapter     : ${BF16_ADAPTER}"
echo "[export] output dir  : ${OUT_DIR}"

# Train the bf16 routing adapter if it doesn't exist yet (a few minutes on the Mac GPU).
if [ ! -d "${BF16_ADAPTER}" ]; then
  echo "[export] bf16 adapter missing — fine-tuning it now (this can take a few minutes)…"
  uv run python - "${BF16_BASE}" "${DATA_DIR}" "${BF16_ADAPTER}" <<'PY'
import sys
from pathlib import Path
from cmn_ai.cli import _build_samples
from cmn_ai.training.dataset import split, write_dataset
from cmn_ai.training.mlx_finetune import run_lora

base, data_dir, adapter = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
train, valid, test = split(_build_samples(), seed=0)
print(f"dataset: {len(train)} train / {len(valid)} valid / {len(test)} test", flush=True)
write_dataset(data_dir, train, valid, test)
run_lora(base_model=base, data_dir=data_dir, adapter_path=adapter, iters=300)
PY
fi

rm -rf "${FUSED_DIR}"
mkdir -p "${FUSED_DIR}"

# Fuse the LoRA adapter into the bf16 base -> standard HF safetensors checkpoint. No
# --export-gguf (mlx_lm can't convert gemma3); Ollama converts the safetensors itself on
# `ollama create` (gemma3 supported).
echo "[export] fusing adapter into bf16 base (this can take a few minutes)…"
uv run python -m mlx_lm fuse \
  --model "${BF16_BASE}" \
  --adapter-path "${BF16_ADAPTER}" \
  --save-path "${FUSED_DIR}"

# Ollama Modelfile: import the fused safetensors, deterministic short routing output.
# IMPORTANT: do NOT quantize this 1B model (q4 garbles it); keep it at f16.
cat > "${OUT_DIR}/Modelfile" <<EOF
# Ollama Modelfile for the cmn-ai trained Dirigent (routing classifier).
# Build on the Pi:  ollama create ${OLLAMA_MODEL} -f Modelfile
# Requires Ollama with gemma3 import support (>= 0.4). Do NOT pass --quantize.
FROM ./fused
PARAMETER temperature 0
PARAMETER num_predict 64
EOF

FUSED_SIZE="$(du -sh "${FUSED_DIR}" 2>/dev/null | cut -f1)"
echo "[export] done."
echo "[export] wrote fused model: ${FUSED_DIR} (${FUSED_SIZE})"
echo "[export] wrote Modelfile  : ${OUT_DIR}/Modelfile"
echo
echo "Next: copy '${OUT_DIR}' to the Pi, then run:  ollama create ${OLLAMA_MODEL} -f Modelfile"
echo "See docs/router-on-pi.md for details."
