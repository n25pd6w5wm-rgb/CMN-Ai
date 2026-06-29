# Running the trained Dirigent (Gemma-3 LoRA) on a Raspberry Pi

The routing model — the "Dirigent" — is fine-tuned with **MLX LoRA**, which runs **only on
Apple Silicon**. A Raspberry Pi can't run MLX, so on the Pi we serve the *same* trained model
through **Ollama** as a fused **GGUF** file. The routing logic, prompts and budget rules are
identical to the Mac; only the inference engine differs.

```
Mac (Apple Silicon)                         Raspberry Pi
─────────────────────                       ─────────────────────
train LoRA  ──►  fuse + export GGUF  ──►  copy  ──►  ollama create  ──►  cmn-ai serve
(mlx_lm)         scripts/export-router-gguf.sh        (Ollama)            (strategy: ollama)
```

## 1. On the Mac: export the GGUF

Requires the training extra:

```bash
uv sync --extra train          # installs mlx + mlx-lm (Apple Silicon only)
uv run cmn-ai train            # only if you haven't trained the adapter yet
./scripts/export-router-gguf.sh
```

This reads `router.base_model` / `router.adapter_path` from `config/default.yaml`, fuses the
adapter into the base model, and writes:

- `dist/router-pi/cmn-dirigent.gguf` — the fused model
- `dist/router-pi/Modelfile` — the Ollama build recipe (temperature 0, short output)

## 2. Copy to the Pi

```bash
scp -r dist/router-pi pi@raspberrypi.local:~/cmn-dirigent
```

## 3. On the Pi: install Ollama + build the model

```bash
curl -fsSL https://ollama.com/install.sh | sh     # if Ollama isn't installed yet
cd ~/cmn-dirigent
ollama create cmn-dirigent -f Modelfile
ollama run cmn-dirigent "classify: write a python function"   # smoke test
```

A 1B model in GGUF (~700 MB–1 GB quantized) fits comfortably on a Pi 5 (8 GB).

## 4. Run cmn-ai on the Pi

The Pi profile (`config/pi.yaml`) already selects the Ollama router:

```yaml
router:
  strategy: ollama
  ollama_model: "cmn-dirigent"
```

Then start it (the Pi profile is auto-detected on ARM Linux):

```bash
uv run cmn-ai serve
```

On startup you should see `trained Dirigent via Ollama (cmn-dirigent).` Each request is now
classified by the trained model locally and for free.

## Fallback behaviour

If Ollama is unreachable, the model isn't built, or any call fails, routing automatically
falls back to the deterministic **rule router** — the app keeps working, it just classifies
heuristically instead of with the learned model. To force the rule router, set
`router.strategy: rule`.

## Troubleshooting

- **`ollama create` fails on the GGUF** — make sure the whole `router-pi/` folder was copied
  (the `Modelfile` references the `.gguf` by relative path).
- **`export-router-gguf.sh` says adapter not found** — train it first with `uv run cmn-ai train`
  on the Mac; check `router.adapter_path` in `config/default.yaml`.
- **`mlx_lm fuse` unknown args** — update the training extra: `uv sync --extra train --upgrade`.
- **Slow first response** — Ollama loads the model on first use; subsequent calls are fast.
