"""Own handwritten notes as a handwriting test set (docs/eval-handwriting.md, "Own handwritten
notes"): intake, a blind split, a typed-by-hand answer key, and a run capped in dollars.

    uv run python scripts/handwriting_own.py intake [--src DIR]  # register, split dev/held-out
    uv run python scripts/handwriting_own.py form [--part dev|heldout]   # open the answer-key form
    uv run python scripts/handwriting_own.py status                      # counts only
    uv run --env-file .env python scripts/handwriting_own.py run --part dev --max-usd 1
    uv run python scripts/handwriting_own.py run --part dev --offline    # replay, no spend

Everything private lives in one folder OUTSIDE the repository, by default
`~/readablestem-private/handwriting/` (override with `HANDWRITING_OWN_DIR`):

    pdfs/            the exported notes (drop them here, or pass --src)
    pages/           page pictures the form shows
    registration.json, registration.sha256   the split, written once, before any model sees a page
    gold/<unit>.json the answer key, typed by hand in the form
    runs/<part>/     recorded model answers, reports, spend log

The notes are never copied into the repository. Every command refuses a folder inside the
repository, or inside any git working tree. The model is never shown anything but the PDFs; the
answer key is typed by hand from a blank form with no model draft. `run` is the only command
that can spend money, and it sends the PDFs to the model provider (Anthropic). It needs
`--max-usd` (at most 2.00, a total across restarts), the part's answer key finished and the
registration unchanged, and it will not record a part twice.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pymupdf

REPO = Path(__file__).resolve().parent.parent
FORM = Path(__file__).resolve().parent / "handwriting_gold_form.html"
DEFAULT_ROOT = Path.home() / "readablestem-private" / "handwriting"
SEED = "handwriting-own-2026-11"
DEV_SHARE = 0.3
MIN_PDFS = 2
MAX_CAP_USD = 2.0
PAGE_DPI = 150
MAX_BODY = 1_000_000
MAX_LATEX = 2000
PARTS = ("dev", "heldout")
UNIT = re.compile(r"own-[0-9a-f]{8}")


class OwnError(SystemExit):
    """A refusal with a plain message (printed, exit status 1)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# ------------------------------------------------------------------------ where things live


def inside_repo(path: Path) -> bool:
    """True if `path` is in this repository, in its main checkout, or in any git working tree."""
    resolved = path.resolve()
    if resolved == REPO or REPO in resolved.parents:
        return True
    home = Path.home().resolve()
    return any((p / ".git").exists() for p in (resolved, *resolved.parents) if p != home)


def ensure_outside_repo(path: Path, what: str = "folder") -> Path:
    resolved = path.expanduser().resolve()
    if inside_repo(resolved):
        raise OwnError(
            f"refusing the {what} {resolved}: it is inside a git repository. "
            "Notes never go in the repo; use a folder like ~/readablestem-private/handwriting/."
        )
    return resolved


def private_root() -> Path:
    return ensure_outside_repo(Path(os.environ.get("HANDWRITING_OWN_DIR") or DEFAULT_ROOT))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=1, sort_keys=True) + "\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------- intake + split


def split_units(file_hashes: list[str]) -> dict[str, str]:
    """hash -> part. By hash alone, so no one picks which pages are sealed: rank every file by
    sha256(seed + file hash); the first ~30% (at least one) are dev, the rest held-out."""
    ranked = sorted(file_hashes, key=lambda h: hashlib.sha256((SEED + h).encode()).hexdigest())
    n_dev = max(1, round(DEV_SHARE * len(ranked)))
    return {h: ("dev" if i < n_dev else "heldout") for i, h in enumerate(ranked)}


def unit_id(file_hash: str) -> str:
    return f"own-{file_hash[:8]}"


def render_pages(pdf: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf) as doc:
        for number, page in enumerate(doc, start=1):
            page.get_pixmap(dpi=PAGE_DPI, alpha=False).save(out_dir / f"p{number:02d}.png")
        return doc.page_count


