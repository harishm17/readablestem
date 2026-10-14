"""Where the user cache lives, with the old a11y-stem names still read.

ReadableSTEM was called a11y-stem until October 2026, and its caches (`~/.cache/a11y-stem/...`:
the held-out archive, the table model, the sealed development part) hold files that are slow or
impossible to fetch again. A cache folder under the old name is therefore still used when it
exists and the new one does not; a fresh machine gets the new name.
"""

from __future__ import annotations

import os
from pathlib import Path

NEW_NAME = "readablestem"
OLD_NAME = "a11y-stem"


def cache_home() -> Path:
    """`$XDG_CACHE_HOME`, else `~/.cache`."""
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    return Path(base) if base else Path.home() / ".cache"


def cache_dir(*parts: str, new: str = NEW_NAME, old: str = OLD_NAME) -> Path:
    """`<cache home>/<new>/<parts>`, or the same place under the old name while only that exists."""
    home = cache_home()
    fresh = home.joinpath(new, *parts)
    legacy = home.joinpath(old, *parts)
    if not fresh.exists() and legacy.exists():
        return legacy
    return fresh
