"""A minimal local check of code blocks and glossary entries on the dev-v2 development set.

Backlog T2-16 (docs/eval.md, "Structure check"). The headings task scores headings; nothing
scored code or glossary entries, whose gold sits in the dev set (`kind` "code" and
"glossary"). This converts every dev document that has such gold with the offline fake
client (no model is called for the structure; the equations are placeholders) and reads
the output HTML:

- code: a gold block is *exact* when some `<pre>` holds its text line for line, indentation
  included (trailing spaces aside), and *flat* when it does when each line's indentation is
  ignored (the right words in the right lines, the layout lost);
- glossary: a gold entry is *term found* when a `<dt>` holds its term and *entry right*
  when the `<dd>` after it also starts with the first words of its definition. Terms and
  definitions are compared by their ASCII letters and digits only, so the maths in a term
  (a placeholder here) does not decide it.

Run: `uv run python -m readablestem.eval.structure_check [--out REPORT.json]`. The dev part
only: it reads `data/dev-v2/dev` and the dev pages printed into `.cache/eval/pages` by
`eval pipeline --set dev-v2`; it never opens the sealed check part.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

DEFINITION_WORDS = 25  # letters and digits of a definition that must agree
PLACEHOLDER = "(equation not converted)"


class _Reader(HTMLParser):
    """The `<pre>` texts and the `<dt>`/`<dd>` pairs of an output document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pre: list[str] = []
        self.entries: list[tuple[str, str]] = []
        self._into: str | None = None
        self._buffer: list[str] = []
        self._term: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("pre", "dt", "dd"):
            self._into, self._buffer = tag, []

    def handle_data(self, data: str) -> None:
        if self._into:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._into:
            return
        text = "".join(self._buffer)
        if tag == "pre":
            self.pre.append(text)
        elif tag == "dt":
            self._term = text
        elif self._term is not None:  # dd
            self.entries.append((self._term, text))
            self._term = None
        self._into = None


def read_html(html: str) -> _Reader:
    reader = _Reader()
    reader.feed(html)
    return reader


def letters(text: str) -> str:
    """ASCII letters and digits only, case-folded: the comparison key of terms and
    definitions."""
    return "".join(
        c for c in text.replace(PLACEHOLDER, "").casefold() if c.isascii() and c.isalnum()
    )


def code_lines(text: str, *, indent: bool) -> list[str]:
    lines = [line.rstrip() for line in text.strip("\n").split("\n")]
    return lines if indent else [line.lstrip() for line in lines]


@dataclass
class Counts:
    gold_code: int = 0
    code_exact: int = 0
    code_flat: int = 0
    pre_found: int = 0
    gold_glossary: int = 0
    term_found: int = 0
    entry_right: int = 0
    dl_entries: int = 0
    documents: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        def share(a: int, b: int) -> float | None:
            return round(a / b, 4) if b else None

        return {
            "documents": self.documents,
            "code": {
                "gold": self.gold_code,
                "pre_blocks_in_output": self.pre_found,
                "exact": self.code_exact,
                "flat": self.code_flat,
                "exact_share": share(self.code_exact, self.gold_code),
                "flat_share": share(self.code_flat, self.gold_code),
            },
            "glossary": {
                "gold": self.gold_glossary,
                "dl_entries_in_output": self.dl_entries,
                "term_found": self.term_found,
                "entry_right": self.entry_right,
                "term_share": share(self.term_found, self.gold_glossary),
                "entry_share": share(self.entry_right, self.gold_glossary),
            },
        }


def score(gold: list[dict], html: str, counts: Counts) -> None:
    reader = read_html(html)
    counts.pre_found += len(reader.pre)
    counts.dl_entries += len(reader.entries)
    for record in gold:
        if record["kind"] == "code":
            counts.gold_code += 1
            want = code_lines(record["text"], indent=True)
            flat = code_lines(record["text"], indent=False)
            if any(code_lines(p, indent=True) == want for p in reader.pre):
                counts.code_exact += 1
            if any(code_lines(p, indent=False) == flat for p in reader.pre):
                counts.code_flat += 1
        elif record["kind"] == "glossary":
            counts.gold_glossary += 1
            term = letters(record["term"])
            definition = letters(record["definition"])[:DEFINITION_WORDS]
            for dt, dd in reader.entries:
                if letters(dt) == term:
                    counts.term_found += 1
                    got = letters(dd)
                    if got.startswith(definition) or definition.startswith(got[:DEFINITION_WORDS]):
                        counts.entry_right += 1
                    break


def dev_documents(root: Path, cache: Path) -> list[tuple[str, Path, list[dict]]]:
    """(name, PDF, gold records) of every dev document with code or glossary gold."""
    found: dict[str, tuple[Path | None, list[dict]]] = {}
    for gold_file in sorted((root / "dev").rglob("*.jsonl")):
        rel = gold_file.relative_to(root / "dev")
        records = [json.loads(line) for line in gold_file.read_text("utf-8").splitlines() if line]
        records = [r for r in records if r.get("kind") in ("code", "glossary")]
        if not records:
            continue
        if rel.parts[0] == "authored":
            name = rel.parts[1]
            pdf: Path | None = gold_file.parent / f"{name}.pdf"
        else:
            name = f"{rel.parts[1]}-{rel.stem}"
            matches = sorted(cache.glob(f"{name}-*.pdf"), key=lambda p: p.stat().st_mtime)
            pdf = matches[-1] if matches else None
        found[name] = (pdf, records)
    return [(n, p, r) for n, (p, r) in found.items() if p is not None and p.exists()]


def run(root: Path, cache: Path) -> dict:
    from readablestem.config import get_settings
    from readablestem.models.fake import FakeVisionClient
    from readablestem.pipeline import run as pipeline_run

    counts = Counts()
    for name, pdf, gold in dev_documents(root, cache):
        output = pipeline_run.convert_pdf(
            pdf.read_bytes(), settings=get_settings(), client=FakeVisionClient()
        )
        score(gold, output.result.html, counts)
        counts.documents.append(name)
    return counts.as_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--devset-dir", type=Path, default=Path("data/dev-v2"))
    parser.add_argument("--cache", type=Path, default=Path(".cache/eval/pages"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = run(args.devset_dir, args.cache)
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
