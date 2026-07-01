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


def _vault_serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("cmn_ai.web.vault_service:vault_app_factory", host=host, port=port, factory=True)


_TABLE_SQL = (
    "create table if not exists api_keys (\n"
    "  name  text primary key,\n"
    "  value text not null\n"
    ");\n"
    "alter table api_keys enable row level security;"
)


def _prompt(label: str, default: str = "") -> str:
    value = input(f"{label}{f' [{default}]' if default else ''}: ").strip()
    return value or default


def _copy_to_clipboard(text: str) -> bool:
    import shutil
    import subprocess

    tool = shutil.which("pbcopy") or shutil.which("xclip")
    if not tool:
        return False
    try:
        subprocess.run([tool], input=text.encode(), check=True)
        return True
    except Exception:
        return False


def _setup() -> None:
    """Interactive wizard: configure Supabase + store model API keys, end to end."""
    import getpass
    from urllib.parse import urlparse

    from cmn_ai.keystore import (
        load_dotenv,
        load_supabase_keys,
        table_exists,
        update_dotenv,
        upsert_keys,
    )

    print("=== cmn-ai Supabase setup ===\n")
    existing: dict[str, str] = {}
    load_dotenv(".env", env=existing)

    url = _prompt("Supabase project URL", existing.get("SUPABASE_URL", "")).rstrip("/")
    key_default = existing.get("SUPABASE_KEY", "")
    hint = " [keep existing]" if key_default else ""
    service_key = getpass.getpass(f"Supabase service-role key{hint}: ").strip() or key_default
    if not url or not service_key:
        print("Need both a URL and a service key. Aborting.")
        return

    update_dotenv(".env", {"SUPABASE_URL": url, "SUPABASE_KEY": service_key})
    print("✓ wrote .env\n")

    try:
        ok = table_exists(url=url, service_key=service_key)
    except Exception as exc:
        print(f"Could not reach Supabase ({exc}). Check the URL/key. Aborting.")
        return

    if not ok:
        print("The 'api_keys' table is missing — create it once in the SQL editor:\n")
        print(_TABLE_SQL + "\n")
        if _copy_to_clipboard(_TABLE_SQL):
            print("(SQL copied to your clipboard.)")
        project = (urlparse(url).hostname or "").split(".")[0]
        if project:
            print(f"Open: https://supabase.com/dashboard/project/{project}/sql/new")
        input("\nPress Enter once the table exists… ")
        try:
            ok = table_exists(url=url, service_key=service_key)
        except Exception as exc:
            print(f"Re-check failed ({exc}). Aborting.")
            return
        if not ok:
            print("Still can't see the table. Aborting.")
            return
    print("✓ table reachable\n")

    settings = load_settings()
    env_names = sorted({c.api_key_env for c in settings.agents.values() if c.api_key_env})
    print("Enter your model API keys (leave blank to skip):")
    to_set: dict[str, str] = {}
    for name in env_names:
        value = getpass.getpass(f"  {name}: ").strip()
        if value:
            to_set[name] = value

    if to_set:
        try:
            upsert_keys(to_set, url=url, service_key=service_key)
        except Exception as exc:
            print(f"Failed to store keys ({exc}). Aborting.")
            return
        print(f"\n✓ stored {len(to_set)} key(s)")
    else:
        print("\n(no new keys entered)")

    try:
        stored = load_supabase_keys(url=url, service_key=service_key)
    except Exception:
        stored = {}
    enabled = sorted(
        n for n, c in settings.agents.items() if c.api_key_env and c.api_key_env in stored
    )
    print("Keys in Supabase:", ", ".join(sorted(stored)) or "(none)")
    print("Agents that will activate:", ", ".join(enabled) or "(local only)")
    print("\nDone. Start the app with:  uv run cmn-ai serve")


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

    sub.add_parser("setup", help="interactive Supabase + API-key setup wizard")

    serve = sub.add_parser("serve", help="run the chat web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    vault = sub.add_parser("vault-serve", help="serve the on-Pi markdown vault (search + upload)")
    vault.add_argument("--host", default="0.0.0.0")
    vault.add_argument("--port", type=int, default=11435)

    train = sub.add_parser("train", help="LoRA fine-tune the routing model locally (MLX)")
    train.add_argument("--iters", type=int, default=300)

    sub.add_parser("eval", help="evaluate the trained router vs the rule baseline")

    args = parser.parse_args()
    if args.command == "setup":
        _setup()
    elif args.command == "serve":
        _serve(args.host, args.port)
    elif args.command == "vault-serve":
        _vault_serve(args.host, args.port)
    elif args.command == "train":
        _train(args.iters)
    elif args.command == "eval":
        _eval()
    else:
        _info()
