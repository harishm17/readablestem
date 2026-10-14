"""Build the authored documents of the extended development set (dev-v2) and check them.

Original documents (written for this project, MIT licence, no third-party text) in three
toolchains, each with its source, an untagged PDF and a `gold.jsonl` of known answers:

- LaTeX through Tectonic: latex-exercises, latex-matrices, latex-chemistry, latex-slides,
  latex-structure, latex-hyphenation
- Word (python-docx) exported to PDF by LibreOffice: word-lecture, word-eq-images
- HTML printed by Chromium: html-slides, html-code-glossary

Build (from the repository root):

    uv run --group browser --with python-docx --with pyphen python \
        data/dev-v2/authored/build_authored.py --out /tmp/dsv2-authored/pool \
        --tectonic /tmp/dsv2-tectonic/tectonic

`--only latex-exercises word-lecture ...` builds some documents. Four documents also have a
"check" variant (`<id>-chk`, a fresh draw from the same generators with other numbers,
words and elements): latex-exercises, latex-chemistry, word-lecture, word-eq-images.

Check what was built (gold parses, MathML converts, headings/terms/code/table cells are in
the PDF text layer, no structure tree, sizes, class counts, dev/check disjointness):

    uv run python data/dev-v2/authored/build_authored.py --check /tmp/dsv2-authored/pool

Needs: Tectonic, LibreOffice (`soffice`), `uv run --group browser` Chromium, python-docx
(not a project dependency: `--with python-docx`) and, for hyphenation in the Word documents,
the LibreOffice English hyphenation dictionary `hyph_en_US.dic` (found in $A11Y_HYPH_DIR,
$DICPATH, /usr/share/hyphen, or inside the `pyphen` package: `--with pyphen`).

Builds are deterministic: seeded generators, no clock, fixed PDF dates.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import authored_core as core  # noqa: E402

from readablestem.eval.equations import latex_to_mathml  # noqa: E402

ORDER = [
    "latex-exercises",
    "latex-matrices",
    "latex-chemistry",
    "latex-slides",
    "latex-structure",
    "latex-hyphenation",
    "word-lecture",
    "word-eq-images",
    "html-slides",
    "html-code-glossary",
]
CHECK_BASES = ["latex-exercises", "latex-chemistry", "word-lecture", "word-eq-images"]
SUFFIX = {"latex": ".tex", "word": ".docx", "html": ".html"}
SHORT = {
    "latex-exercises": "exercises",
    "latex-matrices": "matrices",
    "latex-chemistry": "chemistry",
    "latex-slides": "slides-tex",
    "latex-structure": "structure",
    "latex-hyphenation": "hyphen",
    "word-lecture": "lecture",
    "word-eq-images": "eq-images",
    "html-slides": "slides-html",
    "html-code-glossary": "code-gloss",
}


def builders() -> dict:
    out: dict = {}
    import authored_latex

    out.update(authored_latex.BUILDERS)
    for name in ("authored_word", "authored_html"):
        try:
            mod = __import__(name)
        except ImportError as exc:  # python-docx missing, or the module is not written yet
            print(f"note: {name} unavailable ({exc}); its documents are skipped")
            continue
        out.update(mod.BUILDERS)
    return out


# --------------------------------------------------------------------------- build


def build_one(base: str, variant: str, fn, out: Path, args) -> Path:
    built = fn(variant) if base in CHECK_BASES else fn()
    doc = built.doc
    folder = out / doc.doc_id
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / (doc.doc_id + SUFFIX[doc.family])
    if isinstance(built.source, bytes):
        src.write_bytes(built.source)
    else:
        src.write_text(built.source, encoding="utf-8")
    for name, data in built.assets.items():
        path = folder / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
    labels: dict[str, int] = {}
    if doc.family == "latex":
        pdf, aux = core.run_tectonic(src, args.tectonic)
        labels = core.label_pages(aux)
    elif doc.family == "word":
        pdf = core.fix_pdf(core.run_soffice(src, core.find_hyph_dir()))
    else:
        pdf = core.fix_pdf(core.print_html(src.read_text(encoding="utf-8")))
    if core.has_struct_tree(pdf):
        raise RuntimeError(f"{doc.doc_id}: the PDF has a structure tree")
    if len(pdf) > core.MAX_PDF_BYTES:
        raise RuntimeError(f"{doc.doc_id}: PDF is {len(pdf)} bytes (limit {core.MAX_PDF_BYTES})")
    if built.post:
        built.post(doc, pdf)
    doc.finalize_headings()
    texts = core.pdf_pages_text(pdf, doc.family == "latex")
    anchors = []
    for rec in doc.items:
        lab = rec.get("_label")
        anchors.append(None if lab in labels else rec.get("_anchor"))
    pages = core.locate(texts, anchors)
    for i, rec in enumerate(doc.items):
        lab = rec.get("_label")
        if lab in labels:
            pages[i] = labels[lab]
        if rec["kind"] == "table" and pages[i] is None:
            pages[i] = _table_page(rec, texts)
    (folder / (doc.doc_id + ".pdf")).write_bytes(pdf)
    core.write_gold(folder / "gold.jsonl", doc.records(pages))
    missing = sum(
        1
        for p, r in zip(pages, doc.items, strict=True)
        if p is None and r["kind"] != "hyphen_break"
    )
    print(
        f"wrote {doc.doc_id}/{doc.doc_id}.pdf ({len(pdf)} bytes, {len(texts)} pages, "
        f"{len(doc.items)} gold records, {missing} without a page)"
    )
    return folder


def _table_page(rec: dict, texts: list[str]) -> int:
    cells = [
        re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rec["html"])
    ]
    for c in cells:
        key = core.squash(c)
        if len(key) < 3:
            continue
        for p, t in enumerate(texts):
            if key in core.squash(t):
                return p
    raise RuntimeError(f"table not found in the PDF: {rec['html'][:80]}")


# --------------------------------------------------------------------------- check

# Minimum items per document (sums over a document), from the brief.
TARGETS: list[tuple[str, str, int]] = [
    ("latex-exercises", "inline_fraction", 30),
    ("latex-exercises", "system_braced", 12),
    ("latex-exercises", "piecewise_words", 10),
    ("latex-exercises", "numbered_exercise_eq", 20),
    ("latex-matrices", "determinant", 12),
    ("latex-matrices", "matrix", 14),
    ("latex-matrices", "system_braced", 10),
    ("latex-matrices", "multirow_equations", 8),
    ("latex-chemistry", "chem_equation", 25),
    ("latex-chemistry", "chem_charge", 25),
    ("latex-chemistry", "chem_formula", 20),
    ("latex-chemistry", "nuclide", 22),
    ("latex-chemistry", "prose_unit_power", 40),
    ("latex-chemistry", "prose_chem_script", 40),
    ("latex-chemistry", "prose_nuclide", 20),
    ("latex-chemistry", "prose_script_other", 40),
    ("latex-slides", "display_large", 22),
    ("latex-structure", "heading_multiline", 12),
    ("latex-structure", "heading_sentence", 20),
    ("latex-structure", "heading_repeated_label", 16),
    ("latex-structure", "code_block", 22),
    ("latex-structure", "glossary_entry", 30),
    ("latex-structure", "table_data", 4),
    ("latex-structure", "table_layout", 4),
    ("latex-hyphenation", "hyphen_break", 60),
    ("word-lecture", "prose_unit_power", 40),
    ("word-lecture", "prose_chem_script", 40),
    ("word-lecture", "prose_nuclide", 20),
    ("word-lecture", "prose_script_other", 40),
    ("word-lecture", "numbered_exercise_eq", 25),
    ("word-lecture", "heading_sentence", 6),
    ("word-lecture", "heading_repeated_label", 6),
    ("word-lecture", "glossary_entry", 8),
    ("word-lecture", "table_data", 2),
    ("word-lecture", "table_layout", 1),
    ("word-eq-images", "equation_image", 30),
    ("word-eq-images", "table_data", 1),
    ("word-eq-images", "table_layout", 1),
    ("html-slides", "display_large", 14),
    ("html-slides", "inline_fraction", 3),
    ("html-code-glossary", "code_block", 10),
    ("html-code-glossary", "glossary_entry", 15),
    ("html-code-glossary", "table_data", 1),
    ("html-code-glossary", "table_layout", 1),
]
# Per document, a heading-level and hyphen-break floor (not classes).
REPEATED_LABELS = ["Example", "Solution", "Key Takeaways", "Check Your Understanding"]


def _read_gold(folder: Path) -> list[dict]:
    recs = []
    for n, line in enumerate((folder / "gold.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{folder.name}/gold.jsonl line {n}: {exc}") from exc
    return recs


def _digit_tokens(latex: str) -> list[str]:
    toks = re.findall(r"\d+", latex)
    toks += [
        w for t in re.findall(r"\\text\{([^}]*)\}", latex) for w in re.findall(r"[A-Za-z]{3,}", t)
    ]
    return list(dict.fromkeys(toks))


def _wraps(pdf: bytes, text: str) -> bool:
    """The heading's first line in the PDF is shorter than the whole heading."""
    import pymupdf

    key = core.squash(text)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for ln in block.get("lines", []):
                    sq = core.squash("".join(sp["text"] for sp in ln["spans"]))
                    if len(sq) >= 12 and key.startswith(sq) and sq != key:
                        return True
    return False


