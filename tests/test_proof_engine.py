"""The proof command end to end, offline: record a live run (fake model, real layout and
scoring code, authored PDFs), then replay it and check what the replay promises."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
from pathlib import Path

import pytest

from proof_support import SET_ID, PdfMedia, fake_client, make_project
from readablestem.core.types import Check
from readablestem.pipeline import html_checks, run
from readablestem.proof import engine, registry, runner
from readablestem.proof.bundle import MANIFEST_NAME, Bundle
from readablestem.proof.errors import (
    BundleError,
    Drift,
    Incomplete,
    PinMismatch,
    ProofError,
    ReplayMiss,
)
from readablestem.proof.report import wording_problems

DATE = "2026-10-14"
N_BOOT = 200  # the intervals are tested in test_proof_stats; fewer draws keep this quick


class Recorded:
    def __init__(self, root: Path, bundle: Path, text: str, work: Path) -> None:
        self.root, self.bundle, self.text, self.work = root, bundle, text, work


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> Recorded:
    root = make_project(tmp_path_factory.mktemp("project"))
    work = tmp_path_factory.mktemp("work")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(html_checks, "axe_checks", lambda html: [Check(name="axe", passed=True)])
        result = engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=5.0,
                date=DATE,
                work_dir=work,
                client_factory=fake_client,
                media_factory=PdfMedia,
                out=root / "PROOF.md",
                n_boot=N_BOOT,
            ),
            say=lambda _msg: None,
        )
    assert result.exit_code == 0
    return Recorded(root, work / "bundles" / f"{SET_ID}-{DATE}", result.text, work)


def options(rec: Recorded, **overrides) -> engine.ProofOptions:
    base = {
        "root": rec.root,
        "work_dir": rec.work,
        "bundle_dir": rec.bundle,
        "out": rec.root / "replay.md",
        "n_boot": N_BOOT,
    }
    return engine.ProofOptions(**(base | overrides))


@pytest.fixture(scope="module")
def replayed(recorded) -> engine.ProofResult:
    """One replay with the key gone and the network refused: the default, offline path."""

    def refuse(*_a, **_k):
        raise AssertionError("the replay opened a network connection")

    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("OPENAI_API_KEY", raising=False)
        patch.setattr(socket.socket, "connect", refuse)
        return engine.run_proof(
            options(recorded, out=recorded.root / "first.md", require_complete=True),
            say=lambda _m: None,
        )


def copy_bundle(rec: Recorded, tmp_path: Path) -> Path:
    target = tmp_path / "bundle"
    shutil.copytree(rec.bundle, target)
    return target


def rehash(bundle_dir: Path) -> None:
    """Re-list the bundle's files in its manifest, as if the edit had been recorded."""
    path = bundle_dir / MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest["files"] = {
        p.relative_to(bundle_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(bundle_dir.rglob("*"))
        if p.is_file() and p.name != MANIFEST_NAME
    }
    path.write_text(json.dumps(manifest))


# --------------------------------------------------------------------- what a run makes


def test_a_live_run_writes_a_bundle_and_a_report_that_matches_its_replay(recorded):
    bundle = Bundle.open(recorded.bundle)
    assert bundle.verify() == []
    assert bundle.set_id == SET_ID and bundle.recorded_on == DATE
    assert {(r["task"], r["repeat"]) for r in bundle.runs} == {
        ("document", 1),
        ("document", 2),
        ("equations", 1),
        ("headings", 1),
        ("figures", 1),
    }
    assert [e["section"] for e in bundle.pdfs] == ["physics/p-1", "physics/p-2"]
    assert "Replay compared with the recorded reports: **identical**" in recorded.text
    assert "5 report(s)" in recorded.text


def test_the_report_has_the_tables_and_says_what_is_not_there(recorded):
    text = recorded.text
    for expected in (
        "## Status",
        "## The 2 sections",
        "## Results: a11y-stem",
        "end_to_end_exact_lenient (all gold equations)",
        "heading F1",
        "judge_pass_rate",
        "Required decisions per PDF page",
        "## Attribution",
    ):
        assert expected in text, expected
    assert "**Not done.**" in text  # the human labels
    assert "> **Incomplete.**" in text
    assert "Development set, not held out" in text  # held_out is false in the test set
    assert wording_problems(text) == []


def test_two_repeats_are_pooled_and_each_run_is_shown(recorded):
    row = next(
        line
        for line in recorded.text.splitlines()
        if line.startswith("| **end_to_end_exact_lenient (all gold equations)**")
    )
    assert row.rstrip(" |").split("|")[-1].count(" / ") == 1  # "run 1 / run 2"


# ----------------------------------------------------------------------- what a replay is


def test_a_replay_needs_no_key_no_network_and_calls_no_model(replayed):
    assert replayed.drift == []
    assert "0 had no recorded answer" in replayed.text
    assert "**identical**" in replayed.text


def test_require_complete_fails_while_a_part_is_a_stub(replayed):
    assert replayed.exit_code == 1 and len(replayed.not_wired) == 3


def test_a_replay_writes_the_same_bytes_every_time_and_to_proof_md_by_default(recorded, replayed):
    again = engine.run_proof(options(recorded, out=None), say=lambda _m: None)
    assert again.out == recorded.root / "PROOF.md"
    assert again.text == replayed.text
    assert (recorded.root / "first.md").read_bytes() == again.out.read_bytes()
    # The live run's own file differs from the replay's only in how the status words the mode.
    assert (
        recorded.text.replace(
            "live run that was recorded and then replayed to write this file",
            "replay of a recorded live run: no model call, no key, no cost",
        )
        == again.text
    )


def test_a_changed_bundle_file_is_refused_before_anything_runs(recorded, tmp_path):
    bundle = copy_bundle(recorded, tmp_path)
    pdf = next((bundle / "media" / "pdfs").glob("*.pdf"))
    pdf.write_bytes(pdf.read_bytes() + b"\n%x")
    with pytest.raises(BundleError, match="changed: media/pdfs"):
        engine.run_proof(options(recorded, bundle_dir=bundle), say=lambda _m: None)


def test_a_file_added_to_the_bundle_is_refused(recorded, tmp_path):
    bundle = copy_bundle(recorded, tmp_path)
    (bundle / "extra.txt").write_text("x")
    with pytest.raises(BundleError, match=r"not listed: extra\.txt"):
        engine.run_proof(options(recorded, bundle_dir=bundle), say=lambda _m: None)


def test_a_bundle_for_another_set_is_refused(recorded, tmp_path):
    bundle = copy_bundle(recorded, tmp_path)
    manifest = json.loads((bundle / MANIFEST_NAME).read_text())
    manifest["set_id"] = "other"
    (bundle / MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="recorded for 'other'"):
        engine.run_proof(options(recorded, bundle_dir=bundle), say=lambda _m: None)


def test_a_gold_set_that_is_not_the_pinned_one_stops_the_run(recorded, tmp_path):
    root = tmp_path / "root"
    shutil.copytree(recorded.root, root)
    gold = root / "data" / "gold" / "openstax" / "physics" / "p-1.jsonl"
    gold.write_text(gold.read_text() + "\n")
    with pytest.raises(PinMismatch):
        engine.run_proof(
            engine.ProofOptions(
                root=root, bundle_dir=recorded.bundle, work_dir=recorded.work, out=root / "x.md"
            ),
            say=lambda _m: None,
        )


def test_another_pipeline_version_is_drift_not_a_silent_replay(recorded, monkeypatch):
    monkeypatch.setattr(run, "PIPELINE_VERSION", "99.0.0")
    with pytest.raises(Drift, match="pipeline version"):
        engine.run_proof(options(recorded), say=lambda _m: None)
    result = engine.run_proof(options(recorded, allow_drift=True), say=lambda _m: None)
    assert "this checkout differs from the code that made the recording" in result.text.lower()
    assert "> **DRIFT.**" in result.text


def test_a_setting_that_changes_the_run_is_drift(recorded, monkeypatch):
    monkeypatch.setenv("AUTO_ACCEPT_CONFIDENCE", "0.5")
    with pytest.raises(Drift, match="auto_accept_confidence"):
        engine.run_proof(options(recorded), say=lambda _m: None)


def test_a_missing_answer_stops_the_replay_and_names_the_run(recorded, tmp_path):
    bundle = copy_bundle(recorded, tmp_path)
    answers = bundle / "answers" / "document.run1.jsonl"
    rows = [r for r in answers.read_text().splitlines() if r.strip()]
    answers.write_text("\n".join(r for r in rows if '"t":"model"' not in r) + "\n")
    rehash(bundle)
    with pytest.raises(ReplayMiss, match=r"document\.run1"):
        engine.run_proof(options(recorded, bundle_dir=bundle), say=lambda _m: None)


def test_numbers_that_no_longer_match_the_recording_are_found(recorded, tmp_path):
    bundle_dir = copy_bundle(recorded, tmp_path)
    bundle = Bundle.open(bundle_dir)
    arm = runner.ArmResults(
        [
            runner.RunOutcome(r["task"], r["repeat"], bundle.report(r["task"], r["repeat"]), [])
            for r in bundle.runs
        ]
    )
    assert runner.compare_with_recorded(bundle, arm) == []  # a report equals itself
    path = bundle_dir / "reports" / "headings.run1.json"
    report = json.loads(path.read_text())
    report["metrics"]["precision"] = 0.123
    report["cost"]["model_calls"] += 1
    report["latency_ms"] = {"p50": 12345}  # timing is never compared
    path.write_text(json.dumps(report))
    problems = runner.compare_with_recorded(Bundle.open(bundle_dir), arm)
    assert len(problems) == 2
    assert any("metrics.precision" in p for p in problems)
    assert any("cost.model_calls" in p for p in problems)


def test_check_reports_pins_and_hashes_without_running(recorded):
    never = recorded.root / "never-written.md"
    checks = engine.check_proof(options(recorded, out=never))
    by_name = {c.name: c.status for c in checks}
    assert by_name["recorded run"] == "ok" and by_name["gold manifest"] == "ok"
    assert not never.exists()


# ----------------------------------------------------------------- live-run rules


def test_a_live_run_without_a_cap_is_refused(tmp_path):
    root = make_project(tmp_path)
    with pytest.raises(ProofError, match="--max-usd"):
        engine.run_proof(
            engine.ProofOptions(root=root, live=True, date=DATE, work_dir=tmp_path / "w"),
            say=lambda _m: None,
        )


def test_a_held_out_set_refuses_the_fake_client(tmp_path):
    root = make_project(tmp_path, held_out=True, prereg=b"registered text")
    with pytest.raises(ProofError, match="not allowed on a held-out set"):
        engine.run_proof(
            engine.ProofOptions(
                root=root, live=True, fake=True, date=DATE, work_dir=tmp_path / "w"
            ),
            say=lambda _m: None,
        )


def test_a_held_out_set_needs_the_signoff_to_be_confirmed(tmp_path, monkeypatch):
    root = make_project(tmp_path, held_out=True, prereg=b"registered text")
    monkeypatch.setattr(runner, "git_state", lambda _root: {"commit": "abc", "dirty": False})
    with pytest.raises(ProofError, match="--confirm-signoff"):
        engine.run_proof(
            engine.ProofOptions(
                root=root, live=True, max_usd=1.0, date=DATE, work_dir=tmp_path / "w"
            ),
            say=lambda _m: None,
        )


def test_a_held_out_run_from_a_dirty_checkout_is_refused(tmp_path, monkeypatch):
    root = make_project(tmp_path, held_out=True, prereg=b"registered text")
    monkeypatch.setattr(runner, "git_state", lambda _root: {"commit": "abc", "dirty": True})
    with pytest.raises(ProofError, match="uncommitted changes"):
        engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=1.0,
                confirm_signoff=True,
                date=DATE,
                work_dir=tmp_path / "w",
            ),
            say=lambda _m: None,
        )


