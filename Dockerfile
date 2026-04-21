# Dockerfile for Glama introspection + standalone container deploys.
# Image entrypoint runs the MCP stdio server. Medium creds come from env
# (MEDIUM_INTEGRATION_TOKEN / MEDIUM_SID / MEDIUM_UID) or a mounted
# ~/.cursor/mcp.json (override path with MEDIUM_OPS_MCP_PATH).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /usr/local/bin/uv

COPY pyproject.toml README.md LICENSE ./

COPY src ./src

RUN uv sync --no-dev --extra mcp \
    && uv build --wheel \
    && uv pip install --system dist/*.whl

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=base /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=base /usr/local/bin/medium-ops /usr/local/bin/medium-ops

RUN useradd --uid 10001 --user-group --create-home --home-dir /home/app app
USER app

ENTRYPOINT ["medium-ops", "mcp", "serve"]
