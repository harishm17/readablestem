"""CLI: `readablestem export epub <html> <out>`.

Writes an EPUB 3 from a finished HTML file (the file the pipeline wrote). It reads the file
only: no network, no model, no database. Exit codes: 0 written; 2 the file is missing or
cannot be turned into an EPUB.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from readablestem.pipeline.export_epub import EpubExportError, build_epub, export_epub


def register(group: argparse._SubParsersAction) -> None:
    """Add `export` to the top-level subcommands. Called by cli.main."""
    export = group.add_parser("export", help="write a finished HTML file in another format")
    sub = export.add_subparsers(dest="export_command", required=True)
    epub = sub.add_parser("epub", help="HTML -> EPUB 3 (MathML kept native)")
    epub.add_argument("html", type=Path, help="the finished HTML file")
    epub.add_argument("out", type=Path, help="the .epub file to write")
    epub.add_argument("--title", help="publication title (default: the page's <title>)")
    epub.set_defaults(func=_cmd_epub)


def _cmd_epub(args: argparse.Namespace) -> int:
    try:
        html = args.html.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {args.html}: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    try:
        result = export_epub(html, title=args.title)
    except EpubExportError as exc:
        print(f"cannot write an EPUB: {exc}", file=sys.stderr)
        return 2
    args.out.write_bytes(build_epub(result))
    print(
        f"wrote {args.out}: {result.headings} contents entries, {len(result.images)} images, "
        f"{result.page_labels} page labels"
    )
    return 0
