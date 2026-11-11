"""The test corpus: what each file is, where it comes from, and its reading-order labels.

`tests/workflows/corpus/corpus.json` lists every file. An entry is one of three kinds:

- `public`: a file on a public site. The manifest pins its URL, SHA-256 and size; `fetch`
  downloads it into `.cache/corpus/` and refuses bytes that do not match. The tests use a
  page range cut from it. Nothing of the file is committed.
- `committed`: a file in the corpus folder, either made for this repository (with the
  source it was made from) or an attributed cut of an openly licensed work whose
  publisher's URL does not serve stable bytes.
- `builder`: a file a builder under `tests/workflows/builders/` makes on demand.

Reading-order labels ("gold") sit in `gold/`, one file per entry. For committed and builder
entries the labels hold the text of each block. For public entries they hold no text: each
block is a list of character ranges into the page's own words, so the text exists only once
the file has been fetched, and a hash per block proves it was rebuilt exactly.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "tests" / "workflows" / "corpus"
MANIFEST = CORPUS / "corpus.json"
CACHE = REPO / ".cache" / "corpus"

# Sites a public entry may be fetched from, and sites an entry may cite without fetching.
FETCH_HOSTS = frozenset({"ocw.mit.edu", "arxiv.org", "upload.wikimedia.org"})
CITE_HOSTS = FETCH_HOSTS | {"openstax.org", "commons.wikimedia.org", "creativecommons.org"}

OWN_LICENSE = "MIT (made for this repository)"
# Licenses under which someone else's file may be committed, always with attribution.
COMMIT_LICENSES = frozenset({"CC BY 4.0", "CC0 1.0", "Public domain"})
OWN_LIMIT = 200_000
CITED_LIMIT = 5_000_000

KINDS = ("public", "committed", "builder")
RANGES = "page-ranges"
TEXT = "text"
MIN_RUN = 4
# Block fields that carry no words of the page, so they are stored as they are.
TEXT_FREE = frozenset({"role", "coarse", "float", "entry", "unit"})
USER_AGENT = "Mozilla/5.0 (corpus-fetch)"
BUSY_WAITS = (5, 20, 60)  # seconds to wait after each "too many requests"

_ID = re.compile(r"[a-z0-9][a-z0-9_]*")
_SHA = re.compile(r"[0-9a-f]{64}")
_URL = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_LOCAL = re.compile(
    r"^(?:/|~|\.\.|[A-Za-z]:[\\/]|file:)|(?:^|[\s\"'=(])(?:/home/|/Users/|/tmp/|/var/|/mnt/|~/)"
)


class CorpusError(Exception):
    """The manifest, a fetched file or a gold file is not what the corpus says it is."""


Fetcher = Callable[[str, int], bytes]


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str
    title: str
    document: str
    features: str
    license: str
    attribution: str = ""
    sha256: str = ""
    bytes: int = 0
    url: str = ""
    pages: tuple[int, int] | None = None
    path: str = ""
    made: dict | None = None
    builder: str = ""
    gold: str = ""
    labels: dict | None = None

    @property
    def own(self) -> bool:
        """Made for this repository, so no one else's license applies."""
        return self.license == OWN_LICENSE


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(data: bytes, expected: str, what: str) -> bytes:
    got = sha256(data)
    if got != expected:
        raise CorpusError(
            f"{what}: SHA-256 is {got} ({len(data)} bytes), the manifest says {expected}"
        )
    return data


# --- Manifest -------------------------------------------------------------------------


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _inside(root: Path, relative: str) -> Path | None:
    """`relative` resolved under `root`, or None when it is absolute or climbs out."""
    if not relative or Path(relative).is_absolute():
        return None
    target = (root / relative).resolve()
    return target if target.is_relative_to(root.resolve()) else None


def _url_problem(url: str, hosts: frozenset[str]) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        return "is not https"
    if parts.username or parts.password or parts.port:
        return "carries a login or a port"
    if parts.hostname not in hosts:
        return f"is on {parts.hostname}, which is not an allowed site"
    return ""


