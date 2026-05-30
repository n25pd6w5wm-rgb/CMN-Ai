"""Command-line entry point: ``cmn-ai`` (info) and ``cmn-ai serve`` (web UI)."""

from __future__ import annotations

import argparse

from cmn_ai import __version__
from cmn_ai.config import load_settings


def _info() -> None:
    settings = load_settings()
    print(f"cmn-ai {__version__}")
    print(f"profile: {settings.profile}")
    print(f"monthly budget: {settings.budget.monthly_budget_eur:.2f} EUR")
    enabled = [name for name, a in settings.agents.items() if a.enabled]
    print(f"enabled agents: {', '.join(enabled) or '(none)'}")


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("cmn_ai.web.app:create_app", host=host, port=port, factory=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="cmn-ai")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the chat web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    if args.command == "serve":
        _serve(args.host, args.port)
    else:
        _info()