def test_a_held_out_live_run_needs_the_registration_file_to_be_present(tmp_path, monkeypatch):
    root = make_project(tmp_path, held_out=True, prereg=b"registered text")
    (root / "proof" / "PREREGISTRATION.md").unlink()
    monkeypatch.setattr(runner, "git_state", lambda _root: {"commit": "abc", "dirty": False})
    with pytest.raises(ProofError, match="needs the registration file: no copy at"):
        engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=1.0,
                confirm_signoff=True,
                date=DATE,
                work_dir=tmp_path / "w",
            ),
            say=lambda _m: None,
        )


def test_a_changed_preregistration_stops_a_held_out_run(tmp_path, monkeypatch):
    root = make_project(tmp_path, held_out=True, prereg=b"registered text")
    (root / "proof" / "PREREGISTRATION.md").write_bytes(b"edited afterwards")
    monkeypatch.setattr(runner, "git_state", lambda _root: {"commit": "abc", "dirty": False})
    with pytest.raises(PinMismatch, match="preregistration"):
        engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=1.0,
                confirm_signoff=True,
                date=DATE,
                work_dir=tmp_path / "w",
            ),
            say=lambda _m: None,
        )


def test_a_spend_cap_that_is_too_small_stops_the_run_and_leaves_no_bundle(tmp_path, monkeypatch):
    root = make_project(tmp_path)
    monkeypatch.setattr(html_checks, "axe_checks", lambda html: [Check(name="axe", passed=True)])
    with pytest.raises(Incomplete):
        engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=0.0005,
                date=DATE,
                work_dir=tmp_path / "w",
                client_factory=fake_client,
                media_factory=PdfMedia,
            ),
            say=lambda _m: None,
        )
    assert not (tmp_path / "w" / "bundles" / f"{SET_ID}-{DATE}").exists()


