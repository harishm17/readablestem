"""Course-style LaTeX for the real-world evaluation set (docs/eval-realworld.md, stratum C).

Lecture notes, problem sets and Beamer decks published as LaTeX source on GitHub under a licence
that lets derived data be redistributed. Nothing here reads the wall clock or an unseeded random
source: the repository pool is harvested once with a fixed query set and recorded (the record is
what gets registered); the order and the draw are functions of the pool and a seed.

Steps, each a function here and a subcommand of `scripts/fresh5_select.py`:

1. `harvest`: GitHub repository search with `SEARCH_QUERIES` (every topic phrase against every
   accepted licence qualifier). A repository is kept only if GitHub reports a licence that
   `licence_gate` accepts (CC BY, CC BY-SA or MIT), and it is not a fork or archived.
2. `order_repos` / `order_documents`: SHA-256 of seed and name, never a choice.
3. `fetch_repo`: the tarball of the default branch's commit (pinned by SHA), unpacked by
   `safe_tex.extract_tar` (no links, no unsafe paths, size and file caps, renamed into place only
   when complete); the licence is read again from the repository record.
4. `discover`: the files with `\\documentclass` and `\\begin{document}`; Beamer is `beamer` as the
   class (or a `\\usepackage{beamer}`).
5. `compile_document`: pdflatex or xelatex from a user-level TeX Live (latexmk), then Tectonic if
   neither compiles it, all through `safe_tex.compile_tex` (sandboxed, no network, no rc file, no
   shell escape, reads and writes only below the working tree); the engine and its version are
   recorded.
6. `draw`: walk repositories in the seeded order and their documents in the seeded order, accept a
   document that passes the gates, at most `PER_REPO` per repository and `VISITS_PER_REPO` tried
   in one, stop at `TARGET` documents;
   non-Beamer documents are capped so that at least `MIN_BEAMER` Beamer decks fit.

Gold is made by the arXiv path (`arxiv_gold.analyse`, `choose_pages`, `gold_records`) from the
document's own source and the PDF compiled from it, so the answer key and the PDF agree by
construction. Downloaded files are untrusted data: nothing in them is run except the TeX engine
in no-shell-escape mode.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from readablestem.gold import arxiv_gold as ag
from readablestem.gold import safe_tex
from readablestem.gold.arxiv_corpus import stable_key

USER_AGENT = "readablestem-eval/0.1 (research; contact: harishm6250@gmail.com)"
API = "https://api.github.com"

# GitHub's SPDX id -> (short name, licence URL). Everything else (NC, ND, GPL, no licence, "other")
# is refused: derived gold of those documents may not be redistributed freely.
ACCEPTED_LICENCES: dict[str, tuple[str, str]] = {
    "CC-BY-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "CC-BY-3.0": ("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0/"),
    "CC-BY-SA-4.0": ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "CC-BY-SA-3.0": ("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/"),
    "MIT": ("MIT", "https://opensource.org/license/mit"),
}
# The GitHub `license:` qualifier that finds each accepted id.
LICENCE_QUALIFIERS = ("cc-by-4.0", "cc-by-sa-4.0", "mit")  # GitHub has no 3.0 qualifier
# The fixed topic phrases. Together with the qualifiers they make the query set that is registered.
TOPIC_PHRASES = (
    "lecture notes",
    "lecture slides",
    "course slides",
    "beamer slides",
    "beamer lecture",
    "beamer presentation",
    "problem set",
)
# GitHub topics (what authors tag a repository with), searched the same way.
TOPICS = ("beamer", "beamer-slides", "lecture-notes")
QUERY_SUFFIX = "language:TeX fork:false archived:false"
PER_QUERY = 100  # repositories kept from each query (one page of 100, sorted by stars)

MAX_REPO_KB = 120_000
MAX_DOWNLOAD = 120_000_000
COMPILE_TIMEOUT = 150
MIN_PDF_PAGES, MAX_PDF_PAGES = 6, 200
MIN_TEXT_PER_PAGE = 300  # characters of text layer per page (a page of notes)
MIN_TEXT_PER_SLIDE = 100  # a Beamer slide carries less text than a page of notes
MIN_EQUATIONS_PAGE = 5  # equations on a usable page (as for papers)
MIN_EQUATIONS_SLIDE = 3  # a slide shows fewer equations than a page of notes
MAX_UNCONVERTIBLE = 0.10
PER_REPO = 2
VISITS_PER_REPO = 6  # documents tried in one repository, accepted or not
TARGET = 8
MIN_BEAMER = 3

_DOCUMENTCLASS = re.compile(r"^[^%\n]*\\documentclass(?:\[[^\]]*\])?\{([^}]*)\}", re.M)
_BEAMER_PACKAGE = re.compile(r"^[^%\n]*\\usepackage(?:\[[^\]]*\])?\{beamer\}", re.M)


class CourseError(RuntimeError):
    pass


class Skip(Exception):
    """A document or repository that fails a gate; `code` is the category written to the log."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


