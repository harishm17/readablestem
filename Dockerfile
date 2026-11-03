# ReadableSTEM web app (FastAPI + worker thread). Production builds this file on Railway
# (docs/deploy-railway.md); the Cloud Run path (deploy/deploy.sh) builds it too.
#   docker build -t readablestem .
#   docker run --rm -p 8080:8080 -e DATABASE_URL=sqlite:////tmp/a11y.db readablestem
#
# Base images are pinned by tag and digest; Dependabot (docker ecosystem) proposes updates.
# Digests read from the registries on 2026-10-07.
#
# No BuildKit-only syntax (RUN --mount, COPY --chmod/--link, heredocs, a `# syntax=` line).
# `gcloud builds submit --tag` (deploy/deploy.sh) runs a plain `docker build` in Cloud
# Build's docker 20.10 builder image, which has no buildx and can fall back to the legacy
# builder; the legacy builder rejects those flags. CI's docker job builds this file with
# DOCKER_BUILDKIT=0 as well, and tests/test_ops_repo.py checks for the syntax.

FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv

# ---------------------------------------------------------------- build: locked venv
FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build

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
# The showcase served at /showcase/ (web.seo): the static reviewer demo that site/build.py
# writes from committed files (docs/site.md). No model, no network, no clock. It is written
# into the package before the install below, so the installed package carries it.
RUN PYTHONPATH=/app/src /app/.venv/bin/python site/build.py --out /app/src/readablestem/web/showcase
RUN uv sync --locked --no-dev --no-editable --extra tracing

# The SLANet-plus table-structure model (7.5 MB, Apache-2.0; see THIRD_PARTY_NOTICES.md),
# downloaded once here and refused unless its SHA-256 matches the pin in
# src/readablestem/pipeline/table_model.py. Without it, tables use PyMuPDF's own grid.
RUN /app/.venv/bin/python -m readablestem.pipeline.table_model --dir /app/models

# ---------------------------------------------------------------- local handwriting reader
# Optional (docs/math-readers.md): the TexTeller model files (Apache-2.0; THIRD_PARTY_NOTICES.md),
# fetched at a pinned revision, each file refused unless its SHA-256 matches, then rewritten with
# int8 weights (2.1 GB of float32 becomes about 0.6 GB; the float32 files would not fit the
# conversion child's memory cap). Built only with
#   docker build --build-arg INSTALL_MATH_READER=true .
# (on Railway: the service variable INSTALL_MATH_READER=true). The build needs the network, the
# running service never does. onnx and onnxruntime live in this stage only: the application
# needs onnxruntime, which the locked environment already has.
FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS math-reader
ARG INSTALL_MATH_READER=false
COPY src/readablestem/models/math_readers/local_weights.py /opt/local_weights.py
RUN mkdir /out \
    && if [ "$INSTALL_MATH_READER" = "true" ]; then \
        pip install --no-cache-dir "onnx==1.23.2" "onnxruntime==1.31.0" \
        && python -I /opt/local_weights.py --dir /out; \
    fi

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /app \
       --shell /usr/sbin/nologin app

# Word and PowerPoint uploads (T3-30) need LibreOffice's Writer and Impress components, run
# headless in a limited child process (src/readablestem/web/office_convert.py). They add a few
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
# Empty unless the image was built with INSTALL_MATH_READER=true. Nothing reads it while
# MATH_READER_LOCAL_ENABLED is off (the default).
COPY --from=math-reader /out /app/math-reader

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    TABLE_MODEL_DIR=/app/models \
    MATH_READER_LOCAL_DIR=/app/math-reader

USER 10001:10001
EXPOSE 8080

# Railway's deploy check is its own setting (/healthz, in .railway/railway.ts); local Docker
# uses this one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"]

CMD ["readablestem", "serve", "--host", "0.0.0.0", "--port", "8080"]
