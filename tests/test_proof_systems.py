"""Other systems in the proof: the chatbot recorder, the scorer of their outputs, adding them
to a recorded run, and the wording of a claim."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from a11y_stem.proof import chatbot, stats, systems, systems_tables
from a11y_stem.proof.bundle import Bundle, BundleWriter
from a11y_stem.proof.errors import BundleError

# ----------------------------------------------------------------------- chatbot recorder


def test_html_from_takes_the_largest_fenced_block_or_what_follows_an_open_fence():
    reply = "intro\n```html\n<p>a</p>\n```\ntext\n```html\n<html><p>longer one</p></html>\n```\n"
    assert chatbot.html_from(reply) == "<html><p>longer one</p></html>\n"
    assert chatbot.html_from("x\n```html\n<p>cut off") == "<p>cut off"
    assert chatbot.html_from("no fence <!DOCTYPE html><p>x</p>").startswith("<!DOCTYPE")
    assert chatbot.html_from("just words") == "just words"


class FakeResponses:
    def __init__(self, text: str, status: str = "completed", fail: list[str] | None = None):
        self.text = text
        self.status = status
        self.calls = 0
        self.fail = fail or []
        self.input_tokens = SimpleNamespace(count=lambda **_: SimpleNamespace(input_tokens=1000))

    def create(self, **kwargs):
        assert kwargs["store"] is False and kwargs["max_output_tokens"] == 64_000
        self.calls += 1
        if self.fail:
            name = self.fail.pop(0)
            raise type(name, (Exception,), {})("x")
        usage = SimpleNamespace(
            input_tokens=1000,
            output_tokens=2000,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        )
        return SimpleNamespace(
            output_text=self.text,
            status=self.status,
            usage=usage,
            model=kwargs["model"],
            incomplete_details=None,
        )


def _pdf(path: Path, text: str = "A short test page about velocity.") -> Path:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), text)
        doc.save(path)
    return path


@pytest.fixture
def one_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(chatbot, "page_pngs", lambda _p: [b"png"])
    return {"book/page": _pdf(tmp_path / "book__page.pdf")}


def _client(**kwargs):
    return SimpleNamespace(responses=FakeResponses(**kwargs))


def test_a_reply_is_recorded_with_its_cost_and_status_and_not_asked_twice(one_pdf, tmp_path):
    client = _client(text="```html\n<h2>Speed</h2>\n```")
    out = tmp_path / "out"
    arm = chatbot.ARM_BY_ID["chatbot-luna-purdue"]
    summary = chatbot.record_arms(
        one_pdf, out, arms=[arm], max_usd=5, client=client, say=lambda _m: None
    )
    assert summary["refused_by_cap"] == [] and client.responses.calls == 2  # two runs
    meta = json.loads((out / arm.id / "book__page.run1.json").read_text())
    assert meta["status"] == "ok" and meta["output"] == {
        "file": "book__page.run1.html",
        "format": "html",
    }
    assert meta["cost_usd"] == pytest.approx((1000 * 0.10 + 2000 * 0.50) / 1e6)
    assert meta["settings"]["store"] is False and meta["pdf_pages"] == 1
    assert (out / arm.id / "book__page.run1.html").read_text() == "<h2>Speed</h2>\n"
    chatbot.record_arms(one_pdf, out, arms=[arm], max_usd=5, client=client, say=lambda _m: None)
    assert client.responses.calls == 2, "a recorded run is not paid for again"


@pytest.mark.parametrize(
    ("text", "status_in", "expected"),
    [
        ("", "completed", "empty"),
        ("```html\n<p>x</p>", "incomplete", "truncated"),
        ("<p>x</p>\nCONTINUED IN NEXT REPLY", "completed", "truncated"),
    ],
)
def test_a_cut_off_or_empty_reply_is_a_failure_of_the_section(
    one_pdf, tmp_path, text, status_in, expected
):
    client = _client(text=text, status=status_in)
    arm = chatbot.ARM_BY_ID["chatbot-sol-expert"]
    chatbot.record_arms(one_pdf, tmp_path / "o", arms=[arm], max_usd=5, client=client)
    meta = json.loads((tmp_path / "o" / arm.id / "book__page.json").read_text())
    assert meta["status"] == expected


def test_an_infrastructure_error_is_retried_twice_and_other_errors_are_not(one_pdf, tmp_path):
    arm = chatbot.ARM_BY_ID["chatbot-sol-expert"]
    client = _client(text="<p>x</p>", fail=["InternalServerError", "APITimeoutError"])
    chatbot.record_arms(
        one_pdf, tmp_path / "a", arms=[arm], max_usd=5, client=client, sleep=lambda _s: None
    )
    meta = json.loads((tmp_path / "a" / arm.id / "book__page.json").read_text())
    assert meta["status"] == "ok" and meta["attempts"] == ["InternalServerError", "APITimeoutError"]
    client = _client(text="<p>x</p>", fail=["BadRequestError"])
    chatbot.record_arms(
        one_pdf, tmp_path / "b", arms=[arm], max_usd=5, client=client, sleep=lambda _s: None
    )
    meta = json.loads((tmp_path / "b" / arm.id / "book__page.json").read_text())
    assert meta["status"] == "crashed" and meta["error"] == "BadRequestError"


def test_a_run_the_cap_cannot_hold_is_refused_before_any_call(one_pdf, tmp_path):
    client = _client(text="<p>x</p>")
    arm = chatbot.ARM_BY_ID["chatbot-sol-purdue"]  # worst case about $0.64 per call
    summary = chatbot.record_arms(
        one_pdf, tmp_path / "o", arms=[arm], max_usd=0.5, client=client, say=lambda _m: None
    )
    assert client.responses.calls == 0 and len(summary["refused_by_cap"]) == 2
    assert not (tmp_path / "o" / arm.id / "book__page.run1.json").exists()


# ------------------------------------------------------------------------------ scoring

GOLD_EQ = {
    "id": "openstax/book/page/eq-000",
    "kind": "equation",
    "book_slug": "book",
    "page_slug": "page",
    "index_on_page": 0,
    "display": "block",
    "mathml": "<math><mi>v</mi><mo>=</mo><mn>5</mn></math>",
}
GOLD_H = {
    "id": "openstax/book/page/h-000",
    "kind": "heading",
    "book_slug": "book",
    "page_slug": "page",
    "index_on_page": 0,
    "level": 2,
    "text": "Speed",
    "context": None,
}


def _write_system(root: Path, html: str, status: str = "ok") -> systems.SystemRun:
    arm = root / "chatbot-test"
    arm.mkdir(parents=True)
    (arm / "book__page.html").write_text(html, encoding="utf-8")
    meta = {
        "arm": "chatbot-test",
        "section": "book/page",
        "status": status,
        "output": {"file": "book__page.html", "format": "html"},
    }
    (arm / "book__page.json").write_text(json.dumps(meta), encoding="utf-8")
    return systems.load_arms_dir(root)["chatbot-test"]["book/page"][0]


def test_a_system_output_is_scored_by_the_document_task_code_and_the_shared_reader(tmp_path):
    pdf = _pdf(tmp_path / "p.pdf", "Speed. The speed is v = 5 metres per second today.")
    html = "<html><body><h2>Speed</h2><p>The speed is $v = 5$ metres per second today.</p>"
    html += "<p>$$v = 5$$</p></body></html>"
    run = _write_system(tmp_path / "s", html)
    gold = systems.section_golds([GOLD_EQ, GOLD_H], ["book/page"])["book/page"]
    score = systems.score_section(run, gold, pdf.read_bytes(), [GOLD_EQ["mathml"]], reference=None)
    assert score.heading["matched"] == 1 and score.heading["n_gold"] == 1
    assert score.equation["n_gold"] == 1 and score.equation["matched"] == 1
    assert score.equation["pairs"][0]["exact_lenient"] is True
    assert score.invented is not None and score.invented["counts"]["number"]["n"] >= 1
    hit, total = score.coverage
    assert 0 < hit <= total


def test_a_section_the_system_did_not_convert_scores_as_found_nothing(tmp_path):
    pdf = _pdf(tmp_path / "p.pdf")
    run = _write_system(tmp_path / "s", "<p>x</p>", status="truncated")
    gold = systems.section_golds([GOLD_EQ, GOLD_H], ["book/page"])["book/page"]
    score = systems.score_section(run, gold, pdf.read_bytes(), [GOLD_EQ["mathml"]])
    assert score.invented is None
    assert score.equation["n_gold"] == 1 and score.equation["matched"] == 0
    assert score.heading["n_gold"] == 1 and score.heading["matched"] == 0
    assert score.coverage[0] == 0


# ----------------------------------------------------------------------- bundle and claims


def _bundle(tmp_path: Path) -> Bundle:
    writer = BundleWriter(tmp_path / "b")
    writer.write_text("answers/x.jsonl", "{}\n")
    return writer.finish({"set_id": "s", "recorded_on": "2026-10-13"})


def test_systems_can_be_added_to_a_finished_bundle_once(tmp_path):
    bundle = _bundle(tmp_path)
    source = tmp_path / "src"
    (source / "arm").mkdir(parents=True)
    (source / "arm" / "a.json").write_text("{}")
    new = bundle.extend(source, "systems", extra={"systems_spend": {"chatbot_usd": 1.5}})
    assert not new.verify() and "systems/arm/a.json" in new.files
    assert new.manifest["systems_spend"] == {"chatbot_usd": 1.5}
    with pytest.raises(BundleError, match="already in the recorded run"):
        new.extend(source, "systems")


def test_a_claim_needs_the_corrected_interval_to_exclude_zero():
    spec = systems_tables.METRICS["e2e"]
    wide = stats.Interval(-0.1, 0.4, 0, 15, 0.99)
    better = stats.Interval(0.1, 0.4, 0, 15, 0.99)
    worse = stats.Interval(-0.4, -0.1, 0, 15, 0.99)
    assert systems_tables.verdict(spec, wide, "x", "a") == "no difference this set can detect"
    assert systems_tables.verdict(spec, better, "x", "a") == "a is better"
    assert systems_tables.verdict(spec, worse, "x", "a") == "x is better"
    lower = systems_tables.METRICS["unflagged"]  # lower is better
    assert systems_tables.verdict(lower, better, "x", "a") == "x is better"
    assert systems_tables.verdict(lower, worse, "x", "a") == "a is better"
    empty = stats.Interval(None, None, 0, 0, 0.99)
    assert systems_tables.verdict(spec, empty, "x", "a") == "not computed"


def test_a_section_missing_on_one_side_is_a_section_of_zeros_not_a_gap():
    a = {"inv_sentence": {"s1": (1.0, 10.0), "s2": (0.0, 10.0), "s3": (2.0, 10.0)}}
    b = {"inv_sentence": {"s1": (5.0, 10.0), "s2": (6.0, 10.0)}}  # s3 failed: no units
    done = systems_tables.compare("inv_sentence", "x", a, b, a_name="a", other="b", k=5, n_boot=200)
    assert done.result != "not computed"
    assert done.a == pytest.approx(3 / 30) and done.b == pytest.approx(11 / 20)


def _vectors(rates_a, rates_b, key="inv_sentence"):
    a = {key: {f"s{i:02d}": (r * 10.0, 10.0) for i, r in enumerate(rates_a)}}
    b = {key: {f"s{i:02d}": (r * 10.0, 10.0) for i, r in enumerate(rates_b)}}
    return a, b


def test_no_verdict_is_written_for_fewer_sections_than_the_floor():
    a, b = _vectors([0.9, 0.8, 0.9], [0.1, 0.0, 0.2], key="e2e")
    done = systems_tables.compare("e2e", "x", a, b, a_name="a", other="b", k=6, n_boot=200)
    assert done.result == "not tested, 3 sections"
    assert done.n_sections == 3 and done.difference is not None  # the numbers are still shown
    # A caller may lower the floor (a test of the machinery, not a claim).
    low = systems_tables.compare(
        "e2e", "x", a, b, a_name="a", other="b", k=6, n_boot=200, min_sections=3
    )
    assert low.result.startswith("a is")


def test_a_steady_gain_over_enough_sections_is_better_by_both_tests():
    a, b = _vectors([0.9] * 15, [0.1] * 15, key="e2e")
    done = systems_tables.compare("e2e", "x", a, b, a_name="a", other="b", k=8, n_boot=200)
    assert done.result == "a is better"
    assert done.p_sign_flip is not None and done.p_sign_flip < 0.05 / 8


def test_a_bootstrap_verdict_the_sign_flip_test_does_not_support_is_only_higher():
    base = "a is better"
    text = systems_tables.final_result(base, p=0.0073, k=8, n_sections=15)
    assert text.startswith("a is higher by point estimate")
    assert "p = 0.0073" in text and "better" not in text
    assert systems_tables.final_result(base, p=0.001, k=8, n_sections=15) == base
    assert "p = n/a" in systems_tables.final_result(base, p=None, k=8, n_sections=15)
    nothing = "no difference this set can detect"
    assert systems_tables.final_result(nothing, p=0.5, k=8, n_sections=15) == nothing
    assert systems_tables.final_result(base, p=0.0, k=8, n_sections=9) == "not tested, 9 sections"


def test_the_claims_table_prints_the_sign_flip_p_and_says_what_it_is():
    a, b = _vectors([0.9] * 15, [0.1] * 15, key="e2e")
    done = systems_tables.compare("e2e", "x", a, b, a_name="a", other="b", k=8, n_boot=200)
    table = systems_tables.claims_table("t", [done], a_name="a", k=8)
    text = table.markdown()
    assert "Sign-flip p" in text and "robustness check" in text and "fewer than 10 sections" in text