# ------------------------------------------------------------------------------- licence


@dataclass(frozen=True)
class Licence:
    spdx: str
    name: str
    url: str


def licence_gate(spdx_id: str | None) -> Licence | None:
    """The licence for a GitHub SPDX id, or None if the repository may not be used."""
    if not spdx_id or spdx_id not in ACCEPTED_LICENCES:
        return None
    name, url = ACCEPTED_LICENCES[spdx_id]
    return Licence(spdx_id, name, url)


def require_licence(spdx_id: str | None) -> Licence:
    found = licence_gate(spdx_id)
    if found is None:
        raise Skip("licence_refused", str(spdx_id))
    return found


# --------------------------------------------------------------------------------- pool


@dataclass
class Repo:
    full_name: str
    licence: str  # SPDX id
    default_branch: str
    size_kb: int
    stars: int
    pushed_at: str
    html_url: str
    queries: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def search_queries() -> list[tuple[str, str]]:
    """(label, GitHub `q`) for every phrase and topic against every licence: the fixed query set."""
    out = []
    for phrase in TOPIC_PHRASES:
        for qualifier in LICENCE_QUALIFIERS:
            label = f"{phrase}|{qualifier}"
            out.append((label, f'"{phrase}" license:{qualifier} {QUERY_SUFFIX}'))
    for topic in TOPICS:
        for qualifier in LICENCE_QUALIFIERS:
            label = f"topic:{topic}|{qualifier}"
            out.append((label, f"topic:{topic} license:{qualifier} {QUERY_SUFFIX}"))
    return out


def parse_repo(item: dict[str, Any], label: str) -> Repo | None:
    """A search result as a `Repo`, or None when the licence gate, fork or archive rule refuses."""
    if item.get("fork") or item.get("archived") or item.get("disabled"):
        return None
    spdx = (item.get("license") or {}).get("spdx_id")
    if spdx is None or licence_gate(spdx) is None:
        return None
    if int(item.get("size") or 0) > MAX_REPO_KB:
        return None
    return Repo(
        full_name=item["full_name"],
        licence=spdx,
        default_branch=item.get("default_branch") or "main",
        size_kb=int(item.get("size") or 0),
        stars=int(item.get("stargazers_count") or 0),
        pushed_at=item.get("pushed_at") or "",
        html_url=item["html_url"],
        queries=[label],
    )


def merge_repos(found: Iterable[Repo]) -> list[Repo]:
    """One record per repository (queries joined), sorted by name so the file is stable."""
    by_name: dict[str, Repo] = {}
    for repo in found:
        known = by_name.get(repo.full_name.lower())
        if known is None:
            by_name[repo.full_name.lower()] = repo
        else:
            known.queries = sorted(set(known.queries) | set(repo.queries))
    return sorted(by_name.values(), key=lambda r: r.full_name.lower())


