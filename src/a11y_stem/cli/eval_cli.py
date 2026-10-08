"""CLI: a11y-stem eval pipeline|gate|calibrate|render, demo build (WP7)."""

from __future__ import annotations

import argparse


def register(
    group: argparse._SubParsersAction, top: argparse._SubParsersAction | None = None
) -> None:
    """Add subcommands. `group` is the `eval` subparsers (add pipeline, gate, ...);
    `top` is the top-level subparsers (add `demo`). Called by cli.main.build_parser."""
    return None
