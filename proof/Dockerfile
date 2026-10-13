# One-command reproduction of the public proof on a clean machine (docs/proof.md).
#   docker build -f proof/Dockerfile -t a11y-stem-proof .
#   docker run --rm -v "$PWD/out:/out" a11y-stem-proof a11y-stem proof --out /out/PROOF.md
#
# Two targets:
#   replay (default)  locked dependencies and the repository; no browser. Replays the
#                     recorded run: no key, no network, no cost.
#   full              adds Chromium and the print fonts, for `proof --verify-sources` and
#                     `proof --live` (which needs OPENAI_API_KEY and --max-usd).
#
# Base images and the uv image are the ones the production Dockerfile pins (same digests).
# No BuildKit-only syntax (RUN --mount, COPY --chmod/--link, heredocs, a `# syntax=` line).
# The build context is the repository root, filtered by .dockerignore (no .env, no private
# inputs, no tests, no .git).

FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# ------------------------------------------------------------------- locked dependencies
FROM python:3.12-slim-trixie@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS build

COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

COPY pyproject.toml uv.lock /app/
RUN uv sync --locked --no-dev --no-install-project
COPY . /app
RUN uv sync --locked --no-dev --no-editable

# ------------------------------------------------------------------- full: with Chromium
FROM build AS full-build
# The browser group is only the Python package; the browser itself is installed next, into
# a folder every user can read (the container runs as a non-root user).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN uv sync --locked --no-dev --no-editable --group browser \
    && /app/.venv/bin/python -m playwright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core fonts-liberation \
    && apt-get clean \
    && chmod -R a+rX /ms-playwright

# ------------------------------------------------------------------------------ runtimes
FROM full-build AS full
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app \
       --shell /usr/sbin/nologin app \
    && mkdir -p /out /home/app/.cache && chown -R app:app /out /home/app /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    A11Y_PROOF_CACHE=/tmp/a11y-proof
USER 10001:10001
WORKDIR /app
CMD ["a11y-stem", "proof"]

# The default target is the last one: replay only, no browser.
FROM build AS replay
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app \
       --shell /usr/sbin/nologin app \
    && mkdir -p /out && chown -R app:app /out /home/app /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    A11Y_PROOF_CACHE=/tmp/a11y-proof
USER 10001:10001
WORKDIR /app
CMD ["a11y-stem", "proof"]