def test_the_registry_finds_the_set_and_its_default(recorded):
    pset = registry.resolve_set(recorded.root, None)
    assert pset.id == SET_ID and [s.id for s in pset.sections] == ["physics/p-1", "physics/p-2"]
    assert pset.runs()[:2] == [("document", 1), ("document", 2)]


# ------------------------------------------------------------- stages made elsewhere


class DemoStage:
    """What another work package's module registers: a stage that returns a section."""

    name = "invented_content"
    title = "Invented-content rate"

    def __init__(self, text: str = "Invented: 0 of 10 units (demo).") -> None:
        self.text = text
        self.recorded = False

    def record(self, ctx, writer) -> None:
        self.recorded = True

    def run(self, ctx):
        from readablestem.proof.report import Section, StageResult

        return StageResult("ok", [Section(self.title, [self.text])])


def test_a_registered_stage_replaces_its_stub_in_the_same_place(recorded, monkeypatch):
    from readablestem.proof import stages

    monkeypatch.setitem(stages.STAGES, "invented_content", DemoStage())
    result = engine.run_proof(options(recorded, out=recorded.root / "demo.md"), say=lambda _m: None)
    text = result.text
    assert "Invented: 0 of 10 units (demo)." in text
    assert "`invented_content` is a placeholder" not in text
    assert len(result.not_wired) == 3
    order = [text.index(h) for h in ("## Results: a11y-stem", "## Invented-content rate")]
    assert order == sorted(order)
    assert "| Invented-content rate | done |" in text
    assert "Not computed in this build: Comparison" in text  # the other three still say so


