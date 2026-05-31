"""Training shim: run mlx-lm LoRA fine-tuning locally (Apple Silicon GPU).

Invokes the ``mlx_lm.lora`` CLI in a subprocess so we use the upstream, well-tested
training loop rather than re-implementing it. No cloud GPU is involved — MLX uses the
Mac's own GPU. Flags follow mlx-lm's LoRA CLI (verify with ``mlx_lm.lora --help`` if the
installed version differs).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_lora(
    *,
    base_model: str,
    data_dir: Path | str,
    adapter_path: Path | str,
    iters: int = 300,
    num_layers: int = 8,
    batch_size: int = 4,
    learning_rate: float = 1e-4,
) -> None:
    """Run a LoRA fine-tune; writes adapter weights to ``adapter_path``."""
    Path(adapter_path).mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "mlx_lm.lora",
        "--model",
        base_model,
        "--train",
        "--data",
        str(data_dir),
        "--adapter-path",
        str(adapter_path),
        "--iters",
        str(iters),
        "--num-layers",
        str(num_layers),
        "--batch-size",
        str(batch_size),
        "--learning-rate",
        str(learning_rate),
    ]
    print("[cmn-ai] running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
