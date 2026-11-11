"""The test corpus is what its manifest says: every file has a source, a license and a hash.

Covers the manifest's rules, the hash-checked fetch, the gold labels of files that need no
network, and the fixture builders, which must give their recorded bytes on every run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import builders
import pytest

from readablestem.eval import corpus

BUILDERS = Path(builders.__file__).parent
ENTRIES = corpus.load_manifest()
FIXTURES = builders.load()
STABLE = sorted(name for name, item in FIXTURES.items() if not item.unstable)
QUICK = [name for name in STABLE if not FIXTURES[name].slow]
PUBLIC = next(entry for entry in ENTRIES.values() if entry.kind == "public")


def test_every_entry_names_its_source_license_and_hash() -> None:
    recorded = builders.recorded()
    for entry in ENTRIES.values():
        assert entry.license, entry.id
        if entry.kind == "public":
            assert entry.url and entry.sha256 and entry.attribution, entry.id
        elif entry.kind == "committed":
            assert entry.sha256 and entry.made and entry.made["how"], entry.id
            assert entry.own or entry.attribution, entry.id
        else:
            assert entry.builder in recorded, f"{entry.id}: {entry.builder} has no recorded hash"


def test_every_file_in_the_corpus_folder_is_listed_and_matches_its_hash() -> None:
    listed = {"corpus.json", "README.md", "print_selfmade.py"}
    for entry in ENTRIES.values():
        if entry.kind == "committed":
            listed.add(entry.path)
            listed.update(entry.made.get("from", []))
            assert len(corpus.document(entry)) == entry.bytes  # document() checks the hash
        if entry.gold:
            listed.add(entry.gold)
    on_disk = {
        path.relative_to(corpus.CORPUS).as_posix()
        for path in corpus.CORPUS.rglob("*")
        if path.is_file()
    }
    assert on_disk == listed


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"url": "https://example.com/notes.pdf"}, "not an allowed site"),
        ({"url": "http://ocw.mit.edu/notes.pdf"}, "not https"),
        ({"title": "Copied from /home/someone/course/notes.pdf"}, "a path on one machine"),
        ({"attribution": "see https://drive.example.org/private/notes"}, "not an allowed site"),
        ({"sha256": "abc"}, "sha256"),
        ({"license": ""}, "license is missing"),
        ({"kind": "committed", "path": "../../secrets.pdf"}, "inside the corpus folder"),
    ],
)
def test_an_entry_that_breaks_a_rule_is_refused(change: dict, says: str) -> None:
    raw = json.loads(corpus.MANIFEST.read_text(encoding="utf-8"))["entries"]
    entry = {**next(e for e in raw if e["id"] == PUBLIC.id), **change}
    assert any(says in line for line in corpus.problems(entry)), corpus.problems(entry)


def test_a_file_someone_else_owns_is_committed_only_under_an_open_license() -> None:
    raw = json.loads(corpus.MANIFEST.read_text(encoding="utf-8"))["entries"]
    cited = next(e for e in raw if e["kind"] == "committed" and e["license"] != corpus.OWN_LICENSE)
    closed = corpus.problems({**cited, "license": "CC BY-NC-SA 4.0"})
    assert any("may be committed" in line for line in closed)
    large = corpus.problems({**cited, "license": corpus.OWN_LICENSE, "bytes": 250_000})
    assert any("at most" in line for line in large)


def test_a_fetched_file_that_differs_by_one_byte_is_refused_and_not_kept(tmp_path: Path) -> None:
    good = b"%PDF-1.7 the pinned bytes"
    entry = corpus.Entry(
        id="pinned",
        kind="public",
        title="Pinned",
        document="notes",
        features="",
        license="CC BY 4.0",
        attribution="Someone",
        url="https://ocw.mit.edu/pinned.pdf",
        sha256=corpus.sha256(good),
        bytes=len(good),
        pages=(1, 1),
    )
    calls: list[str] = []

    def serve(data: bytes) -> corpus.Fetcher:
        def get(url: str, size: int) -> bytes:
            calls.append(url)
            return data

        return get

    with pytest.raises(corpus.CorpusError, match="the manifest says"):
        corpus.fetch(entry, cache=tmp_path, get=serve(good[:-1] + b"t"))
    assert list(tmp_path.iterdir()) == []

    assert corpus.fetch(entry, cache=tmp_path, get=serve(good)) == good
    assert corpus.fetch(entry, cache=tmp_path, get=serve(b"not asked again")) == good
    assert len(calls) == 2


def test_gold_without_text_rebuilds_the_labeled_text_from_the_file() -> None:
    entry = next(e for e in ENTRIES.values() if e.kind == "committed" and e.gold and not e.own)
    stored = (corpus.CORPUS / entry.gold).read_text(encoding="utf-8")
    assert '"text"' not in stored
    gold = corpus.load_gold(entry)
    blocks = [block for page in gold["pages"] for block in page["blocks"]]
    assert blocks and all(block["text"] and block["role"] for block in blocks)

    other = next(e for e in ENTRIES.values() if e.kind == "committed" and e.own)
    with pytest.raises(corpus.CorpusError, match="not the words the gold was encoded against"):
        corpus.load_gold(entry, corpus.document(other))


def test_gold_for_a_file_made_here_reads_without_the_network() -> None:
    assert corpus.check(fetch_public=False) == []


@pytest.mark.network
def test_every_public_file_still_serves_its_pinned_bytes_and_its_gold_decodes() -> None:
    assert corpus.check(fetch_public=True) == []


def _in_a_new_process(*args: str) -> dict:
    out = subprocess.run(
        [sys.executable, str(BUILDERS / "record.py"), "hashes", *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "77"},
    )
    return json.loads(out.stdout)


def _assert_recorded(names: list[str], fresh: dict) -> None:
    recorded = builders.recorded()
    assert sorted(recorded) == STABLE
    for name in names:
        data = FIXTURES[name].make()
        here = {"sha256": builders.sha256(data), "bytes": len(data)}
        assert here == recorded[name], f"{name} built in this process"
        assert fresh[name] == recorded[name], f"{name} built in a new process"


def test_every_builder_gives_its_recorded_bytes_in_two_runs() -> None:
    _assert_recorded(QUICK, _in_a_new_process())


@pytest.mark.slow
def test_the_slow_builders_give_their_recorded_bytes_in_two_runs() -> None:
    _assert_recorded([n for n in STABLE if FIXTURES[n].slow], _in_a_new_process("--slow"))


def test_a_file_that_cannot_be_byte_stable_still_has_what_its_test_needs() -> None:
    unstable = [item for item in FIXTURES.values() if item.unstable]
    assert unstable
    for item in unstable:
        assert item.check is not None
        item.check(item.make())