def test_a_stage_that_writes_a_forbidden_word_stops_the_report(recorded, monkeypatch):
    from readablestem.proof import stages

    monkeypatch.setitem(stages.STAGES, "invented_content", DemoStage("The output is compliant."))
    with pytest.raises(ProofError, match="wording rules"):
        engine.run_proof(options(recorded, out=recorded.root / "bad.md"), say=lambda _m: None)
    assert not (recorded.root / "bad.md").exists()


def test_a_live_run_calls_every_stage_record_hook(tmp_path, monkeypatch):
    from readablestem.proof import stages

    root = make_project(tmp_path)
    demo = DemoStage()
    monkeypatch.setitem(stages.STAGES, "invented_content", demo)
    monkeypatch.setattr(html_checks, "axe_checks", lambda html: [Check(name="axe", passed=True)])
    engine.run_proof(
        engine.ProofOptions(
            root=root,
            live=True,
            max_usd=5.0,
            date=DATE,
            work_dir=tmp_path / "w",
            client_factory=fake_client,
            media_factory=PdfMedia,
            n_boot=N_BOOT,
        ),
        say=lambda _m: None,
    )
    assert demo.recorded


# ------------------------------------------------------- a cap stop that keeps finished runs


def _client_that_runs_out_after(finished_runs: int):
    from readablestem.core.budget import BudgetExceeded

    made = {"n": 0}

    def factory(settings, budget):
        made["n"] += 1
        inner = fake_client(settings, budget)
        if made["n"] <= finished_runs:
            return inner

        class OutOfMoney:
            def __getattr__(self, name):
                return getattr(inner, name)

            def run(self, *_a, **_k):
                raise BudgetExceeded("daily", "spend cap reached")

        return OutOfMoney()

    return factory