def check_folder(folder: Path, problems: list[str]) -> list[dict]:
    doc_id = folder.name
    recs = _read_gold(folder)
    pdf_path = folder / (doc_id + ".pdf")
    pdf = pdf_path.read_bytes()
    if len(pdf) >= core.MAX_PDF_BYTES:
        problems.append(f"{doc_id}: PDF is {len(pdf)} bytes")
    if core.has_struct_tree(pdf):
        problems.append(f"{doc_id}: PDF has a StructTreeRoot")
    tectonic = bool(recs) and recs[0]["toolchain"] == "tectonic"
    texts = core.pdf_pages_text(pdf, tectonic)
    full = core.squash("".join(texts))
    squashed_pages = [core.squash(t) for t in texts]

    def miss(kind: str, what: str) -> None:
        problems.append(f"{doc_id}: {kind} not in the text layer: {what[:90]!r}")

    for r in recs:
        for field in ("id", "kind", "variant", "pdf", "classes", "index_on_page", "toolchain"):
            if field not in r:
                problems.append(f"{doc_id}: record lacks {field}: {r.get('id')}")
        kind = r["kind"]
        page = r.get("page")
        if kind == "equation":
            try:
                ml = latex_to_mathml(r["latex"], r["display"])
            except Exception as exc:
                problems.append(f"{doc_id}: {r['id']} latex does not convert: {exc}")
                continue
            if ml != r["mathml"]:
                problems.append(f"{doc_id}: {r['id']} stored mathml differs from latex_to_mathml")
            if re.search(r"<m[ino]>\\", ml):
                problems.append(f"{doc_id}: {r['id']} has an unconverted command in MathML")
            if r["appearance"] == "image":
                continue
            toks = _digit_tokens(r["latex"])
            if toks:
                text = squashed_pages[page] if page is not None else full
                found = [t for t in toks if core.squash(t) in text]
                need = 1 if "display_large" in r["classes"] else math.ceil(0.6 * len(toks))
                if len(found) < need:
                    miss("equation tokens", f"{r['id']} {toks} on page {page}")
        elif kind == "heading":
            if core.squash(r["text"]) not in full:
                miss("heading", r["text"])
            elif page is not None and core.squash(r["text"]) not in squashed_pages[page]:
                problems.append(f"{doc_id}: heading {r['text'][:50]!r} is not on its page {page}")
            if "heading_multiline" in r["classes"] and tectonic and not _wraps(pdf, r["text"]):
                problems.append(
                    f"{doc_id}: heading does not wrap onto a second line: {r['text'][:60]!r}"
                )
        elif kind == "glossary":
            for part in (r["term"], r["definition"]):
                if core.squash(part) not in full:
                    miss("glossary", part)
        elif kind == "code":
            for ln in r["text"].splitlines():
                if ln.strip() and core.squash(ln) not in full:
                    miss("code line", ln)
            first = next((ln for ln in r["text"].splitlines() if ln.strip()), "")
            if page is not None and core.squash(first) not in squashed_pages[page]:
                problems.append(f"{doc_id}: code {first[:40]!r} is not on its page {page}")
        elif kind == "table":
            if page is None:
                problems.append(f"{doc_id}: {r['id']} table without a page")
            cells = [
                re.sub(r"<[^>]+>", "", c)
                for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r["html"])
            ]
            for c in cells:
                if c.strip() and core.squash(c) not in full:
                    miss("table cell", c)
        elif kind == "script":
            plain = re.sub(r"[\^_]\{([^}]*)\}", r"\1", r["text"])
            if core.squash(plain) not in full:
                miss("script paragraph", plain)
            elif page is not None and core.squash(plain[:30]) not in squashed_pages[page]:
                problems.append(
                    f"{doc_id}: script paragraph {plain[:40]!r} is not on its page {page}"
                )
        elif kind == "exercise_text":
            if core.squash(r["text"]) not in full:
                miss("exercise text", r["text"])
        elif kind == "hyphen_break":
            if page is None or core.squash(r["left"] + r["right"]) not in squashed_pages[page]:
                miss("hyphen break", f"{r['left']}|{r['right']}")
    return recs


