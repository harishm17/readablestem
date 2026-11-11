"""Addresses that exist only where people build the site (WS12.6, F-180, F-187).

One rule says which addresses a production server answers. A page for builders, or a file
only they need, is listed here and is not mounted in production, so it answers 404 like any
unknown address.

- `available(settings)` is the one test. It follows `Settings.env`, the setting production
  is decided by (`env == "prod"`, which also turns on HSTS and `check_production`).
- `DEV_PATHS` lists the dev-only addresses. `/style` is a router (`routes/dev.py`) that
  `create_app` includes only when `available` is true. `/dev-login` stays in the sign-in
  router and answers 404 through the same test (`deps.AppContext.dev_login_enabled`),
  because sign-in tests need it in the same app.
- Nothing a builder needs sits under `web/static/`: that folder is served whole at
  `/static`. Stored data (the demo's source PDF and its recorded result) lives in a data
  folder beside it (`web/demo/`) that routes read, so it has no second, unsandboxed address.
  `tests/test_web_pages.py` sweeps a production app for both.
"""

from __future__ import annotations

from readablestem.config import Settings

DEV_ENVS = frozenset({"dev", "test"})

# Addresses a production server must not answer (the sweep in tests/test_web_pages.py).
DEV_PATHS = ("/style", "/dev-login")


def available(settings: Settings) -> bool:
    """True where builder pages exist: any environment other than production."""
    return settings.env in DEV_ENVS
