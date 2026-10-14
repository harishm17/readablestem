"""Choosing and writing the extended development set (`gold devset select|build`).

`make_plan` scans the candidate books and chooses the sections of both parts; `build_set`
fetches the picks and writes the dev tree, its manifest, the attribution, the summary, the
sealed check archive and its seal. Both read OpenStax archive responses through a fetcher
(the repository's .cache/openstax, offline once warm) and the held-out text through a
second fetcher that caches outside the repository. Neither calls a model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from a11y_stem.gold import devset as ds
from a11y_stem.gold import openstax as ox

KEY_TERMS_SLUG = re.compile(r"^(\d+)-key-terms$")


@dataclass
class Plan:
    """What `make_plan` decided: sections and key-terms pages per part."""

    sections: dict[str, list[ds.Candidate]]
    key_terms: dict[str, list[ds.Candidate]]
    pool_size: int
    dev_pool: int

    @property
    def dev(self) -> list[ds.Candidate]:
        return self.sections["dev"]

    @property
    def check(self) -> list[ds.Candidate]:
        return self.sections["check"]

    def picks(self, part: str) -> list[ds.Candidate]:
        return [*self.sections[part], *self.key_terms[part]]

    def pages(self, part: str) -> int:
        return sum(c.pages_estimate for c in self.picks(part))

    @staticmethod
    def _row(c: ds.Candidate) -> dict:
        return {
            "book": c.book,
            "slug": c.slug,
            "title": c.title,
            "words": c.words,
            "equations": c.equations,
            "figures": c.figures,
            "tables": c.tables,
            "overlap_with_heldout": c.overlap,
            "pages_estimate": c.pages_estimate,
            "items": dict(sorted(c.counts.items())),
        }

    def dev_rows(self) -> list[dict]:
        return [self._row(c) for c in self.sections["dev"]]

    def key_terms_rows(self, part: str) -> list[dict]:
        return [self._row(c) for c in self.key_terms[part]]


def _key_terms_candidates(
    config: ds.SelectionConfig,
    fetcher: ox.Fetcher,
    reference: set[tuple[str, ...]],
    chosen: Sequence[ds.Candidate],
    target: int,
) -> list[ds.Candidate]:
    """Key-terms pages of the chapters the chosen sections belong to, fullest first,
    until `target` glossary entries are reached."""
    chapters = {(c.book, int(c.slug.split("-", 1)[0])) for c in chosen}
    found: list[ds.Candidate] = []
    for slug in sorted({book for book, _ in chapters}):
        book = ox.resolve_book(
            slug, fetcher, pin=ox.BookPin(config.archive_version, config.books[slug])
        )
        for page in ox.iter_pages(book.tree):
            match = KEY_TERMS_SLUG.match(page.slug)
            if not match or (slug, int(match.group(1))) not in chapters:
                continue
            xhtml = fetcher.get_json(book.content_url(page.uuid))["content"]
            cand = ds.candidate_from_page(book, page, xhtml, reference)
            if cand is not None and cand.overlap < ds.NEAR_DUP:
                found.append(cand)
    found.sort(key=lambda c: (-c.counts.get("glossary_entry", 0), c.key))
    out: list[ds.Candidate] = []
    total = 0
    for cand in found:
        if total >= target:
            break
        out.append(cand)
        total += cand.counts.get("glossary_entry", 0)
    return out


def make_plan(
    config: ds.SelectionConfig, fetcher: ox.Fetcher, reference: set[tuple[str, ...]]
) -> Plan:
    """Scan the candidate books and choose the sections and key-terms pages of both parts."""
    pool = ds.candidate_pool(config, fetcher, reference)
    sections = ds.choose_sections(config, pool)
    terms = {
        part: _key_terms_candidates(
            config,
            fetcher,
            reference,
            sections[part],
            (config.dev_targets if part == "dev" else config.check_targets).get(
                "glossary_entry", 0
            ),
        )
        for part in ds.PARTS
    }
    return Plan(
        sections=sections,
        key_terms=terms,
        pool_size=len(pool),
        dev_pool=len(ds.split_pool(config, pool)["dev"]),
    )


def fetch_picks(
    picks: Sequence[ds.Candidate],
    config: ds.SelectionConfig,
    fetcher: ox.Fetcher,
    retrieved_at: str,
) -> tuple[dict[tuple[str, str], list[dict]], dict[str, ox.Book]]:
    """Gold records of each picked page, and the books they come from."""
    books: dict[str, ox.Book] = {}
    pages: dict[tuple[str, str], list[dict]] = {}
    for pick in picks:
        if pick.book not in books:
            books[pick.book] = ox.resolve_book(
                pick.book,
                fetcher,
                pin=ox.BookPin(config.archive_version, config.books[pick.book]),
            )
            ox.check_public_license(books[pick.book])
        book = books[pick.book]
        if ds.is_heldout(pick.book, pick.slug) or pick.book in ds.heldout_books():
            raise ds.DevsetError(f"{pick.key} is held out")
        (page,) = ox.select_pages(ox.iter_pages(book.tree), 1, slugs=[pick.slug])
        xhtml = fetcher.get_json(book.content_url(page.uuid))["content"]
        pages[(pick.book, pick.slug)] = ds.build_page(book, page, xhtml, retrieved_at)
    return pages, books


def build_set(
    root: Path,
    config: ds.SelectionConfig,
    selection: Mapping,
    fetcher: ox.Fetcher,
    held_fetcher: ox.Fetcher,
    *,
    authored_pool: Path,
    date: str,
    overwrite: bool = False,
) -> dict:
    """Write the whole set under `root`: dev tree, manifest, attribution, summary, the
    sealed check archive and its seal. Returns the dev manifest."""
    dev_dir = root / "dev"
    if dev_dir.exists() and any(dev_dir.iterdir()) and not overwrite:
        raise ds.DevsetError(f"{dev_dir} already has files (use --force to write over them)")
    plan = make_plan(config, fetcher, ds.heldout_grams(held_fetcher))
    committed = [(s["book"], s["slug"]) for s in selection.get("dev", [])]
    if committed and committed != [(c.book, c.slug) for c in plan.dev]:
        raise ds.DevsetError("the dev sections chosen now differ from selection.json")
    parts: dict[str, dict[str, bytes]] = {}
    for part in ds.PARTS:
        pages, books = fetch_picks(plan.picks(part), config, fetcher, date)
        files = {**ds.openstax_files(pages, books, date), **ds.authored_files(authored_pool, part)}
        if not any(p.startswith("authored/") for p in files):
            raise ds.DevsetError(f"{authored_pool} has no authored documents of variant {part}")
        parts[part] = files
    dev_manifest = ds.part_manifest(parts["dev"], part="dev", date=date)
    check_manifest = ds.part_manifest(parts["check"], part="check", date=date)
    for path, data in parts["dev"].items():
        target = dev_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (dev_dir / "manifest.json").write_bytes(ds.manifest_json(dev_manifest))
    archive = ds.make_tar_gz({**parts["check"], "manifest.json": ds.manifest_json(check_manifest)})
    (root / ds.CHECK_ARCHIVE).write_bytes(archive)
    seal = ds.seal_record(archive, parts["check"], check_manifest, date)
    (root / ds.SEAL_FILE).write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
    (root / "ATTRIBUTION.md").write_text(ds.attribution_text(dev_manifest), encoding="utf-8")
    rows = ds.summary_rows(dev_manifest, seal["class_counts"])
    (root / "summary.md").write_text(
        "# Extended development set: items per class\n\n"
        "Generated by `a11y-stem gold devset build`; the same table is printed by "
        "`a11y-stem gold devset summary`. Check counts are totals only.\n\n"
        + ds.summary_markdown(rows),
        encoding="utf-8",
    )
    return dev_manifest