def class_counts(recs: list[dict]) -> Counter:
    c: Counter = Counter()
    for r in recs:
        if r["kind"] == "script":
            for run in r["runs"]:
                c[run["class"]] += 1
        else:
            for cls in r["classes"]:
                c[cls] += 1
    return c


def disjointness(dev: list[dict], chk: list[dict]) -> dict[str, list[str]]:
    """Headings and equation latex strings shared by the dev and the check documents."""

    def keys(recs: list[dict], kind: str, field: str) -> set[str]:
        return {" ".join(r[field].split()) for r in recs if r["kind"] == kind}

    return {
        "headings": sorted(keys(dev, "heading", "text") & keys(chk, "heading", "text")),
        "equations": sorted(keys(dev, "equation", "latex") & keys(chk, "equation", "latex")),
    }


def print_table(title: str, docs: dict[str, list[dict]], class_ids: tuple[str, ...]) -> None:
    ids = list(docs)
    names = [
        SHORT.get(i.removesuffix("-chk"), i) + ("*" if i.endswith("-chk") else "") for i in ids
    ]
    widths = [max(len(n), 4) for n in names]
    print(f"\n{title}")
    print(
        f"{'class':24}"
        + "".join(f" {n:>{w}}" for n, w in zip(names, widths, strict=True))
        + "  total"
    )
    counts = {i: class_counts(r) for i, r in docs.items()}
    for cid in class_ids:
        row = [counts[i][cid] for i in ids]
        print(
            f"{cid:24}"
            + "".join(f" {v:>{w}}" for v, w in zip(row, widths, strict=True))
            + f" {sum(row):>6}"
        )
    kinds = [
        "equation",
        "heading",
        "script",
        "code",
        "glossary",
        "table",
        "hyphen_break",
        "exercise_text",
    ]
    print("records by kind")
    for k in kinds:
        row = [sum(1 for r in docs[i] if r["kind"] == k) for i in ids]
        print(
            f"{k:24}"
            + "".join(f" {v:>{w}}" for v, w in zip(row, widths, strict=True))
            + f" {sum(row):>6}"
        )