def intake(root: Path, src: Path | None = None) -> dict:
    """Copy the PDFs into the private folder, render the pages, register the split (once)."""
    pdf_dir = root / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    if src is not None:
        src = ensure_outside_repo(src, "source folder")
        if src != pdf_dir.resolve():
            for pdf in sorted(src.glob("*.pdf")):
                shutil.copy2(pdf, pdf_dir / pdf.name)
    files = sorted(pdf_dir.glob("*.pdf"))
    if len(files) < MIN_PDFS:
        raise OwnError(f"need at least {MIN_PDFS} PDFs in {pdf_dir} (found {len(files)})")
    hashes = {f: sha256_file(f) for f in files}
    if len(set(hashes.values())) != len(files):
        raise OwnError("two of the PDFs are identical files; remove the copy")
    registration = root / "registration.json"
    parts = split_units(list(hashes.values()))
    entries = []
    for file in files:
        digest = hashes[file]
        with pymupdf.open(file) as doc:
            if doc.page_count < 1:
                raise OwnError(f"{file.name} has no pages")
        entries.append(
            {
                "unit": unit_id(digest),
                "file": file.name,
                "sha256": digest,
                "part": parts[digest],
            }
        )
    entries.sort(key=lambda e: e["unit"])
    if registration.is_file():
        old = json.loads(registration.read_text(encoding="utf-8"))
        if {(e["unit"], e["sha256"], e["part"]) for e in old["units"]} != {
            (e["unit"], e["sha256"], e["part"]) for e in entries
        }:
            raise OwnError(
                f"{registration} is already written and the PDFs differ from it. The split is "
                "fixed before any model run; to start over, delete the registration on purpose."
            )
        data = old
    else:
        data = {
            "registered": dt.date.today().isoformat(),
            "seed": SEED,
            "dev_share": DEV_SHARE,
            "units": entries,
        }
        write_json(registration, data)
        (root / "registration.sha256").write_text(
            f"{sha256_file(registration)}  registration.json\n", encoding="utf-8"
        )
    for entry in data["units"]:
        entry["pages"] = render_pages(pdf_dir / entry["file"], root / "pages" / entry["unit"])
    return data


def load_registration(root: Path) -> dict:
    """The registration, checked: its own hash, and every PDF still the registered bytes."""
    registration = root / "registration.json"
    sidecar = root / "registration.sha256"
    if not registration.is_file() or not sidecar.is_file():
        raise OwnError(f"not registered: run `handwriting_own.py intake` ({registration})")
    if sidecar.read_text().split()[0] != sha256_file(registration):
        raise OwnError("registration.json has changed since it was registered")
    data = json.loads(registration.read_text(encoding="utf-8"))
    for entry in data["units"]:
        pdf = root / "pdfs" / entry["file"]
        if not pdf.is_file() or sha256_file(pdf) != entry["sha256"]:
            raise OwnError(f"{entry['file']} has changed or is missing since registration")
    return data


def page_count(root: Path, entry: dict) -> int:
    with pymupdf.open(root / "pdfs" / entry["file"]) as doc:
        return doc.page_count


# ------------------------------------------------------------------------------ the answer key


def clean_gold(body: object, entry: dict, pages: int) -> dict:
    """Validate what the form posts; raise ValueError with a plain reason."""
    if not isinstance(body, dict) or not isinstance(body.get("expressions"), list):
        raise ValueError("expected an object with a list of expressions")
    out = []
    for raw in body["expressions"]:
        if not isinstance(raw, dict):
            raise ValueError("an expression is not an object")
        page, box, latex = raw.get("page"), raw.get("box"), raw.get("latex", "")
        display = raw.get("display", "inline")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= pages:
            raise ValueError("page out of range")
        if (
            not isinstance(box, list)
            or len(box) != 4
            or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in box)
            or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)
        ):
            raise ValueError("box must be [x0, y0, x1, y1] as fractions of the page")
        if not isinstance(latex, str) or len(latex) > MAX_LATEX:
            raise ValueError("latex must be a string of at most 2000 characters")
        if display not in ("inline", "display"):
            raise ValueError("display must be inline or display")
        out.append(
            {
                "page": page,
                "box": [round(float(v), 5) for v in box],
                "latex": latex.strip(),
                "display": display,
                "uncertain": bool(raw.get("uncertain", False)),
            }
        )
    return {
        "unit": entry["unit"],
        "pdf_sha256": entry["sha256"],
        "part": entry["part"],
        "complete": bool(body.get("complete", False)),
        "expressions": out,
    }