def _live(root, work, **overrides):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(html_checks, "axe_checks", lambda html: [Check(name="axe", passed=True)])
        return engine.run_proof(
            engine.ProofOptions(
                root=root, live=True, max_usd=5.0, date=DATE, work_dir=work,
                media_factory=PdfMedia, out=root / "PROOF.md", n_boot=N_BOOT, **overrides,
            ),
            say=lambda _msg: None,
        )  # fmt: skip


def test_a_cap_stop_writes_no_bundle_by_default(tmp_path):
    root = make_project(tmp_path)
    with pytest.raises(Incomplete):
        _live(root, tmp_path / "w", client_factory=_client_that_runs_out_after(1))


def test_a_cap_stop_can_keep_the_runs_that_finished_and_says_so(tmp_path):
    root = make_project(tmp_path)
    result = _live(
        root, tmp_path / "w", client_factory=_client_that_runs_out_after(1), keep_finished=True
    )
    bundle = Bundle.open(tmp_path / "w" / "bundles" / f"{SET_ID}-{DATE}")
    assert bundle.verify() == []
    assert [(r["task"], r["repeat"]) for r in bundle.runs] == [("document", 1)]
    assert bundle.manifest["incomplete"]["runs_planned"] == 5
    assert "INCOMPLETE RUN" in result.text and "1 of 5 planned runs" in result.text
    assert "Replay compared with the recorded reports: **identical**" in result.text


def test_an_interrupted_recording_resumes_without_paying_for_finished_runs(tmp_path):
    root = make_project(tmp_path)
    work = tmp_path / "w"
    with pytest.raises(Incomplete):
        _live(root, work, client_factory=_client_that_runs_out_after(2))
    partial = work / "bundles" / f"{SET_ID}-{DATE}.partial"
    assert (partial / "runs" / "document.run1.json").is_file()
    asked = []

    def counting(settings, budget):
        asked.append(1)
        return fake_client(settings, budget)

    result = _live(root, work, client_factory=counting, resume=True)
    bundle = Bundle.open(work / "bundles" / f"{SET_ID}-{DATE}")
    assert bundle.verify() == []
    assert bundle.manifest["resumed_runs"] == ["document.run1", "document.run2"]
    assert len(asked) == 3  # equations, headings, figures: the two finished runs are not made again
    assert "interrupted once and resumed" in result.text
    assert "Replay compared with the recorded reports: **identical**" in result.text


# ------------------------------------------------------------------ adding other systems


