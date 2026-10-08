"""CLI commands: a11y-stem ops aggregate|purge (WP6 implements)."""

from __future__ import annotations

import argparse


def register(
    group: argparse._SubParsersAction, top: argparse._SubParsersAction | None = None
) -> None:
    """Add this module's subcommands to `group` (top-level). Called by cli.main."""
    return None
