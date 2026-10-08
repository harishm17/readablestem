"""Versioned prompts. One Markdown file per prompt in this directory.

The version is the file stem plus the first 8 hex digits of the SHA-256 of the text, so
any edit to a prompt changes its version. Versions are recorded on every `ModelCall` and
in `DocumentResult.prompt_versions`, and the CI eval gate keys its baseline on them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str  # "<name>-<sha8>"
    text: str


@cache
def load_prompt(name: str) -> Prompt:
    """Load `<name>.md` from this package. Raises FileNotFoundError if missing."""
    text = resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return Prompt(name=name, version=f"{name}-{digest}", text=text)
