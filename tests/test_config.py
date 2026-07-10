"""Tests for layered YAML configuration loading."""

from __future__ import annotations

import pytest

from cmn_ai.config import OnLimit, load_settings
from cmn_ai.core import Bucket


def test_load_mac_profile_uses_default_budget() -> None:
    settings = load_settings(profile="mac")
    assert settings.profile == "mac"
    assert settings.budget.monthly_budget_eur == 35.0
    assert settings.budget.on_limit is OnLimit.BLOCK
    assert settings.budget.bucket_split[Bucket.CODING] == 0.4


def test_mac_profile_overrides_local_model() -> None:
    settings = load_settings(profile="mac")
    assert settings.agents["local"].model == "gemma4:e4b"


def test_pi_profile_overrides_local_model() -> None:
    settings = load_settings(profile="pi")
    assert settings.agents["local"].model == "gemma3:1b"


def test_coding_agent_uses_coding_bucket() -> None:
    settings = load_settings(profile="mac")
    assert settings.agents["coding"].bucket is Bucket.CODING


def test_api_key_resolved_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    settings = load_settings(profile="mac")
    assert settings.api_key_for("anthropic") == "sk-test-123"
    assert settings.api_key_for("local") is None


def test_profile_override_wins_over_detection() -> None:
    # Explicit profile argument must be honored regardless of host platform.
    settings = load_settings(profile="pi")
    assert settings.profile == "pi"


def test_render_profile_ships_public_supabase_values() -> None:
    # The login wall must come up on Render without manual dashboard steps: the
    # public (client-side) Supabase URL + anon key ride along in the profile.
    settings = load_settings("render")
    assert settings.supabase_url and settings.supabase_url.startswith("https://")
    assert settings.supabase_anon_key and settings.supabase_anon_key.startswith("sb_publishable_")