def harvest(
    token: str | None, *, pause: float = 7.0, log: Callable[[str], None] = print
) -> tuple[list[Repo], dict[str, int]]:
    """Run the fixed queries; return the merged pool and the hit counts per query."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    found: list[Repo] = []
    counts: dict[str, int] = {}
    with httpx.Client(headers=headers, timeout=60) as client:
        for label, q in search_queries():
            for attempt in range(5):
                response = client.get(
                    f"{API}/search/repositories",
                    params={"q": q, "sort": "stars", "order": "desc", "per_page": PER_QUERY},
                )
                if response.status_code == 200:
                    break
                if response.status_code in (403, 429):
                    time.sleep(30 * (attempt + 1))
                    continue
                response.raise_for_status()
            else:
                raise CourseError(f"search failed for {label}")
            items = response.json().get("items", [])
            kept = [r for r in (parse_repo(i, label) for i in items) if r is not None]
            counts[label] = len(kept)
            found += kept
            log(f"{label}: {len(items)} results, {len(kept)} pass the gates")
            time.sleep(pause)
    return merge_repos(found), counts


# --------------------------------------------------------------------------------- order


def order_repos(repos: list[Repo], seed: str) -> list[Repo]:
    return sorted(repos, key=lambda r: stable_key(seed, "repo", r.full_name.lower()))


def order_documents(paths: Iterable[str], seed: str, repo: str) -> list[str]:
    return sorted(paths, key=lambda p: stable_key(seed, "doc", repo.lower(), p))


# ------------------------------------------------------------------------------- download


def commit_sha(client: httpx.Client, repo: Repo) -> str:
    response = client.get(f"{API}/repos/{repo.full_name}/commits/{repo.default_branch}")
    response.raise_for_status()
    return response.json()["sha"]


def repo_licence(client: httpx.Client, repo: Repo) -> Licence:
    """The licence as GitHub reports it now; it must still pass the gate."""
    response = client.get(f"{API}/repos/{repo.full_name}/license")
    if response.status_code != 200:
        raise Skip("licence_unreadable", str(response.status_code))
    spdx = (response.json().get("license") or {}).get("spdx_id")
    return require_licence(spdx)


def fetch_repo(client: httpx.Client, repo: Repo, base: Path) -> tuple[Path, str, Licence]:
    """(source folder, commit SHA, licence) of the repository, from the cache if present."""
    licence = repo_licence(client, repo)
    sha = commit_sha(client, repo)
    src = base / "src"
    if not src.is_dir():
        response = client.get(
            f"https://codeload.github.com/{repo.full_name}/tar.gz/{sha}",
            follow_redirects=True,
            timeout=180,
        )
        if response.status_code != 200:
            raise Skip(f"http_{response.status_code}")
        if len(response.content) > MAX_DOWNLOAD:
            raise Skip("download_too_large")
        try:
            safe_tex.extract_tar(response.content, src, strip_top=True)
        except safe_tex.UnsafeArchive as exc:
            raise Skip(exc.code, exc.detail) from None
    return src, sha, licence


# ------------------------------------------------------------------------------ discover


@dataclass(frozen=True)
class Doc:
    path: str  # relative to the repository, posix
    beamer: bool


def is_beamer(text: str) -> bool:
    cls = _DOCUMENTCLASS.search(text)
    return bool((cls and cls.group(1).strip() == "beamer") or _BEAMER_PACKAGE.search(text))


def discover(sources: dict[str, str]) -> list[Doc]:
    """Every file that is a complete document: `\\documentclass` and `\\begin{document}`."""
    docs = []
    for name, text in sorted(sources.items()):
        if not name.endswith((".tex", ".ltx")):
            continue
        if _DOCUMENTCLASS.search(text) and "\\begin{document}" in text:
            docs.append(Doc(name, is_beamer(text)))
    return docs


def sources_for(sources: dict[str, str], main: str) -> dict[str, str]:
    """`sources` without the other documents of the repository, so that `analyse` takes `main`."""
    others = {d.path for d in discover(sources)} - {main}
    return {k: v for k, v in sources.items() if k not in others}


# ------------------------------------------------------------------------------- compile


texlive_bin = safe_tex.texlive_bin
tectonic_path = safe_tex.tectonic_path
engines = safe_tex.engines


def compile_document(
    src: Path, main: str, out: Path, *, timeout: int = COMPILE_TIMEOUT
) -> tuple[Path, str]:
    """(the PDF of `src/main`, "engine: version") from the first engine that compiles it, run by
    `safe_tex.compile_tex` (sandboxed, no network, no rc file, no shell escape, reads and writes
    only below the working tree). The engine is written beside the PDF so that a cached build
    reports it too."""
    out.mkdir(parents=True, exist_ok=True)
    target = out / (Path(main).stem + ".pdf")
    record = out / (Path(main).stem + ".engine")
    if target.is_file() and record.is_file():
        return target, record.read_text(encoding="utf-8").strip()
    timed_out = False
    try:
        available = engines()
    except safe_tex.SandboxUnavailable as exc:
        raise CourseError(str(exc)) from None
    for name, version in available:
        for old in out.glob("*"):
            if old.is_file():
                old.unlink()
        try:
            run = safe_tex.compile_tex(src, main, out, name, timeout=timeout)
        except safe_tex.SandboxUnavailable as exc:
            raise CourseError(str(exc)) from None
        if run.timed_out:
            timed_out = True
            continue
        if run.returncode == 0 and target.is_file():
            label = f"{name}: {version}"
            record.write_text(label + "\n", encoding="utf-8")
            return target, label
    raise Skip("compile_timeout" if timed_out else "compile_failed")


# --------------------------------------------------------------------------------- gold


@dataclass
class Built:
    analysis: ag.Analysis
    pages: list[int]
    info: dict
    pdf: Path


def build_document(
    sources: dict[str, str], doc: Doc, src: Path, work: Path, seed: str, unit_id: str
) -> Built:
    """Compile, check the gates, analyse and choose pages. Raises `Skip` with a category."""
    pdf, engine = compile_document(src, doc.path, work)
    try:
        texts = ag.page_texts_of(pdf)
    except Exception as exc:
        raise Skip("unreadable_pdf", type(exc).__name__) from None
    if not MIN_PDF_PAGES <= len(texts) <= MAX_PDF_PAGES:
        raise Skip("pdf_page_count", str(len(texts)))
    if sum(len(t) for t in texts) / len(texts) < (
        MIN_TEXT_PER_SLIDE if doc.beamer else MIN_TEXT_PER_PAGE
    ):
        raise Skip("no_text_layer")
    result = ag.analyse(
        sources_for(sources, doc.path),
        texts,
        min_equations=MIN_EQUATIONS_SLIDE if doc.beamer else MIN_EQUATIONS_PAGE,
    )
    if isinstance(result, str):
        raise Skip(result)
    if result.unconvertible_share > MAX_UNCONVERTIBLE:
        raise Skip("unconvertible_equations", f"{result.unconvertible_share:.2f}")
    pages = ag.choose_pages(result.usable, seed, unit_id)
    if not pages:
        raise Skip("fewer_than_4_usable_pages", str(len(result.usable)))
    info = {
        "pdf_pages": len(texts),
        "pdf_sha256": ag.sha256_file(pdf),
        "tex_engine": engine,
        "main_tex": result.main,
        "equations": len(result.equations),
        "unconvertible": len(result.bad),
        "usable_pages": len(result.usable),
        "anchors": result.anchors,
    }
    return Built(result, pages, info, pdf)


def unit_id(repo: str, path: str) -> str:
    """`owner/repo/path/to/main` without the extension, as the gold's `arxiv_id` field holds it."""
    return f"course/{repo}/{Path(path).with_suffix('').as_posix()}"


