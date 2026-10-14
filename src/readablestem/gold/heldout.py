"""The pre-registered held-out set: 15 OpenStax sections that no development step has seen.

`REGISTERED` is the list the pre-registration fixed on 2026-10-13, before any system ran
on it, and the hashes of the gold files `gold fetch` wrote for it that day. This module
only knows the list and how to rebuild and check the gold; it prints, converts and scores
nothing, and it holds no OpenStax text (slugs and hashes only).

The sections come from retired first editions, because every live CC BY STEM book was used
in development and every current edition of the others is CC BY-NC-SA. Whether a retired
edition is acceptable ground truth is open (docs/decisions/0001-scope.md, question 2), so
fetching needs the same `--allow-retired` as any retired book.

Rules the held-out gold follows (the pre-registration, section 10):

- It is never written into `data/gold`, a test fixture or the repository's caches. The CLI
  keeps it, its caches and its reports outside the project folder (`cli.heldout`).
- A fetch reads the pinned archive release, so the files do not change when OpenStax
  publishes a new one. Rebuilding them byte for byte also needs the registered date:
  `gold fetch --set heldout --allow-retired --date 2026-10-13`.
- `verify_gold` is the check a run makes before it starts: the files, the manifest and the
  attribution must be the registered ones.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from readablestem.gold.openstax import Book, BookPin, Fetcher, GoldError, PageResult, fetch_gold

HELDOUT_SET = "heldout"
SHOWN_PATHS = 4  # file names listed in one problem line


class HeldoutError(GoldError):
    """The held-out gold on disk is not the registered one (or cannot be checked)."""


@dataclass(frozen=True)
class Section:
    book: str
    slug: str
    pdf_pages: int  # Letter pages `eval render` printed on the registration day
    sha256: str  # of the gold JSONL file, as `gold fetch` wrote it
    content_sha256: str  # the same file without its `retrieved_at` fields

    @property
    def path(self) -> str:
        """Where the gold file lies under the gold folder."""
        return f"openstax/{self.book}/{self.slug}.jsonl"


@dataclass(frozen=True)
class Registration:
    date: str  # the day the gold was fetched; every record's `retrieved_at`
    sections: tuple[Section, ...]
    pins: Mapping[str, BookPin]
    manifest_sha256: str
    attribution_sha256: str
    preregistration_sha256: str | None = None  # of PREREGISTRATION.md, for the report

    @property
    def pages(self) -> dict[str, tuple[str, ...]]:
        """Page slugs per book, books and sections in registration order."""
        books: dict[str, list[str]] = {}
        for section in self.sections:
            books.setdefault(section.book, []).append(section.slug)
        return {book: tuple(slugs) for book, slugs in books.items()}

    @property
    def total_pdf_pages(self) -> int:
        return sum(section.pdf_pages for section in self.sections)

    def section(self, slug: str, book: str | None = None) -> Section | None:
        """The section with this page slug (and book, when given)."""
        for section in self.sections:
            if section.slug == slug and book in (None, section.book):
                return section
        return None


_ARCHIVE = "20260604.144757"
_BOOK_VERSIONS = {
    "college-physics": "1aa3e7a",
    "chemistry": "741a7af",
    "precalculus": "ebc5beb",
    "introductory-statistics": "1d23970",
    "intermediate-algebra": "eb2e29d",
}

# Book order and, within a book, the order the pre-registration lists them (its low, mid
# and high maths-density strata). Page counts are section 4.4 of the pre-registration: 242.
REGISTERED = Registration(
    date="2026-10-13",
    preregistration_sha256="d4d3af030b403a84da2c1a6787dc9773775a826cfa4f86edf9b48f7ec3f288ec",
    manifest_sha256="9be8c5650fe492d0c5312199b3040aa6ea741595d5ba97df4b68e816455c9aa3",
    attribution_sha256="d84e481532b7d135f8900fca1efc89e53c67b9736010f2bc88ba6e05d4289325",
    pins={book: BookPin(_ARCHIVE, version) for book, version in _BOOK_VERSIONS.items()},
    sections=(
        Section(
            "college-physics",
            "29-3-photon-energies-and-the-electromagnetic-spectrum",
            10,
            "8b5c9fa6b865fccc825415cfd28e3ec8355bb43377a38ee40c5c8b4568daa1bc",
            "995bbd5f9ff895269740853b51b0e44718f5efd3e3c0a531e45977256f8659eb",
        ),
        Section(
            "college-physics",
            "5-3-elasticity-stress-and-strain",
            14,
            "c808939fc6736ba951cb77277a00c17a0c46d5048c3e7316e2957e8fb65a71ee",
            "5ebac7b0e4c8cde016ed12a931dbdc70265249b3cb3c068ec094287da24c4b47",
        ),
        Section(
            "college-physics",
            "28-4-relativistic-addition-of-velocities",
            9,
            "86198c1eef84afe0668355d859969b76e8b6ca98fb119119b2077baf90ad3575",
            "ab2c2b038995bb7e7c09cbcbf9cd6ee7d6585fbb67a64bd4ce7549671254dd10",
        ),
        Section(
            "chemistry",
            "19-3-spectroscopic-and-magnetic-properties-of-coordination-compounds",
            8,
            "f7378d24fe8e3b05acde023bc2b34cec70afb8dfc17eed1894f488ee657d88fc",
            "c8b47369875155d5f1a36c1235e3a1a266783d62b6219471e64bedfb13a2e329",
        ),
        Section(
            "chemistry",
            "7-3-lewis-symbols-and-structures",
            9,
            "aff3da30565c4b32c46959e36ea30469a3e225df831bb725dc509e7e757ccc6b",
            "2eb8ffa92dbe81cf99b28609cd5edf02438b5574da85644bada9cda3991a417a",
        ),
        Section(
            "chemistry",
            "14-7-acid-base-titrations",
            11,
            "4f147c83666ae70c12bf60a91d6d6d4ac1677acbfb5c5f9516a2837dd88f69b5",
            "fc465888a0b5f75d9f983421c40cef7e6b90d37391ce590030a8dbfefecece56",
        ),
        Section(
            "precalculus",
            "5-4-right-triangle-trigonometry",
            29,
            "f47bfa0524d6691d3eb42fde7f00e8bb3457854bddc567ac3e61377260e40821",
            "8a0e5e0cb505164475a474fea9b28403e6dc60042bb258ea2987ff4ff29df9d6",
        ),
        Section(
            "precalculus",
            "1-6-absolute-value-functions",
            28,
            "6e894af136a1aa703c698aafa6929de34887f6193eecccf588821fbd0b04c677",
            "cd060c941fc2355467328466bf2425efd6831f35d629cb3335baf317f6d645d9",
        ),
        Section(
            "precalculus",
            "3-1-complex-numbers",
            20,
            "b83420ba314019a30a8243aa5330ec2b06e303dfbd931f931bb24043efa37690",
            "ffa8bb285aabfc800a4030f85a855ebf0acf05da78d0713954c9ba20ce3055f5",
        ),
        Section(
            "introductory-statistics",
            "2-4-box-plots",
            9,
            "e0e1f63c5b6f9d613b012e30f5559784ca668ce5a55f12609fc94b8c2ff9bee8",
            "3e52b184655e96fac6a37d19dfde5c6d4bdc02c5d077c881ea5ed4a35682477d",
        ),
        Section(
            "introductory-statistics",
            "4-5-hypergeometric-distribution",
            6,
            "4f9b5adc3f990816f7dec32d3b9745dd456fa2e4a78e0e35f99bb7f673a46acd",
            "77099578db8f312f12b791cce307c0cf908ac8116594b526b8e194237b8c2867",
        ),
        Section(
            "introductory-statistics",
            "3-1-terminology",
            8,
            "01be9ec7d71e7c93dac7ba7d564338b8536267e44b3301c9009b972b6e384a9c",
            "1ae1ceab51321f23d95add9cbcd4cbbd850273afc9b8572a8daf3df95424a82c",
        ),
        Section(
            "intermediate-algebra",
            "4-3-solve-mixture-applications-with-systems-of-equations",
            28,
            "838ececd5ad8a35ced3557860f75238a8a86932b4e039d2d7a2d825b23692231",
            "63ba732d1bdc509a7f66b573dfea4bdd88932fb6b32c47824e81eedd4db436d4",
        ),
        Section(
            "intermediate-algebra",
            "4-6-solve-systems-of-equations-using-determinants",
            27,
            "80fbbac890ef971d7333a08ee8c0abeb9a98195158a04b37066407aeed1e88c9",
            "3864d707a76e242740f1d1d6cf70e32b61c18c14fd61fe3de1e77673e2c932e1",
        ),
        Section(
            "intermediate-algebra",
            "1-5-properties-of-real-numbers",
            26,
            "19235caefa9c5a496ed13b456e35a95656dc2950ed49460278100178f9f24dda",
            "baa50079154ee351cdc424ea934fa96fa2dddfc7fab27d2dd160305720d7fe3b",
        ),
    ),
)


# ------------------------------------------------------------------------- hashing


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_sha256(path: Path) -> str:
    """Hash of a gold JSONL file without its `retrieved_at` fields (what a re-fetch on
    another day keeps the same), over each record re-serialised as `gold fetch` writes it."""
    digest = hashlib.sha256()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record.pop("retrieved_at", None)
        digest.update(json.dumps(record, ensure_ascii=False, sort_keys=True).encode() + b"\n")
    return digest.hexdigest()


# ------------------------------------------------------------------------- fetching


def fetch_set(
    out_dir: Path,
    fetcher: Fetcher,
    *,
    retrieved_at: str,
    allow_retired: bool = False,
    registration: Registration | None = None,
) -> list[tuple[Book, list[PageResult]]]:
    """Fetch every registered section (all four record kinds) into `out_dir`.

    Same files, manifest and ATTRIBUTION.md as `fetch_gold`; each book is read from its
    pinned archive release. Returns `(book, results)` per book, in registration order.
    The license gate (plain CC BY in the archive and the CMS) and the state gate apply:
    retired books need `allow_retired`. `registration` defaults to `REGISTERED`.
    """
    registration = registration or REGISTERED
    fetched: list[tuple[Book, list[PageResult]]] = []
    for book, slugs in registration.pages.items():
        fetched.append(
            fetch_gold(
                book,
                len(slugs),
                out_dir,
                fetcher,
                page_slugs=list(slugs),
                retrieved_at=retrieved_at,
                allow_retired=allow_retired,
                pin=registration.pins[book],
            )
        )
    return fetched


# ------------------------------------------------------------------------ checking


def _listed(names: list[str]) -> str:
    shown = ", ".join(names[:SHOWN_PATHS])
    return shown + (f" and {len(names) - SHOWN_PATHS} more" if len(names) > SHOWN_PATHS else "")


def _book_file_problems(gold_dir: Path, registration: Registration) -> list[str]:
    """Each `_book.json` must be the book entry the manifest lists (the manifest's hash
    covers the registered entries, so this ties the folder to them)."""
    try:
        manifest = json.loads((gold_dir / "manifest.json").read_text(encoding="utf-8"))
        listed = manifest.get("books", {})
    except (OSError, ValueError):
        return []  # already reported through the manifest's hash
    unreadable: list[str] = []
    different: list[str] = []
    for book in registration.pins:
        path = gold_dir / "openstax" / book / "_book.json"
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(f"{book}/_book.json")
            continue
        if found != listed.get(book):
            different.append(f"{book}/_book.json")
    problems = []
    if unreadable:
        problems.append(
            f"{len(unreadable)} book file(s) missing or not JSON: {_listed(unreadable)}"
        )
    if different:
        problems.append(
            f"{len(different)} book file(s) differ from the manifest: {_listed(different)}"
        )
    return problems


def verify_gold(gold_dir: Path, registration: Registration | None = None) -> list[str]:
    """What differs between `gold_dir` and the registered held-out gold (empty: nothing).

    Checks the 15 files against their registered hashes, that no other gold file is
    there, manifest.json and ATTRIBUTION.md against theirs, and each `_book.json` against
    the manifest. A file that differs only in `retrieved_at` is named as such, since
    fetching on another day does that. `registration` defaults to `REGISTERED`.
    """
    registration = registration or REGISTERED
    if not gold_dir.is_dir():
        return [f"gold folder not found: {gold_dir}"]
    missing: list[str] = []
    date_only: list[str] = []
    changed: list[str] = []
    for section in registration.sections:
        path = gold_dir / section.path
        if not path.is_file():
            missing.append(section.path)
        elif file_sha256(path) != section.sha256:
            try:
                same = content_sha256(path) == section.content_sha256
            except (OSError, ValueError):
                same = False
            (date_only if same else changed).append(section.path)
    registered = {section.path for section in registration.sections}
    # Every .jsonl anywhere below: the loaders read all of them (`iter_gold_records`).
    extra = sorted(
        path.relative_to(gold_dir).as_posix()
        for path in gold_dir.rglob("*.jsonl")
        if path.relative_to(gold_dir).as_posix() not in registered
    )

    problems = []
    if missing:
        problems.append(f"{len(missing)} registered file(s) missing: {_listed(missing)}")
    if changed:
        problems.append(f"{len(changed)} file(s) differ in content: {_listed(changed)}")
    if date_only:
        problems.append(
            f"{len(date_only)} file(s) differ from the registered ones only in retrieved_at "
            f"(registered: {registration.date}); fetch again with --date {registration.date}"
        )
    if extra:
        problems.append(f"{len(extra)} file(s) outside the registered set: {_listed(extra)}")
    for name, expected in (
        ("manifest.json", registration.manifest_sha256),
        ("ATTRIBUTION.md", registration.attribution_sha256),
    ):
        path = gold_dir / name
        if not path.is_file():
            problems.append(f"{name} is missing")
        elif file_sha256(path) != expected:
            problems.append(f"{name} is not the registered one (sha256 {file_sha256(path)[:12]}…)")
    return problems + _book_file_problems(gold_dir, registration)


def require_gold(gold_dir: Path, registration: Registration | None = None) -> None:
    """Raise `HeldoutError` unless `gold_dir` is the registered held-out gold."""
    problems = verify_gold(gold_dir, registration)
    if problems:
        raise HeldoutError(
            "this is not the pre-registered held-out gold:\n  - " + "\n  - ".join(problems)
        )
