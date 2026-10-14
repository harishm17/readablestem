"""Where the user cache lives: `$XDG_CACHE_HOME/readablestem` or `~/.cache/readablestem`.

The caches (the held-out archive, the table model, the sealed development part) hold files that
are slow or impossible to fetch again.
"""

from __future__ import annotations

import os
from pathlib import Path

NAME = "readablestem"


def cache_home() -> Path:
    """`$XDG_CACHE_HOME`, else `~/.cache`."""
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    return Path(base) if base else Path.home() / ".cache"


def cache_dir(*parts: str, name: str = NAME) -> Path:
    """`<cache home>/<name>/<parts>`."""
    return cache_home().joinpath(name, *parts)
