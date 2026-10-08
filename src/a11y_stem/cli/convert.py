"""CLI: a11y-stem convert FILE.pdf --out OUT.html (WP4 implements)."""

from __future__ import annotations

import argparse


def register(
    group: argparse._SubParsersAction, top: argparse._SubParsersAction | None = None
) -> None:
    """Add this module's subcommands to `group` (top-level). Called by cli.main."""
    return None