def load_gold(root: Path, entry: dict) -> dict | None:
    path = root / "gold" / f"{entry['unit']}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def mathml_for(latex: str, display: str) -> str:
    from readablestem.gold.arxiv_gold import to_mathml

    return to_mathml(latex, display == "display")


def gold_records(root: Path, entry: dict, gold: dict) -> list[dict]:
    """The scoring records of a finished unit, in the document task's format. Expressions marked
    uncertain ("?") are left out of the primary; blank ones are skipped. Reading order: page,
    then top to bottom, then left to right."""
    from readablestem.gold.arxiv_gold import GoldError, token_count

    items = [e for e in gold["expressions"] if e["latex"] and not e["uncertain"]]
    items.sort(key=lambda e: (e["page"], round(e["box"][1], 2), e["box"][0]))
    records = []
    for index, item in enumerate(items):
        try:
            ml = mathml_for(item["latex"], item["display"])
        except GoldError as exc:
            raise OwnError(
                f"{entry['unit']} page {item['page']}: the converter cannot read {item['latex']!r}"
                " (fix it in the form)"
            ) from exc
        records.append(
            {
                "book_slug": "handwriting-own",
                "book_title": "Own handwritten notes",
                "page_slug": entry["unit"],
                "page_title": entry["unit"],
                "id": f"handwriting-own/{entry['unit']}/eq-{index:03d}",
                "kind": "equation",
                "display": "block" if item["display"] == "display" else "inline",
                "latex": item["latex"],
                "mathml": ml,
                "index_on_page": index,
                "tokens": token_count(ml),
                "classes": [],
                "in_float": False,
                "pdf": f"pdfs/{entry['unit']}.pdf",
                "pdf_page": item["page"],
                "pdf_sha256": entry["sha256"],
                "source": "own-notes",
                "part": entry["part"],
            }
        )
    return records


def build_gold_dir(root: Path, registration: dict, part: str) -> tuple[Path, dict]:
    """`runs/<part>/gold/` with the part's PDFs and jsonl, from finished answer keys; counts."""
    entries = [e for e in registration["units"] if e["part"] == part]
    unfinished = []
    golds = {}
    for entry in entries:
        gold = load_gold(root, entry)
        if not gold or not gold["complete"] or gold["pdf_sha256"] != entry["sha256"]:
            unfinished.append(entry["unit"])
        else:
            golds[entry["unit"]] = gold
    if unfinished:
        raise OwnError(
            f"the {part} answer key is not finished for {len(unfinished)} of {len(entries)} "
            "PDFs; open the form and tick 'finished' for each"
        )
    out = root / "runs" / part / "gold"
    (out / "pdfs").mkdir(parents=True, exist_ok=True)
    counts = {"units": len(entries), "expressions": 0, "left_out_uncertain": 0}
    for entry in entries:
        shutil.copy2(root / "pdfs" / entry["file"], out / "pdfs" / f"{entry['unit']}.pdf")
        records = gold_records(root, entry, golds[entry["unit"]])
        lines = [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records]
        (out / f"{entry['unit']}.jsonl").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
        counts["expressions"] += len(records)
        counts["left_out_uncertain"] += sum(
            e["uncertain"] for e in golds[entry["unit"]]["expressions"]
        )
    return out, counts


# --------------------------------------------------------------------------- the form server


