"""Command-line entry point: ``cmn-ai`` (info), ``serve``, ``train``, ``eval``."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from cmn_ai import __version__
from cmn_ai.config import load_settings

if TYPE_CHECKING:
    from cmn_ai.training.dataset import LabeledPrompt


def _info() -> None:
    settings = load_settings()
    print(f"cmn-ai {__version__}")
    print(f"profile: {settings.profile}")
    print(f"router strategy: {settings.router.strategy}")
    print(f"monthly budget: {settings.budget.monthly_budget_eur:.2f} EUR")
    enabled = [name for name, a in settings.agents.items() if a.enabled]
    print(f"enabled agents: {', '.join(enabled) or '(none)'}")


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("cmn_ai.web.app:create_app", host=host, port=port, factory=True)


def _build_samples() -> list[LabeledPrompt]:
    """Combine the curated seed with any logged decisions into labeled samples."""
    from cmn_ai.storage.decisions import DecisionLog
    from cmn_ai.training.dataset import SEED_PATH, build_samples

    settings = load_settings()
    db = settings.storage.resolved_path
    log = DecisionLog(db) if db.exists() else None
    return build_samples(SEED_PATH, log)


def _train(iters: int) -> None:
    from cmn_ai.training.dataset import split, write_dataset
    from cmn_ai.training.mlx_finetune import run_lora

    settings = load_settings()
    samples = _build_samples()
    train, valid, test = split(samples, seed=0)
    print(f"[cmn-ai] dataset: {len(train)} train / {len(valid)} valid / {len(test)} test")

    adapter = settings.router.resolved_adapter_path
    data_dir = adapter.parent / "router-data"
    write_dataset(data_dir, train, valid, test)
    run_lora(
        base_model=settings.router.base_model,
        data_dir=data_dir,
        adapter_path=adapter,
        iters=iters,
    )
    print(f"[cmn-ai] adapter written to {adapter}")


def _eval() -> None:
    from cmn_ai.core import Task
    from cmn_ai.router.mlx_router import MLXRouter
    from cmn_ai.router.rule_router import RuleRouter
    from cmn_ai.training.dataset import split
    from cmn_ai.training.evaluate import evaluate
    from cmn_ai.training.mlx_backend import build_generate_fn

    settings = load_settings()
    samples = _build_samples()
    _, _, test = split(samples, seed=0)

    rule = RuleRouter(on_limit=settings.budget.on_limit)
    baseline = evaluate(test, lambda p: rule.classify(Task(prompt=p)))

    generate_fn = build_generate_fn(
        base_model=settings.router.base_model,
        adapter_path=settings.router.resolved_adapter_path,
        max_new_tokens=settings.router.max_new_tokens,
    )
    mlx = MLXRouter(rule_router=rule, generate_fn=generate_fn)
    model = evaluate(test, lambda p: mlx.classify(Task(prompt=p)))

    print(f"test samples: {model.n}")
    print("                 capability  complexity  needs_web   exact")
    print(
        f"  rule baseline  {baseline.capability_acc:>9.2f}  {baseline.complexity_acc:>9.2f}  "
        f"{baseline.needs_web_acc:>8.2f}  {baseline.exact_acc:>6.2f}"
    )
    print(
        f"  trained (mlx)  {model.capability_acc:>9.2f}  {model.complexity_acc:>9.2f}  "
        f"{model.needs_web_acc:>8.2f}  {model.exact_acc:>6.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="cmn-ai")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the chat web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    train = sub.add_parser("train", help="LoRA fine-tune the routing model locally (MLX)")
    train.add_argument("--iters", type=int, default=300)

    sub.add_parser("eval", help="evaluate the trained router vs the rule baseline")

    args = parser.parse_args()
    if args.command == "serve":
        _serve(args.host, args.port)
    elif args.command == "train":
        _train(args.iters)
    elif args.command == "eval":
        _eval()
    else:
        _info()