def _fake_chat_records(arm_ids, refused=()):
    """A stand-in for `chatbot.record_arms` that writes finished outputs for `arm_ids`."""

    def record(pdfs, out, *, arms, max_usd, client, say=print, **_kw):
        for arm in arm_ids:
            for section in pdfs:
                stem = section.replace("/", "__")
                folder = out / arm
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{stem}.html").write_text("<h2>Worked example</h2>", encoding="utf-8")
                meta = {
                    "arm": arm,
                    "section": section,
                    "status": "ok",
                    "output": {"file": f"{stem}.html", "format": "html"},
                    "pdf_pages": 1,
                    "cost_usd": 0.01,
                }
                (folder / f"{stem}.json").write_text(json.dumps(meta), encoding="utf-8")
        return {"spent_usd": 0.04, "cap_usd": max_usd, "refused_by_cap": list(refused)}

    return record


def test_chatbot_arms_that_finished_whole_are_added_and_a_cap_stopped_arm_is_not(
    recorded, tmp_path, monkeypatch
):
    from readablestem.core.budget import InMemorySpendGuard, JobBudget

    record = _fake_chat_records(
        ["chatbot-sol-purdue", "chatbot-sol-expert"], refused=["chatbot-sol-expert/x.run1"]
    )
    from readablestem.eval import render
    from readablestem.proof import chatbot

    monkeypatch.setattr(chatbot, "record_arms", record)
    monkeypatch.setattr(render.ImageCache, "png", lambda self, url: b"not a picture")
    bundle_dir = copy_bundle(recorded, tmp_path)
    budget = JobBudget(InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    extra = engine.add_systems(
        options(recorded, bundle_dir=bundle_dir, work_dir=tmp_path / "w", max_usd=3.0, date=DATE),
        "chatbot",
        say=lambda _m: None,
        chat_client=object(),
        judge_client=fake_client(runner.proof_settings(), budget),
    )
    bundle = Bundle.open(bundle_dir)
    assert bundle.verify() == []
    arms = sorted(p.name for p in bundle.systems_dir.iterdir())
    assert arms == ["chatbot-sol-purdue"]
    assert extra["systems_not_run"] == {"chatbot-sol-expert": "the spend cap did not hold it"}
    again = options(recorded, bundle_dir=bundle_dir, work_dir=tmp_path / "w", max_usd=3.0)
    with pytest.raises(ProofError, match="already holds the chatbot arms"):
        engine.add_systems(again, "chatbot", say=lambda _m: None, chat_client=object())


def test_a_primary_chatbot_arm_the_cap_cannot_hold_adds_nothing(recorded, tmp_path, monkeypatch):
    from readablestem.proof import chatbot

    monkeypatch.setattr(
        chatbot,
        "record_arms",
        _fake_chat_records(["chatbot-sol-purdue"], refused=["chatbot-sol-purdue/x.run2"]),
    )
    bundle_dir = copy_bundle(recorded, tmp_path)
    with pytest.raises(ProofError, match="did not hold the primary arm"):
        engine.add_systems(
            options(recorded, bundle_dir=bundle_dir, work_dir=tmp_path / "w", max_usd=3.0),
            "chatbot",
            say=lambda _m: None,
            chat_client=object(),
        )
    assert not (bundle_dir / "systems").exists()


def test_claude_arms_are_added_one_after_the_other_with_the_runs_that_were_made(
    recorded, tmp_path, monkeypatch
):
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.eval import render
    from readablestem.proof import chatbot

    asked = []

    def record(pdfs, out, *, arms, max_usd, client, runs=None, say=print, **_kw):
        asked.append(([a.id for a in arms], runs))
        return _fake_chat_records([a.id for a in arms])(
            pdfs, out, arms=arms, max_usd=max_usd, client=client
        )

    monkeypatch.setattr(chatbot, "record_arms", record)
    monkeypatch.setattr(chatbot, "make_client", lambda provider="openai": object())
    monkeypatch.setattr(render.ImageCache, "png", lambda self, url: b"not a picture")
    bundle_dir = copy_bundle(recorded, tmp_path)
    budget = JobBudget(InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    settings = runner.proof_settings("anthropic")
    common = {
        "bundle_dir": bundle_dir,
        "work_dir": tmp_path / "w",
        "provider": "anthropic",
        "probe": lambda: None,
        "date": DATE,
    }
    first = engine.add_systems(
        options(recorded, max_usd=3.0, chat_runs=1, **common),
        "chatbot",
        say=lambda _m: None,
        arms=["chatbot-opus-purdue"],
        judge_client=fake_client(settings, budget),
    )
    assert asked == [(["chatbot-opus-purdue"], 1)]
    assert first["systems_runs"] == {"chatbot-opus-purdue": 1}
    assert first["systems_judge"] == {"provider": "anthropic", "model": "claude-opus-5-5"}
    second = engine.add_systems(
        options(recorded, max_usd=2.0, **common),
        "chatbot",
        say=lambda _m: None,
        arms=["chatbot-haiku-purdue"],
        judge_client=fake_client(settings, budget),
    )
    assert "systems_runs" not in second
    bundle = Bundle.open(bundle_dir)
    assert bundle.verify() == []
    assert sorted(p.name for p in bundle.systems_dir.iterdir()) == [
        "chatbot-haiku-purdue",
        "chatbot-opus-purdue",
    ]
    assert bundle.manifest["systems_runs"] == {"chatbot-opus-purdue": 1}
    assert bundle.manifest["systems_spend"]["cap_usd"] == 5.0  # both caps, added
    with pytest.raises(ProofError, match="already holds the chatbot arms"):
        engine.add_systems(
            options(recorded, max_usd=1.0, **common),
            "chatbot",
            say=lambda _m: None,
            arms=["chatbot-opus-purdue"],
        )


def test_claude_arms_are_not_run_for_the_openai_provider(recorded, tmp_path):
    bundle_dir = copy_bundle(recorded, tmp_path)
    with pytest.raises(ProofError, match="run on anthropic, not on openai: pass --provider"):
        engine.add_systems(
            options(recorded, bundle_dir=bundle_dir, work_dir=tmp_path / "w", max_usd=3.0),
            "chatbot",
            say=lambda _m: None,
            arms=["chatbot-opus-purdue"],
            chat_client=object(),
        )
    assert not (bundle_dir / "systems").exists()


def test_sections_recorded_together_replay_to_the_same_report(recorded, tmp_path):
    """A live run may convert several sections at once (`--section-workers`). Its answers
    and reports are those of one after the other, a replay of it passes the comparison with
    what was recorded, and it records no section times (they would include the waiting)."""
    root = make_project(tmp_path / "project")
    work = tmp_path / "work"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(html_checks, "axe_checks", lambda html: [Check(name="axe", passed=True)])
        together = engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=5.0,
                date=DATE,
                work_dir=work,
                client_factory=fake_client,
                media_factory=PdfMedia,
                out=root / "PROOF.md",
                n_boot=N_BOOT,
                section_workers=2,
            ),
            say=lambda _msg: None,
        )
    assert together.exit_code == 0
    bundle = work / "bundles" / f"{SET_ID}-{DATE}"

    def reports(directory: Path) -> dict[str, dict]:
        return {
            p.name: runner.comparable(json.loads(p.read_text()))
            for p in sorted((directory / "reports").glob("*.json"))
        }

    assert reports(bundle) == reports(recorded.bundle)
    runs = [json.loads(p.read_text()) for p in sorted((bundle / "runs").glob("document*.json"))]
    assert runs and all(run["timing"] == [] for run in runs)
    again = engine.run_proof(
        engine.ProofOptions(
            root=root,
            work_dir=work,
            bundle_dir=bundle,
            out=root / "replay-together.md",
            n_boot=N_BOOT,
        ),
        say=lambda _m: None,
    )
    assert again.exit_code == 0
