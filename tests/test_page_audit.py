"""The page audit (`pipeline/page_audit.py`) and its shadow run (`readablestem eval
page-audit`): what a request carries, when a page is a pass, flagged or not audited, the
spend limits, and a replay. Offline: a stand-in answers for the model."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf
import pytest

from readablestem.cli.main import main
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import DocumentResult, DocumentStats, Element, Kind, PageType
from readablestem.eval.answers import CachedClient
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import page_audit
from readablestem.pipeline.page_audit import PageVerdict

PRINTED = {
    0: "2 Motion. A body keeps its velocity unless a force acts on it.",
    1: "The second page says that momentum is conserved.",
    2: "The third page is about energy.",
}


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def words_of(page: int) -> list[str]:
    return re.findall(r"[a-z0-9]+", PRINTED.get(page, "").lower())


def picture(page: int) -> bytes:
    return f"the picture of page {page + 1}".encode()


def element(element_id: str, kind: Kind, order: int, text: str) -> Element:
    page = int(element_id.split("-")[0][1:]) - 1  # ids count pages from 1
    return Element(id=element_id, kind=kind, page=page, bbox=(0, 0, 10, 10), order=order, text=text)


def document(
    *elements: Element, kinds: tuple[str, ...] = ("text", "text", "text")
) -> DocumentResult:
    return DocumentResult(
        pipeline_version="test",
        elements=list(elements),
        stats=DocumentStats(pages=len(kinds), cost_usd=0, latency_ms=0, model_calls=0),
        page_sizes={page: (612.0, 792.0) for page in range(len(kinds))},
        page_types=[PageType(page=page, kind=kind) for page, kind in enumerate(kinds)],
    )


DOC = document(
    element("p1-e0", Kind.HEADING, 0, "2 Motion"),
    element("p1-e1", Kind.PARAGRAPH, 1, "A body keeps its velocity unless a force acts on it."),
    element("p2-e0", Kind.PARAGRAPH, 2, "The second page says that momentum is conserved."),
    element("p3-e0", Kind.PARAGRAPH, 3, "The third page is about energy."),
)


def finding(severity: str = "high", **kw) -> dict:
    return {
        "code": "page_order",
        "severity": severity,
        "elements": ["p1-e1"],
        "box": {"left": 0.1, "top": 0.2, "right": 0.9, "bottom": 0.5},
        "text_fault": "none",
    } | kw


def answering(*findings: dict, **kw) -> FakeVisionClient:
    return FakeVisionClient(defaults={"page_audit": {"findings": list(findings)}}, **kw)


def audit_first_page(client: FakeVisionClient, doc: DocumentResult = DOC, **kw) -> PageVerdict:
    verdict, _call = page_audit.audit_page(client, settings(**kw), doc, 0, picture, words_of)
    return verdict


# ------------------------------------------------------------------------------ the request


def test_a_request_holds_one_page_its_picture_and_its_listing_and_no_other_page():
    client = answering()
    verdict = audit_first_page(client)
    (request,) = client.requests
    assert (request.step, request.model, request.effort) == (
        "page_audit",
        "claude-sonnet-5-5",
        "low",
    )
    assert request.images == [picture(0)]
    shown = request.context_text or ""
    assert "p1-e1 (paragraph): A body keeps its velocity" in shown
    assert "momentum" not in shown and "energy" not in shown  # the other pages
    assert "velocity" not in request.instructions  # document text is data, never instructions
    assert verdict.verdict == "pass" and verdict.by == "model"
    assert verdict.key and verdict.model == "claude-sonnet-5-5"  # tied to the input it judged


def test_the_picture_of_the_output_is_sent_second_when_there_is_one():
    client = answering()
    page_audit.audit_page(
        client, settings(), DOC, 0, picture, words_of, lambda page: b"the output as drawn"
    )
    assert client.requests[0].images == [picture(0), b"the output as drawn"]


# ----------------------------------------------------------------------------- the verdict


@pytest.mark.parametrize(
    ("findings", "verdict"),
    [
        ([], "pass"),
        ([finding("low")], "pass"),  # a blemish a listener gets past
        ([finding("low"), finding("high", code="page_missing", elements=[])], "flagged"),
    ],
)
def test_a_page_is_flagged_exactly_when_a_finding_is_of_high_severity(findings, verdict):
    got = audit_first_page(answering(*findings))
    assert got.verdict == verdict and got.reason is None
    assert [f.code for f in got.findings] == [f["code"] for f in findings]


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (FakeVisionClient(), "refused"),
        (answering(finding(code="page_looks_fine")), "invalid_output"),
        (answering(finding(elements=["p2-e0"])), "unknown_element"),  # an id of another page
        (answering(finding(box={"left": 0.4, "top": 0.2, "right": 1.7, "bottom": 0.5})), "bad_box"),
        # (what a batch request that kept failing is recorded and replayed as)
        (answering(fail_models={"claude-sonnet-5-5": "api_error"}), "api_error"),
    ],
)
def test_an_answer_that_cannot_be_used_is_not_audited_and_never_a_pass(client, reason):
    got = audit_first_page(client)
    assert (got.verdict, got.reason, got.findings) == ("not_audited", reason, [])


def test_a_page_with_ink_and_no_elements_is_flagged_without_asking():
    client = answering()
    doc = document(DOC.elements[2], kinds=("text", "text", "blank"))  # only page 2 has elements
    done = page_audit.audit(client, settings(), doc, picture, words_of)
    assert [(v.verdict, v.by) for v in done.verdicts] == [
        ("flagged", "code"),  # printed, and nothing of it is in the output
        ("pass", "model"),
        ("pass", "code"),  # a blank page has nothing to lose
    ]
    assert [f.code for f in done.verdicts[0].findings] == ["page_missing"]
    assert len(client.requests) == 1


# ------------------------------------------------------------------------------ the limits


@pytest.mark.parametrize(
    ("doc", "kw", "reason"),
    [
        (DOC, {"page_audit_max_usd_per_page": 0.001}, "over_page_ceiling"),
        (
            document(*(element(f"p1-e{n}", Kind.PARAGRAPH, n, "word " * 290) for n in range(30))),
            {},
            "listing_too_long",  # cut, it would show the model a page with text missing
        ),
    ],
)
def test_a_page_that_may_not_be_sent_is_not_audited_and_costs_nothing(doc, kw, reason):
    client = answering()
    got = audit_first_page(client, doc, **kw)
    assert (got.verdict, got.by, got.reason) == ("not_audited", "code", reason)
    assert client.requests == []


def test_a_run_over_its_cap_stops_and_says_which_pages_it_did_not_audit():
    budget = JobBudget(InMemorySpendGuard(cap_usd=10.0), max_usd=10.0, max_calls=1)
    client = answering(budget=budget)
    done = page_audit.audit(client, settings(), DOC, picture, words_of)
    assert done.stopped == "job_calls"
    assert [(v.verdict, v.reason) for v in done.verdicts] == [
        ("pass", None),
        ("not_audited", "cap_reached"),
        ("not_audited", "cap_reached"),
    ]
    assert len(client.requests) == 1  # nothing is asked after the cap


def test_a_replay_of_the_recorded_answers_gives_the_same_verdicts_and_sends_nothing(
    tmp_path: Path,
):
    answers = tmp_path / "answers.jsonl"
    live = CachedClient(answering(finding()), answers, settings())
    recorded = page_audit.audit(live, settings(), DOC, picture, words_of)

    replay = CachedClient(None, answers, settings())  # no model behind it
    again = page_audit.audit(replay, settings(), DOC, picture, words_of)

    assert again.verdicts == recorded.verdicts
    assert [v.verdict for v in again.verdicts] == ["flagged", "not_audited", "not_audited"]
    assert (replay.hits, replay.misses) == (3, 0)


# ----------------------------------------------------------------------------- the command


def test_the_shadow_run_writes_a_verdict_for_every_page_and_no_text_of_the_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    files = tmp_path / "set" / "files"
    files.mkdir(parents=True)
    with pymupdf.open() as made:
        for page in (0, 1):
            made.new_page().insert_text((72, 100), PRINTED[page], fontsize=12)
        made.save(files / "notes.pdf")
    out = tmp_path / "report.json"

    code = main(
        ["eval", "page-audit", "--gold-dir", str(files.parent), "--fake", "--out", str(out)]
    )

    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["shadow"] is True and report["pages"] == 2
    # (the offline stand-in answers nothing, which is never a pass)
    assert report["verdicts"] == {"not_audited": 2}
    assert [(row["document"], row["page"]) for row in report["rows"]] == [
        ("notes", 0),
        ("notes", 1),
    ]
    assert "velocity" not in out.read_text(encoding="utf-8")
    assert "velocity" not in capsys.readouterr().out