def make_server(root: Path, registration: dict, part: str | None, port: int = 0):
    """A server on 127.0.0.1 for the form: pages out, answer keys in, MathML from the converter."""
    entries = {e["unit"]: e for e in registration["units"] if part in (None, e["part"])}
    host_ok: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        server_version = "own-notes-form"

        def log_message(self, *args: object) -> None:  # no paths or content in any log
            pass

        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; img-src 'self' data:",
            )
            self.end_headers()
            self.wfile.write(body)

        def reply(self, data: object, status: int = 200) -> None:
            self.send(status, json.dumps(data).encode(), "application/json")

        def allowed(self) -> bool:
            if self.headers.get("Host", "") not in host_ok:  # blocks DNS rebinding
                self.reply({"error": "bad host"}, HTTPStatus.FORBIDDEN)
                return False
            return True

        def do_GET(self) -> None:
            if not self.allowed():
                return
            url = urlparse(self.path)
            if url.path == "/":
                self.send(200, FORM.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                units = []
                for entry in entries.values():
                    units.append(
                        {
                            "unit": entry["unit"],
                            "part": entry["part"],
                            "name": entry["file"],
                            "pages": page_count(root, entry),
                            "gold": load_gold(root, entry),
                        }
                    )
                self.reply({"units": units})
            elif url.path == "/api/mathml":
                query = parse_qs(url.query)
                latex = (query.get("latex") or [""])[0][:MAX_LATEX]
                display = (query.get("display") or ["inline"])[0]
                try:
                    self.reply({"mathml": mathml_for(latex, display) if latex.strip() else ""})
                except Exception:  # the converter's own errors; no content in the reply
                    self.reply({"error": "the converter cannot read this yet"})
            else:
                match = re.fullmatch(r"/page/(own-[0-9a-f]{8})/(\d{1,3})\.png", unquote(url.path))
                if not match or match[1] not in entries:
                    self.reply({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                image = root / "pages" / match[1] / f"p{int(match[2]):02d}.png"
                if not image.is_file():
                    self.reply({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                self.send(200, image.read_bytes(), "image/png")

        def do_POST(self) -> None:
            if not self.allowed():
                return
            match = re.fullmatch(r"/api/gold/(own-[0-9a-f]{8})", self.path)
            if not match or match[1] not in entries:
                self.reply({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            if self.headers.get_content_type() != "application/json":
                self.reply({"error": "send JSON"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self.reply({"error": "bad Content-Length"}, HTTPStatus.BAD_REQUEST)
                return
            if not 0 < length <= MAX_BODY:
                self.reply({"error": "bad size"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            entry = entries[match[1]]
            try:
                body = json.loads(self.rfile.read(length))
                gold = clean_gold(body, entry, page_count(root, entry))
            except ValueError as exc:
                self.reply({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            write_json(root / "gold" / f"{entry['unit']}.json", gold)
            self.reply({"saved": len(gold["expressions"])})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    bound = server.server_address[1]
    host_ok.update({f"127.0.0.1:{bound}", f"localhost:{bound}"})
    return server


def cmd_form(args: argparse.Namespace) -> int:
    root = private_root()
    registration = load_registration(root)
    server = make_server(root, registration, args.part, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"the answer-key form is at {url}  (Ctrl+C to stop; every change is saved as you type)")
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


# ------------------------------------------------------------------------------- the run


def answers_cost(path: Path) -> float:
    if not path.is_file():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += float((json.loads(line).get("call") or {}).get("cost_usd") or 0.0)
    return total


# The registered settings of the handwriting evaluation: Opus reads the pages, no second check.
PIPELINE_ENV = {
    "CLAUDE_SECOND_CHECK": "off",
    "FEATURE_IMAGE_PAGES": "true",
    "IMAGE_PAGES_MODEL": "claude-opus-5-5",
}
PIPELINE_UNSET = ("IMAGE_PAGES_DPI", "ANTHROPIC_EFFORT_IMAGE_PAGES")


@contextlib.contextmanager
def scoped_env(values: dict[str, str], unset: tuple[str, ...] = ()):
    """Set and unset environment variables for the block only (settings are read from the
    environment when the pipeline starts), then restore them: nothing leaks to the caller."""
    saved = {k: os.environ.get(k) for k in (*values, *unset)}
    os.environ.update(values)
    for key in unset:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def total_spent(runs: Path) -> float:
    """Dollars already spent on these notes, from the recorded answers of both parts."""
    return sum(answers_cost(runs / part / "answers.jsonl") for part in PARTS)


def cmd_run(args: argparse.Namespace) -> int:
    if args.max_usd is None or not 0 < args.max_usd <= MAX_CAP_USD:
        raise OwnError(f"--max-usd is required and must be above 0 and at most {MAX_CAP_USD:.2f}")
    root = private_root()
    registration = load_registration(root)
    runs = root / "runs"
    marker = runs / f"{args.part}.recorded"
    if not args.offline:
        if marker.is_file():
            raise OwnError(f"{args.part} was already recorded: replay with --offline")
        if args.part == "heldout" and not (runs / "dev.recorded").is_file():
            raise OwnError("run dev first: the held-out notes are read once, after the dev part")
    gold_dir, counts = build_gold_dir(root, registration, args.part)
    if counts["expressions"] == 0:
        raise OwnError(f"the {args.part} answer key has no expressions")

    spent_before = total_spent(runs)
    remaining = round(args.max_usd - spent_before, 4)
    if not args.offline and remaining <= 0:
        raise OwnError(
            f"--max-usd {args.max_usd:.2f} is a total across restarts and ${spent_before:.2f} "
            "is already spent on these notes"
        )
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser
    from readablestem.eval.realworld_media import RealWorldMedia

    out_dir = runs / args.part
    answers, report, items = (
        out_dir / "answers.jsonl",
        out_dir / "report.json",
        out_dir / "items.jsonl",
    )
    argv = [
        "eval", "pipeline", "--task", "document", "--provider", "anthropic",
        "--answers", str(answers), "--items", str(items), "--out", str(report),
        "--section-workers", "1", "--cache", str(out_dir / "cache"),
        "--max-usd", str(args.max_usd if args.offline else remaining),
    ]  # fmt: skip
    if args.offline:
        argv.append("--answers-offline")
    ns = build_parser().parse_args(argv)
    before = answers_cost(answers)
    with scoped_env(PIPELINE_ENV, PIPELINE_UNSET):
        code = eval_cli._run_pipeline(
            ns,
            gold_dir,
            report,
            gold_label=f"own handwriting ({args.part})",
            media_cls=RealWorldMedia,
            cache_dir=out_dir / "cache",
            openstax_cache=out_dir / "cache",
        )
    spent = answers_cost(answers) - before
    if code == 0 and not args.offline:
        entry = {
            "when": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "part": args.part,
            "cap_usd": args.max_usd,
            "actual_usd": round(spent, 4),
            **counts,
        }
        with (runs / "spend-log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        marker.write_text(dt.date.today().isoformat())
    print(f"spent this start: ${spent:.4f}")
    return code


def cmd_status(_: argparse.Namespace) -> int:
    root = private_root()
    registration = load_registration(root)
    for part in PARTS:
        entries = [e for e in registration["units"] if e["part"] == part]
        golds = [load_gold(root, e) for e in entries]
        typed = sum(len(g["expressions"]) for g in golds if g)
        finished = sum(bool(g and g["complete"]) for g in golds)
        print(
            f"{part}: {len(entries)} PDFs, {finished} finished, {typed} expressions typed, "
            f"recorded: {(root / 'runs' / f'{part}.recorded').is_file()}"
        )
    return 0


def cmd_intake(args: argparse.Namespace) -> int:
    root = private_root()
    data = intake(root, args.src)
    for part in PARTS:
        entries = [e for e in data["units"] if e["part"] == part]
        pages = sum(e["pages"] for e in entries)
        print(f"{part}: {len(entries)} PDFs, {pages} pages")
    print("registered; next: `handwriting_own.py form`")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    intake_p = sub.add_parser("intake")
    intake_p.add_argument("--src", type=Path, help="folder with the PDFs (outside the repo)")
    form = sub.add_parser("form")
    form.add_argument("--part", choices=PARTS)
    form.add_argument("--port", type=int, default=8765)
    form.add_argument("--no-open", action="store_true")
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--part", choices=PARTS, required=True)
    run.add_argument("--max-usd", type=float, required=False)
    run.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    return {"intake": cmd_intake, "form": cmd_form, "status": cmd_status, "run": cmd_run}[
        args.command
    ](args)


if __name__ == "__main__":
    sys.exit(main())
