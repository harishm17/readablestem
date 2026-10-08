# syntax=docker/dockerfile:1
#
# a11y-stem web app (FastAPI + worker thread) for Cloud Run.
#   docker build -t a11y-stem .
#   docker run --rm -p 8080:8080 -e DATABASE_URL=sqlite:////tmp/a11y.db a11y-stem
#
# Base images are pinned by tag and digest; Dependabot (docker ecosystem) proposes updates.
# Digests read from the registries on 2026-10-07.

FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# ---------------------------------------------------------------- build: locked venv
FROM python:3.12-slim-trixie@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS build

COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first, so this layer is reused until uv.lock changes. No dev or browser
# groups. The tracing extra is included so TRACING_ENABLED works without a rebuild; it
# stays off unless that variable and the Langfuse keys are set.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-editable --no-install-project --extra tracing

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra tracing

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-trixie@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /app \
       --shell /usr/sbin/nologin app

WORKDIR /app
# Owned by root and read-only to the app user: the process cannot rewrite its own code.
COPY --from=build /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

USER 10001:10001
EXPOSE 8080

# Cloud Run ignores HEALTHCHECK (its uptime check calls /healthz); local Docker uses it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"]

CMD ["a11y-stem", "serve", "--host", "0.0.0.0", "--port", "8080"]
