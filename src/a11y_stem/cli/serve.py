"""CLI: a11y-stem serve, users allow|deny|list, db upgrade (WP5 implements)."""

from __future__ import annotations

import argparse


def register(
    group: argparse._SubParsersAction, top: argparse._SubParsersAction | None = None
) -> None:
    """Add this module's subcommands to `group` (top-level). Called by cli.main."""
    return None