def check(root: Path) -> int:
    problems: list[str] = []
    docs: dict[str, list[dict]] = {}
    sizes = {}
    for folder in sorted(p for p in root.iterdir() if (p / "gold.jsonl").exists()):
        docs[folder.name] = check_folder(folder, problems)
        sizes[folder.name] = (folder / (folder.name + ".pdf")).stat().st_size
    order = [d for b in ORDER for d in (b, b + "-chk") if d in docs]
    dev = {d: docs[d] for d in order if not d.endswith("-chk")}
    chk = {d: docs[d] for d in order if d.endswith("-chk")}
    print_table("DEV documents", dev, core.CLASS_IDS)
    if chk:
        print_table("CHECK documents (* = check variant)", chk, core.CLASS_IDS)
    print("\nPDF sizes (bytes)")
    for d in order:
        print(f"  {d:28} {sizes[d]:>8}")
    # targets
    print("\ntargets")
    counts = {d: class_counts(r) for d, r in docs.items()}
    for base, cls, need in TARGETS:
        if base not in docs:
            continue
        got = counts[base][cls]
        flag = "ok  " if got >= need else "FAIL"
        if got < need:
            problems.append(f"target: {base} {cls} {got} < {need}")
        print(f"  {flag} {base:20} {cls:24} {got:>4} >= {need}")
    if "latex-structure" in docs:
        heads = Counter(r["text"] for r in docs["latex-structure"] if r["kind"] == "heading")
        for label in REPEATED_LABELS:
            flag = "ok  " if heads[label] >= 4 else "FAIL"
            if heads[label] < 4:
                problems.append(f"target: label {label} occurs {heads[label]} times")
            print(f"  {flag} latex-structure repeated label {label!r}: {heads[label]} (>= 4)")
    for b in CHECK_BASES:
        if b in docs and b + "-chk" in docs:
            sizes_ = (len(docs[b]), len(docs[b + "-chk"]))
            ratio = sizes_[1] / sizes_[0]
            if not 0.75 <= ratio <= 1.25:
                problems.append(f"{b}: check/dev record ratio {ratio:.2f}")
            print(f"  records {b}: dev {sizes_[0]}, check {sizes_[1]} (ratio {ratio:.2f})")
            for cls in core.CLASS_IDS:
                d_n, c_n = counts[b][cls], counts[b + "-chk"][cls]
                if cls != "hyphen_break" and d_n >= 10 and not 0.75 <= c_n / d_n <= 1.25:
                    problems.append(f"{b}: {cls} dev {d_n} vs check {c_n} (outside 25%)")
    # disjointness
    dev_recs = [r for d in dev for r in docs[d]]
    chk_recs = [r for d in chk for r in docs[d]]
    if chk_recs:
        shared = disjointness(dev_recs, chk_recs)
        print(
            f"\ndisjointness: shared headings {len(shared['headings'])}, "
            f"shared equation latex {len(shared['equations'])}"
        )
        for k, v in shared.items():
            for s in v:
                problems.append(f"dev and check share {k[:-1]}: {s[:80]}")
    print(f"\n{len(problems)} problem(s)")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, help="build into this folder")
    ap.add_argument("--check", type=Path, help="check an existing build folder")
    ap.add_argument("--tectonic", default="tectonic")
    ap.add_argument(
        "--only", nargs="*", help="document ids to build (a base id builds both variants)"
    )
    args = ap.parse_args(argv)
    if args.check:
        return check(args.check)
    if not args.out:
        ap.error("--out DIR or --check DIR is required")
    args.out.mkdir(parents=True, exist_ok=True)
    core.CONFIG["tectonic"] = args.tectonic
    if core.find_hyph_dir() is None:
        print("note: hyph_en_US.dic not found; Word documents get no hyphenation (see --help)")
    registry = builders()
    wanted = args.only or ORDER
    for base in ORDER:
        if base not in registry:
            continue
        for variant in ("dev", "check"):
            doc_id = base if variant == "dev" else base + "-chk"
            if variant == "check" and base not in CHECK_BASES:
                continue
            if args.only and doc_id not in wanted and base not in wanted:
                continue
            build_one(base, variant, registry[base], args.out, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