def gold_records(
    built: Built, repo: Repo, doc: Doc, licence: Licence, sha: str, *, retrieved_at: str
) -> list[dict]:
    """The arXiv path's records, with the source fields of a course document.

    `arxiv_id` keeps its name because the report scripts group records by it; it holds the unit id.
    """
    uid = unit_id(repo.full_name, doc.path)
    meta = {
        "id": uid,
        "title": f"{repo.full_name}: {doc.path}",
        "licence_name": licence.name,
        "license": licence.url,
        "authors": repo.full_name.split("/")[0],
        "style": "beamer" if doc.beamer else "course",
    }
    slug = ag.paper_slug(uid)
    rows = ag.gold_records(
        built.analysis, built.pages, meta, pdf_relpath=f"pdfs/{slug}.pdf",
        retrieved_at=retrieved_at, pdf_sha256=built.info["pdf_sha256"], source_sha256=sha,
    )  # fmt: skip
    for row in rows:
        row["id"] = row["id"].replace("arxiv/", "course/", 1)
        row["source"] = "course"
        row["book_slug"] = "course"
        row["book_title"] = "Course-style LaTeX (GitHub, author source)"
        row["source_url"] = f"{repo.html_url}/blob/{sha}/{doc.path}"
        row["repo_commit"] = sha
    return rows


# --------------------------------------------------------------------------------- draw


def can_accept(doc: Doc, accepted: list[dict], repo: str) -> str | None:
    """Why a document that passed the build gates still is not taken, or None."""
    if sum(1 for a in accepted if a["repo"] == repo) >= PER_REPO:
        return "repo_cap"
    beamer = sum(1 for a in accepted if a["beamer"])
    if doc.beamer:
        return "beamer_full" if beamer >= TARGET else None
    if len(accepted) - beamer >= TARGET - MIN_BEAMER:
        return "non_beamer_full"
    return None


def draw(
    repos: list[Repo],
    seed: str,
    documents: Callable[[Repo], tuple[list[Doc], Callable[[Doc], dict]]],
    log: list[dict],
) -> list[dict]:
    """Walk the seeded order and take what passes. `documents(repo)` returns the repo's documents
    and a `build(doc)` that returns the accepted entry or raises `Skip`. Every visit goes to `log`;
    a skipped document is never replaced by choice."""
    accepted: list[dict] = []
    for repo in order_repos(repos, seed):
        if len(accepted) >= TARGET:
            break
        try:
            docs, build = documents(repo)
        except Skip as exc:
            log.append({"repo": repo.full_name, "outcome": "skipped", "reason": exc.code,
                        "detail": exc.detail})  # fmt: skip
            continue
        by_path = {d.path: d for d in docs}
        for path in order_documents(by_path, seed, repo.full_name)[:VISITS_PER_REPO]:
            if len(accepted) >= TARGET:
                break
            doc = by_path[path]
            entry = {"repo": repo.full_name, "path": path, "beamer": doc.beamer}
            refused = can_accept(doc, accepted, repo.full_name)
            if refused:
                log.append(entry | {"outcome": "skipped", "reason": refused})
                continue
            try:
                built = build(doc)
            except Skip as exc:
                log.append(entry | {"outcome": "skipped", "reason": exc.code, "detail": exc.detail})
                continue
            except Exception as exc:  # a source that breaks the reader is a skip, with its type
                log.append(entry | {"outcome": "skipped", "reason": "reader_error",
                                    "detail": type(exc).__name__})  # fmt: skip
                continue
            accepted.append(built)
            log.append(entry | {"outcome": "accepted"})
    return accepted


def dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
