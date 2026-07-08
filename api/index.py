"""Vercel serverless entry point for the cmn-ai web app.

Vercel's Python runtime serves the module-level ``app`` (an ASGI FastAPI
instance) for every request routed here by ``vercel.json``. Unlike Render (which
builds the Dockerfile and runs uvicorn), Vercel installs ``requirements.txt`` and
imports this file directly — so we put the package's ``src`` layout on the path
and default the profile to ``vercel`` (writable ``/tmp`` DB, no persistent disk).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The package lives under ``src/`` (uv/src layout). Vercel does not ``pip install``
# the project itself, so make ``cmn_ai`` importable before we touch it.
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Serverless: no persistent disk. Use the render-like hosted profile but with a
# ``/tmp`` SQLite path (ledger/decision-log only — conversations live in Supabase).
os.environ.setdefault("CMN_AI_PROFILE", "vercel")

from cmn_ai.web.app import create_app  # noqa: E402

app = create_app()
