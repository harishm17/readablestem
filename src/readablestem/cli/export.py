"""CLI: `readablestem export epub <html> <out>` and `readablestem export docx <html> <out>`.

Writes an EPUB 3 or a Word file from a finished HTML file (the file the pipeline wrote). It
reads the file only: no network, no model, no database. The Word file needs pandoc on PATH.
Exit codes: 0 written; 2 the file is missing or cannot be turned into that file type.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from readablestem.pipeline.export_canvas import CanvasExportError
from readablestem.pipeline.export_docx import DocxExportError, export_docx
from readablestem.pipeline.export_epub import build_epub, export_epub


def register(group: argparse._SubParsersAction) -> None:
    """Add `export` to the top-level subcommands. Called by cli.main."""
    export = group.add_parser("export", help="write a finished HTML file in another format")
    sub = export.add_subparsers(dest="export_command", required=True)
    epub = sub.add_parser("epub", help="HTML -> EPUB 3 (MathML kept native)")
    epub.add_argument("html", type=Path, help="the finished HTML file")
    epub.add_argument("out", type=Path, help="the .epub file to write")
    epub.add_argument("--title", help="publication title (default: the page's <title>)")
    epub.set_defaults(func=_cmd_epub)
    docx = sub.add_parser("docx", help="HTML -> Word (.docx) with native equations (needs pandoc)")
    docx.add_argument("html", type=Path, help="the finished HTML file")
    docx.add_argument("out", type=Path, help="the .docx file to write")
    docx.add_argument("--title", help="document title (default: the page's <title>)")
    docx.set_defaults(func=_cmd_docx)


def _cmd_epub(args: argparse.Namespace) -> int:
    try:
        html = args.html.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {args.html}: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    try:
        result = export_epub(html, title=args.title)
        data = build_epub(result)
    except CanvasExportError as exc:  # also EpubExportError: a picture the book cannot hold
        print(f"cannot write an EPUB: {exc}", file=sys.stderr)
        return 2
    try:
        args.out.write_bytes(data)
    except OSError as exc:
        print(f"cannot write {args.out}: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    print(
        f"wrote {args.out}: {result.headings} contents entries, {len(result.images)} images, "
        f"{result.page_labels} page labels"
    )
    return 0


def _cmd_docx(args: argparse.Namespace) -> int:
    try:
        html = args.html.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {args.html}: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    try:
        result = export_docx(html, title=args.title)
    except DocxExportError as exc:
        print(f"cannot write a Word file: {exc.code}", file=sys.stderr)
        return 2
    try:
        args.out.write_bytes(result.data)
    except OSError as exc:
        print(f"cannot write {args.out}: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    print(
        f"wrote {args.out}: {result.equations} equations, {result.headings} headings, "
        f"{result.images} pictures with alt text, {result.tables} tables"
    )
    return 0
