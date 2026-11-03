"""The accessibility conformance report draft (docs/acr/acr-draft.md) keeps its promises.

Offline. It does not judge the report's findings; it holds the report to its own rules so a
row cannot say more than its evidence: every WCAG 2.2 A and AA criterion is there once, a row
that says "Supports" names a test or a place in the code, every test and file a row names
exists, a row that waits for the screen reader session says so, the counts add up, and no
sentence claims conformance (CONTRIBUTING.md, wording).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
ACR = (ROOT / "docs/acr/acr-draft.md").read_text(encoding="utf-8")
LEVELS = ("Supports", "Partially Supports", "Not Applicable", "Not yet evaluated")

LEVEL_A = re.findall(
    r"\d\.\d+\.\d+",
    "1.1.1 1.2.1 1.2.2 1.2.3 1.3.1 1.3.2 1.3.3 1.4.1 1.4.2 2.1.1 2.1.2 2.1.4 2.2.1 2.2.2 2.3.1 "
    "2.4.1 2.4.2 2.4.3 2.4.4 2.5.1 2.5.2 2.5.3 2.5.4 3.1.1 3.2.1 3.2.2 3.2.6 3.3.1 3.3.2 3.3.7 "
    "4.1.1 4.1.2",
)
LEVEL_AA = re.findall(
    r"\d\.\d+\.\d+",
    "1.2.4 1.2.5 1.3.4 1.3.5 1.4.3 1.4.4 1.4.5 1.4.10 1.4.11 1.4.12 1.4.13 2.4.5 2.4.6 2.4.7 "
    "2.4.11 2.5.7 2.5.8 3.1.2 3.2.3 3.2.4 3.3.3 3.3.4 3.3.8 4.1.3",
)
# The rows whose answer depends on what a screen reader says (and the others
# the VoiceOver sheet feeds with a judgement of wording): they stay "Not yet evaluated".
NEEDS_VOICEOVER = {
    "1.1.1", "1.3.1", "1.3.2", "2.4.1", "2.4.2", "2.4.4", "3.3.1", "4.1.2",  # level A
    "2.4.6", "3.1.2", "4.1.3",  # level AA
}  # fmt: skip


def section(start: str, end: str) -> str:
    return ACR.split(start, 1)[1].split(end, 1)[0]


def rows(text: str) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        match = re.match(r"\| (\d\.\d\.\d+) [^|]+\| ([^|]+?) \| (.*) \|$", line)
        if match:
            assert match.group(1) not in found, f"{match.group(1)} appears twice"
            found[match.group(1)] = (match.group(2), match.group(3))
    return found


A = rows(section("## 5. WCAG 2.2 Level A", "## 6."))
AA = rows(section("## 6. WCAG 2.2 Level AA", "## 7."))
ALL = {**A, **AA}


def test_every_level_a_and_aa_criterion_is_there_once():
    assert sorted(A, key=lambda c: tuple(map(int, c.split(".")))) == sorted(
        LEVEL_A, key=lambda c: tuple(map(int, c.split(".")))
    )
    assert set(AA) == set(LEVEL_AA)


def test_every_row_has_one_of_the_four_levels():
    for criterion, (level, _) in ALL.items():
        assert level in LEVELS, (criterion, level)


def test_a_supports_row_names_a_test_or_the_code_it_rests_on():
    for criterion, (level, evidence) in ALL.items():
        if level != "Supports":
            continue
        cites = re.findall(r"`([^`]+)`", evidence)
        assert any(
            re.search(r"test_|tests/|\.html|\.css|\.js|web/|base\.html", c) for c in cites
        ) or re.search(r"[Bb]y construction", evidence), (
            f"{criterion} says Supports with no evidence"
        )


def test_every_test_and_file_a_row_names_exists():
    tests_text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("*.py"))
    for cite in set(re.findall(r"`([^`]+)`", ACR)):
        if re.fullmatch(r"test_\w+", cite):
            assert f"def {cite}(" in tests_text, f"no test named {cite}"
        elif re.fullmatch(r"tests/[\w/.]+\.py", cite):
            assert (ROOT / cite).is_file(), cite
        elif re.fullmatch(r"(test_\w+\.py)", cite):
            assert (ROOT / "tests" / cite).is_file(), cite
        elif re.fullmatch(r"[\w/]+\.html", cite) and "/" not in cite:
            assert list((ROOT / "src/readablestem/web/templates").glob(cite)), cite


def test_the_screen_reader_rows_wait_for_the_session():
    for criterion in NEEDS_VOICEOVER:
        level, evidence = ALL[criterion]
        assert level == "Not yet evaluated", (criterion, level)
        assert "VoiceOver" in evidence, criterion


def test_a_row_that_is_not_yet_evaluated_says_why():
    for criterion, (level, evidence) in ALL.items():
        if level == "Not yet evaluated":
            assert "VoiceOver" in evidence or "Not reviewed" in evidence, criterion


def test_the_counts_at_the_end_add_up():
    for name, table in (("Level A", A), ("Level AA", AA)):
        counts = {level: sum(1 for lv, _ in table.values() if lv == level) for level in LEVELS}
        line = re.search(rf"{name}, (\d+) rows: (.*?)\.\n", ACR + "\n", flags=re.S)
        assert line, name
        text = " ".join(line.group(0).split())
        assert f"{name}, {len(table)} rows" in text
        for level, number in counts.items():
            assert f"{level} {number}" in text, (name, level, number, text)


def test_nothing_claims_conformance():
    lowered = ACR.lower()
    for phrase in (
        "fully conform",
        "is wcag",
        "wcag compliant",
        "meets wcag",
        "complies with wcag",
        "is accessible to",
        "certified",
        "guarantee that",
    ):
        for match in re.finditer(re.escape(phrase), lowered):
            before = lowered[max(0, match.start() - 40) : match.start()]
            assert "not " in before or "no " in before or "nothing" in before, phrase
    assert "not a statement that" in lowered and "draft" in lowered
