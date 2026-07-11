# cmn-ai web app — container image for hosting the frontend/orchestrator (e.g. Render).
# The local model is NOT in here: it runs on the Raspberry Pi and is reached via
# OLLAMA_HOST. This image serves the chat UI + API and routes to the Pi / paid APIs.
# Python 3.12 matches .python-version (which uv sync respects inside the container)
# and Vercel's runtime, so every host runs the same interpreter.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    CMN_AI_PROFILE=render

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . .
# Install runtime deps + the project (no dev tools, no Apple-only train extra).
RUN uv sync --frozen --no-dev

EXPOSE 8000
# Render injects $PORT; bind all interfaces.
CMD ["sh", "-c", "uv run uvicorn cmn_ai.web.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