def problems_in_text(value: Any) -> list[str]:
    """Strings that name a path on one machine or cite a site that is not allowed."""
    out = []
    for text in _strings(value):
        if _LOCAL.search(text):
            out.append(f"{text!r} is a path on one machine")
        for url in _URL.findall(text):
            if why := _url_problem(url.rstrip(".,;)"), CITE_HOSTS):
                out.append(f"{url} {why}")
    return out


def problems(raw: dict, root: Path = CORPUS, labels: dict | None = None) -> list[str]:
    """Everything wrong with one manifest entry, in plain words. Empty when it is sound.

    `labels` is the manifest's table of labeling methods, which an entry with gold names.
    """
    out: list[str] = []
    kind = raw.get("kind")
    if not isinstance(raw.get("id"), str) or not _ID.fullmatch(raw["id"]):
        out.append("id must be lower-case letters, digits and underscores")
    if kind not in KINDS:
        return [*out, f"kind must be one of {', '.join(KINDS)}"]
    for field in ("title", "document", "license"):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            out.append(f"{field} is missing")
    out.extend(problems_in_text({k: v for k, v in raw.items() if k != "url"}))

    if kind in ("public", "committed"):
        if not isinstance(raw.get("sha256"), str) or not _SHA.fullmatch(raw["sha256"]):
            out.append("sha256 must be 64 lower-case hex digits")
        if not isinstance(raw.get("bytes"), int) or raw["bytes"] <= 0:
            out.append("bytes must be the file's size")

    if kind == "public":
        url = raw.get("url")
        if not isinstance(url, str):
            out.append("url is missing")
        elif why := _url_problem(url, FETCH_HOSTS):
            out.append(f"url {why}")
        pages = raw.get("pages")
        if not (
            isinstance(pages, list)
            and len(pages) == 2
            and all(isinstance(n, int) for n in pages)
            and 1 <= pages[0] <= pages[1]
        ):
            out.append("pages must be [first, last], counted from 1")
        if not raw.get("attribution"):
            out.append("attribution is missing")
        if raw.get("path") or raw.get("builder"):
            out.append("a public entry is fetched; it has no path or builder")

    if kind == "committed":
        target = _inside(root, raw.get("path", "")) if isinstance(raw.get("path"), str) else None
        if target is None:
            out.append("path must be a file inside the corpus folder")
        elif not target.is_file():
            out.append(f"{raw['path']} is not in the corpus folder")
        made = raw.get("made")
        if not isinstance(made, dict) or not made.get("how"):
            out.append("made.how must say how the file was produced")
        else:
            for source in made.get("from", []):
                found = _inside(root, source) if isinstance(source, str) else None
                if found is None or not found.is_file():
                    out.append(f"made.from lists {source!r}, which is not in the corpus folder")
        size = raw["bytes"] if isinstance(raw.get("bytes"), int) else 0
        if raw.get("license") == OWN_LICENSE:
            if size > OWN_LIMIT:
                out.append(f"a file made here is at most {OWN_LIMIT} bytes; this is {size}")
        else:
            if raw.get("license") not in COMMIT_LICENSES:
                allowed = ", ".join(sorted(COMMIT_LICENSES))
                out.append(f"only {allowed} files may be committed, not {raw.get('license')!r}")
            if not raw.get("attribution"):
                out.append("attribution is missing")
            if size > CITED_LIMIT:
                out.append(f"a committed public file is at most {CITED_LIMIT} bytes")

    if kind == "builder":
        if not isinstance(raw.get("builder"), str) or not raw["builder"]:
            out.append("builder must name a fixture builder")
        if raw.get("license") != OWN_LICENSE:
            out.append(f"a built file's license is {OWN_LICENSE!r}")

    if "gold" in raw:
        gold = _inside(root, raw["gold"]) if isinstance(raw["gold"], str) else None
        if gold is None or not gold.is_file():
            out.append(f"gold file {raw['gold']!r} is not in the corpus folder")
        how = (labels or {}).get(raw.get("labels"))
        if not isinstance(how, dict) or not how.get("method") or not how.get("date"):
            out.append("labels must name a labeling method that says how and when")
    return out


