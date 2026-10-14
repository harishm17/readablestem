"""Proof sets and their pins: what is accepted, what stops a run, what is only reported."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from proof_support import SET_ID, make_project
from readablestem.proof import registry
from readablestem.proof.errors import PinMismatch, ProofError

SHA = "a" * 64


def raw_set(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "id": "x",
        "title": "A set",
        "held_out": False,
        "gold": {
            "dir": "data/gold",
            "manifest_sha256": SHA,
            "fetch": {
                "date": "2026-10-06",
                "sections": [
                    {"book": "physics", "page": "p-1"},
                    {"book": "physics", "page": "p-2"},
                ],
            },
        },
        "bundle": "proof/bundles/x",
    }
    data.update(overrides)
    return data


def test_a_minimal_set_gets_the_defaults():
    pset = registry.parse_set(raw_set())
    assert pset.tasks == registry.TASKS
    assert pset.repeats == {"document": 1, "equations": 1, "headings": 1, "figures": 1}
    assert pset.concurrency == 4 and pset.signoff == () and pset.fetch.pins == {}
    assert pset.runs()[0] == ("document", 1) and len(pset.runs()) == 4
    assert [s.id for s in pset.sections] == ["physics/p-1", "physics/p-2"]


def test_repeats_expand_to_runs_in_task_order():
    pset = registry.parse_set(raw_set(repeats={"document": 3, "equations": 2}))
    assert pset.runs() == [
        ("document", 1),
        ("document", 2),
        ("document", 3),
        ("equations", 1),
        ("equations", 2),
        ("headings", 1),
        ("figures", 1),
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"id": 7}, "wrong type"),
        ({"title": None}, "wrong type"),
        ({"held_out": "yes"}, "wrong type"),
        ({"held_out": True}, "must pin its preregistration"),
        ({"tasks": ["document", "tables"]}, "tasks must come from"),
        ({"tasks": []}, "tasks must come from"),
        ({"repeats": {"document": 0}}, "repeats must be 1 or more"),
        ({"preregistration": {"file": "p.md", "sha256": "nothex"}}, "SHA-256"),
        ({"bundle": None}, "wrong type"),
    ],
)
def test_a_malformed_set_is_refused_with_a_reason(change, message):
    with pytest.raises(ProofError, match=message):
        registry.parse_set(raw_set(**change))


def test_sections_must_exist_and_be_unique():
    for sections, message in (([], "no sections"), ([{"book": "b", "page": "p"}] * 2, "twice")):
        data = copy.deepcopy(raw_set())
        data["gold"]["fetch"]["sections"] = sections
        with pytest.raises(ProofError, match=message):
            registry.parse_set(data)


def test_a_boolean_does_not_pass_for_a_string():
    data = raw_set()
    data["gold"]["manifest_sha256"] = True
    with pytest.raises(ProofError, match="wrong type"):
        registry.parse_set(data)


def test_archive_pins_are_read_per_book():
    data = copy.deepcopy(raw_set())
    data["gold"]["fetch"]["pins"] = {"physics": {"archive_version": "2026", "version": "abc"}}
    assert registry.parse_set(data).fetch.pins == {"physics": ("2026", "abc")}
    data["gold"]["fetch"]["pins"] = {"physics": "abc"}
    with pytest.raises(ProofError, match="must be an object"):
        registry.parse_set(data)
    data["gold"]["fetch"]["pins"] = {"physics": {"version": "abc"}}
    with pytest.raises(ProofError, match="archive_version"):
        registry.parse_set(data)


def test_a_held_out_set_carries_its_preregistration_pin():
    pset = registry.parse_set(
        raw_set(held_out=True, preregistration={"file": "proof/PREREG.md", "sha256": SHA})
    )
    assert pset.held_out and pset.prereg is not None and pset.prereg.sha256 == SHA


def test_the_registry_lists_sets_and_skips_the_index(tmp_path):
    root = make_project(tmp_path)
    assert [s.id for s in registry.list_sets(root)] == [SET_ID]
    assert registry.default_set_id(root) == SET_ID
    assert registry.resolve_set(root, None).id == SET_ID
    assert registry.resolve_set(root, SET_ID).id == SET_ID
    with pytest.raises(ProofError, match=r"no proof set 'nope' \(known: synthetic\)"):
        registry.resolve_set(root, "nope")


def test_no_sets_folder_means_no_default(tmp_path):
    assert registry.list_sets(tmp_path) == []
    assert registry.default_set_id(tmp_path) is None
    with pytest.raises(ProofError, match="no proof set: add one"):
        registry.resolve_set(tmp_path, None)


def test_a_broken_index_or_set_file_is_a_clear_error(tmp_path):
    root = make_project(tmp_path)
    (root / "proof" / "sets" / "index.json").write_text("{not json")
    assert registry.default_set_id(root) is None
    (root / "proof" / "sets" / "bad.json").write_text("[1]")
    with pytest.raises(ProofError, match="expected a JSON object"):
        registry.list_sets(root)
    (root / "proof" / "sets" / "bad.json").write_text("{nope")
    with pytest.raises(ProofError, match=r"cannot read proof set bad\.json"):
        registry.list_sets(root)


def statuses(checks) -> dict[str, str]:
    return {c.name: c.status for c in checks}


def test_a_matching_gold_set_passes_every_pin(tmp_path):
    root = make_project(tmp_path)
    pset = registry.resolve_set(root, None)
    checks = registry.check_gold(root / "data" / "gold", pset)
    assert statuses(checks) == {
        "gold manifest": "ok",
        "gold files": "ok",
        "gold sections": "ok",
    }
    registry.require_ok(checks)


def test_a_missing_gold_manifest_is_absent_not_a_mismatch(tmp_path):
    root = make_project(tmp_path)
    pset = registry.resolve_set(root, None)
    checks = registry.check_gold(tmp_path / "nowhere", pset)
    assert statuses(checks) == {"gold manifest": "absent"}
    registry.require_ok(checks)  # absent is reported, not fatal


def test_a_changed_manifest_a_changed_file_and_an_extra_file_are_each_found(tmp_path):
    root = make_project(tmp_path)
    pset = registry.resolve_set(root, None)
    gold = root / "data" / "gold"
    page = gold / "openstax" / "physics" / "p-1.jsonl"
    original = page.read_text()

    page.write_text(original + "\n")
    checks = registry.check_gold(gold, pset)
    assert statuses(checks)["gold files"] == "mismatch"
    with pytest.raises(PinMismatch, match="changed or missing"):
        registry.require_ok(checks)
    page.write_text(original)

    (gold / "openstax" / "physics" / "extra.jsonl").write_text("{}\n")
    assert "not in the manifest" in next(
        c.detail for c in registry.check_gold(gold, pset) if c.name == "gold files"
    )
    (gold / "openstax" / "physics" / "extra.jsonl").unlink()

    manifest = gold / "manifest.json"
    manifest.write_text(manifest.read_text() + " ")
    only = registry.check_gold(gold, pset)
    assert statuses(only) == {"gold manifest": "mismatch"}  # nothing else is trusted


def test_a_gold_set_for_other_sections_is_a_mismatch(tmp_path):
    root = make_project(tmp_path)
    data = json.loads((root / "proof" / "sets" / f"{SET_ID}.json").read_text())
    data["gold"]["fetch"]["sections"][1]["page"] = "p-9"
    pset = registry.parse_set(data)
    assert (
        statuses(registry.check_gold(root / "data" / "gold", pset))["gold sections"] == "mismatch"
    )


def test_the_preregistration_copy_is_checked_when_present(tmp_path):
    root = make_project(tmp_path, held_out=True, prereg=b"registered")
    pset = registry.resolve_set(root, None)
    check = registry.check_prereg(pset, root)
    assert check is not None and check.status == "ok"
    (root / "proof" / "PREREGISTRATION.md").write_bytes(b"edited")
    check = registry.check_prereg(pset, root)
    assert check is not None and check.status == "mismatch"
    (root / "proof" / "PREREGISTRATION.md").unlink()
    check = registry.check_prereg(pset, root)
    assert check is not None and check.status == "absent" and "no copy at" in check.detail


def test_a_development_set_has_no_preregistration_to_check(tmp_path):
    root = make_project(tmp_path)
    assert registry.check_prereg(registry.resolve_set(root, None), root) is None


def test_sha256_file_streams_a_file(tmp_path: Path):
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    assert registry.sha256_file(path) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
