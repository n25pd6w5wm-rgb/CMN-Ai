"""Command-line entry point. Expanded in later phases (serve, login, ask)."""

from __future__ import annotations

from cmn_ai import __version__
from cmn_ai.config import load_settings


def main() -> None:
    """Print the resolved profile and budget — a smoke check that config loads."""
    settings = load_settings()
    print(f"cmn-ai {__version__}")
    print(f"profile: {settings.profile}")
    print(f"monthly budget: {settings.budget.monthly_budget_eur:.2f} EUR")
    enabled = [name for name, a in settings.agents.items() if a.enabled]
    print(f"enabled agents: {', '.join(enabled) or '(none)'}")
