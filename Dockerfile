# a11y-stem web app (FastAPI + worker thread) for Cloud Run.
#   docker build -t a11y-stem .
#   docker run --rm -p 8080:8080 -e DATABASE_URL=sqlite:////tmp/a11y.db a11y-stem
#
# Base images are pinned by tag and digest; Dependabot (docker ecosystem) proposes updates.
# Digests read from the registries on 2026-10-07.
#
# No BuildKit-only syntax (RUN --mount, COPY --chmod/--link, heredocs, a `# syntax=` line).
# `gcloud builds submit --tag` (deploy/deploy.sh) runs a plain `docker build` in Cloud
# Build's docker 20.10 builder image, which has no buildx and can fall back to the legacy
# builder; the legacy builder rejects those flags. CI's docker job builds this file with
# DOCKER_BUILDKIT=0 as well, and tests/test_ops_repo.py checks for the syntax.

FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# ---------------------------------------------------------------- build: locked venv
FROM python:3.12-slim-trixie@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS build

COPY --from=uv /uv /bin/uv
# UV_NO_CACHE: the download cache would only bloat this stage (Cloud Build runs with
# --no-cache anyway, so a cache mount would gain nothing there).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first, so this layer is reused until uv.lock changes. No dev or browser
# groups. The tracing extra is included so TRACING_ENABLED works without a rebuild; it
# stays off unless that variable and the Langfuse keys are set.
COPY pyproject.toml uv.lock /app/
RUN uv sync --locked --no-dev --no-editable --no-install-project --extra tracing

COPY . /app
RUN uv sync --locked --no-dev --no-editable --extra tracing

# The SLANet-plus table-structure model (7.5 MB, Apache-2.0; see THIRD_PARTY_NOTICES.md),
# downloaded once here and refused unless its SHA-256 matches the pin in
# src/a11y_stem/pipeline/table_model.py. Without it, tables use PyMuPDF's own grid.
RUN /app/.venv/bin/python -m a11y_stem.pipeline.table_model --dir /app/models

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-trixie@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /app \
       --shell /usr/sbin/nologin app

# Word and PowerPoint uploads (T3-30) need LibreOffice's Writer and Impress components, run
# headless in a limited child process (src/a11y_stem/web/office_convert.py). They add a few
# hundred megabytes to the image and about 300 MB of memory while a file converts, so they
# are installed only when asked for: docker build --build-arg INSTALL_LIBREOFFICE=true .
# Without them the upload form takes PDFs only and says so (docs/deploy.md).
ARG INSTALL_LIBREOFFICE=false
RUN if [ "$INSTALL_LIBREOFFICE" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends \
            libreoffice-writer-nogui libreoffice-impress-nogui \
            fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 \
        && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app
# Owned by root and read-only to the app user: the process cannot rewrite its own code.
COPY --from=build /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    TABLE_MODEL_DIR=/app/models

USER 10001:10001
EXPOSE 8080

# Cloud Run ignores HEALTHCHECK (its uptime check calls /healthz); local Docker uses it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"]

CMD ["a11y-stem", "serve", "--host", "0.0.0.0", "--port", "8080"]