def load_manifest(path: Path = MANIFEST) -> dict[str, Entry]:
    """Every corpus entry by id. Raises CorpusError listing every unsound entry."""
    root = path.parent
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: dict[str, Entry] = {}
    wrong: list[str] = []
    methods = data.get("labels", {})
    for name, how in methods.items():
        wrong.extend(f"labels.{name}: {line}" for line in problems_in_text(how))
    for raw in data["entries"]:
        name = raw.get("id", "?")
        found = problems(raw, root, methods)
        if name in entries:
            found.append("id is used twice")
        if found:
            wrong.extend(f"{name}: {line}" for line in found)
            continue
        fields = dict(raw)
        if "pages" in fields:
            fields["pages"] = tuple(fields["pages"])
        if "labels" in fields:
            fields["labels"] = methods[fields["labels"]]
        entries[name] = Entry(**fields)
    if wrong:
        raise CorpusError("corpus.json:\n  " + "\n  ".join(wrong))
    return entries


# --- Files ----------------------------------------------------------------------------


class _SameSites(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if why := _url_problem(newurl, FETCH_HOSTS):
            raise CorpusError(f"{req.full_url} redirects to {newurl}, which {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, size: int) -> bytes:
    """The bytes at `url`, reading at most one byte past `size`."""
    opener = urllib.request.build_opener(_SameSites)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for wait in (*BUSY_WAITS, None):
        try:
            with opener.open(request, timeout=120) as response:
                return response.read(size + 1)
        except urllib.error.HTTPError as exc:
            # A site that says "too many requests" is asked again after a pause.
            if exc.code != 429 or wait is None:
                raise CorpusError(f"could not fetch {url}: {exc}") from exc
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CorpusError(f"could not fetch {url}: {exc}") from exc
    raise AssertionError("unreachable")


def fetch(entry: Entry, *, cache: Path = CACHE, get: Fetcher = download) -> bytes:
    """A public entry's whole file, from the cache or the network, checked against its hash.

    A cached copy that no longer matches is fetched again. Bytes that do not match are
    never written to the cache.
    """
    if entry.kind != "public":
        raise CorpusError(f"{entry.id} is not fetched; it is {entry.kind}")
    cached = cache / f"{entry.sha256}.pdf"
    if cached.is_file():
        data = cached.read_bytes()
        if sha256(data) == entry.sha256:
            return data
    data = verify(get(entry.url, entry.bytes), entry.sha256, f"{entry.id} from {entry.url}")
    cache.mkdir(parents=True, exist_ok=True)
    handle, partial = tempfile.mkstemp(dir=cache, suffix=".part")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(partial, cached)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise
    return data


def cut(data: bytes, first: int, last: int) -> bytes:
    """Pages `first` to `last` (counted from 1) of a PDF, the same bytes on every run."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if last > doc.page_count:
            raise CorpusError(f"pages {first}-{last} asked of a {doc.page_count}-page file")
        doc.select(list(range(first - 1, last)))
        return doc.tobytes(garbage=3, deflate=True, no_new_id=True)


def _builders():
    tests = str(REPO / "tests" / "workflows")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    return importlib.import_module("builders")


def document(entry: Entry, *, cache: Path = CACHE, get: Fetcher = download) -> bytes:
    """The file the tests use for `entry`: the cut pages, the committed file or the build."""
    if entry.kind == "public":
        assert entry.pages is not None
        return cut(fetch(entry, cache=cache, get=get), *entry.pages)
    if entry.kind == "committed":
        return verify((CORPUS / entry.path).read_bytes(), entry.sha256, entry.path)
    return _builders().build(entry.builder)


# --- Gold -----------------------------------------------------------------------------


def clean(text: str) -> str:
    """Gold text as stored: single spaces, none at the ends."""
    return " ".join(text.split())


def page_stream(page: pymupdf.Page) -> str:
    """The page's words in file order, NFKC-normalized, with nothing between them."""
    return "".join(unicodedata.normalize("NFKC", w[4]) for w in page.get_text("words", sort=False))


def _longest(flat: str, start: int, stream: str) -> int:
    """Length of the longest piece of `flat` from `start` that occurs in `stream`."""
    low, high = 0, len(flat) - start
    while low < high:
        middle = (low + high + 1) // 2
        if flat[start : start + middle] in stream:
            low = middle
        else:
            high = middle - 1
    return low


def encode_block(text: str, stream: str, near: int = 0) -> tuple[dict, int]:
    """One block's text as ranges of `stream` (literal characters where the page has none).

    `near` is where the block before ended, so a phrase that occurs twice on the page
    resolves to the occurrence that follows. Returns the stored block and the new `near`.
    """
    tokens = text.split(" ")
    flat = "".join(tokens)
    runs: list[list[int] | str] = []
    at = 0
    while at < len(flat):
        length = _longest(flat, at, stream)
        if length >= min(MIN_RUN, len(flat) - at):
            piece = flat[at : at + length]
            found = stream.find(piece, near)
            if found < 0:
                found = stream.find(piece)
            runs.append([found, found + length])
            near = found + length
            at += length
        else:
            if runs and isinstance(runs[-1], str):
                runs[-1] += flat[at]
            else:
                runs.append(flat[at])
            at += 1
    block = {
        "runs": runs,
        "tokens": [len(token) for token in tokens],
        "sha256": sha256(text.encode("utf-8")),
    }
    return block, near


def decode_block(block: dict, stream: str) -> str:
    pieces = (run if isinstance(run, str) else stream[run[0] : run[1]] for run in block["runs"])
    flat = "".join(pieces)
    words, at = [], 0
    for length in block["tokens"]:
        words.append(flat[at : at + length])
        at += length
    text = " ".join(words)
    if at != len(flat) or sha256(text.encode("utf-8")) != block["sha256"]:
        raise CorpusError("a gold block does not rebuild to the text that was labeled")
    return text


def encode_gold(gold: dict, pdf: bytes) -> dict:
    """Text gold for `pdf` rewritten as page ranges, carrying no text of its own."""
    pages = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page in gold["pages"]:
            if unknown := set(page) - {"page", "blocks"}:
                raise CorpusError(f"page field {sorted(unknown)} may hold text; encode it first")
            stream = page_stream(doc[page["page"] - 1])
            near, blocks = 0, []
            for block in page["blocks"]:
                if unknown := set(block) - {"text", "start"} - TEXT_FREE:
                    raise CorpusError(f"block field {sorted(unknown)} may hold text")
                stored, near = encode_block(clean(block["text"]), stream, near)
                extra = {k: v for k, v in block.items() if k in TEXT_FREE}
                blocks.append({**extra, **stored})
            pages.append(
                {
                    "page": page["page"],
                    "stream_sha256": sha256(stream.encode("utf-8")),
                    "blocks": blocks,
                }
            )
    return {"id": gold["id"], "encoding": RANGES, "pages": pages}


def decode_gold(gold: dict, pdf: bytes) -> dict:
    pages = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page in gold["pages"]:
            stream = page_stream(doc[page["page"] - 1])
            if sha256(stream.encode("utf-8")) != page["stream_sha256"]:
                raise CorpusError(
                    f"{gold['id']} page {page['page']}: the words read from the file are not "
                    "the words the gold was encoded against (a different file, or a PyMuPDF "
                    "that reads the page differently)"
                )
            blocks = []
            for block in page["blocks"]:
                extra = {k: v for k, v in block.items() if k not in ("runs", "tokens", "sha256")}
                blocks.append({**extra, "text": decode_block(block, stream)})
            pages.append({"page": page["page"], "blocks": blocks})
    return {"id": gold["id"], "pages": pages}


def load_gold(entry: Entry, pdf: bytes | None = None) -> dict:
    """An entry's reading-order labels as `{"id", "pages": [{"page", "blocks": [...]}]}`.

    Each block has `role` and `text`. Pass the entry's document when it is already in hand;
    otherwise a public entry's file is fetched to rebuild the text.
    """
    if not entry.gold:
        raise CorpusError(f"{entry.id} has no gold")
    gold = json.loads((CORPUS / entry.gold).read_text(encoding="utf-8"))
    if gold.get("encoding") != RANGES:
        return {"id": gold["id"], "pages": gold["pages"]}
    return decode_gold(gold, document(entry) if pdf is None else pdf)


def dump_manifest(labels: dict, entries: list[dict]) -> str:
    """The manifest's text: one field a line, so a changed hash is a one-line diff."""
    lines = ["{", ' "labels": {']
    for n, (name, how) in enumerate(labels.items()):
        comma = "," if n + 1 < len(labels) else ""
        lines.append(f"  {json.dumps(name)}: {json.dumps(how, ensure_ascii=False)}{comma}")
    lines.extend([" },", ' "entries": ['])
    for n, entry in enumerate(entries):
        fields = [
            f"   {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in entry.items()
        ]
        lines.append("  {\n" + ",\n".join(fields) + "\n  }" + ("," if n + 1 < len(entries) else ""))
    lines.extend([" ]", "}", ""])
    return "\n".join(lines)


def dump_gold(gold: dict) -> str:
    """A gold file's text: one block a line, so a changed label is a one-line diff."""
    lines = ["{", f' "id": {json.dumps(gold["id"])},']
    lines.append(f' "encoding": {json.dumps(gold.get("encoding", TEXT))},')
    lines.append(' "pages": [')
    for p, page in enumerate(gold["pages"]):
        head = {k: v for k, v in page.items() if k != "blocks"}
        lines.append("  {" + json.dumps(head, ensure_ascii=False)[1:-1] + ', "blocks": [')
        for b, block in enumerate(page["blocks"]):
            comma = "," if b + 1 < len(page["blocks"]) else ""
            lines.append("   " + json.dumps(block, ensure_ascii=False) + comma)
        lines.append("  ]}" + ("," if p + 1 < len(gold["pages"]) else ""))
    lines.extend([" ]", "}", ""])
    return "\n".join(lines)


# --- Command line ---------------------------------------------------------------------


def check(*, fetch_public: bool, get: Fetcher = download) -> list[str]:
    """Problems found reading every entry's file and gold. Public files only when asked."""
    found: list[str] = []
    for entry in load_manifest().values():
        if entry.kind == "public" and not fetch_public:
            continue
        try:
            pdf = document(entry, get=get)
            if entry.gold:
                load_gold(entry, pdf)
        except CorpusError as exc:
            found.append(f"{entry.id}: {exc}")
    return found


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check the test corpus or encode gold for it.")
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser("check", help="read every file and gold label")
    checking.add_argument("--fetch", action="store_true", help="also fetch the public files")
    encoding = commands.add_parser("encode", help="rewrite text gold as page ranges")
    encoding.add_argument("id", help="the public entry the gold is for")
    encoding.add_argument("gold", type=Path, help="gold with text, kept outside the repository")
    args = parser.parse_args(argv)

    if args.command == "check":
        found = check(fetch_public=args.fetch)
        for line in found:
            print(line)
        entries = load_manifest()
        skipped = 0 if args.fetch else sum(e.kind == "public" for e in entries.values())
        print(f"{len(entries) - skipped} entries read, {skipped} public entries not fetched")
        return 1 if found else 0

    entry = load_manifest()[args.id]
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    encoded = encode_gold({**gold, "id": entry.id}, document(entry))
    target = CORPUS / (entry.gold or f"gold/{entry.id}.json")
    target.write_text(dump_gold(encoded), encoding="utf-8")
    print(f"wrote {target.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
