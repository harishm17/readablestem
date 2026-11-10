"""Proof sets and their pins: what is accepted, what stops a run, what is only reported."""

from __future__ import annotations

import copy
import json

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


def statuses(checks) -> dict[str, str]:
    return {c.name: c.status for c in checks}


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
